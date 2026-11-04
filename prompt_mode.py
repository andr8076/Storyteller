#!/usr/bin/env python3
"""One-shot, project-scoped capture of the next Jan user prompt.

Jan prompt text is persisted as UTF-8 under the project's private .storyteller
directory, then handed to the existing resumable importer. Nothing is sent to
the writing model while an import mode is armed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

VALID_MODES = {"catchup", "dump"}
MIN_CHARS = 20
MAX_CHARS = 32 * 1024 * 1024
RETRY_WINDOW = 180


def now():
    return datetime.now(timezone.utc).isoformat()


def root(project):
    alias = str(project.get("alias", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", alias):
        raise ValueError("Invalid project alias")
    return (Path(project["folder"]).expanduser().resolve()
            / ".storyteller" / "project-state" / alias)


@contextmanager
def locked(project):
    folder = root(project)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "prompt-mode.lock", "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield folder
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path, data):
    temp = path.with_name(path.name + ".tmp." + str(os.getpid()) +
                          "." + secrets.token_hex(4))
    try:
        with open(temp, "w", encoding="utf-8") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_state(project):
    file = root(project) / "next-prompt.json"
    if not file.is_file() or file.is_symlink():
        return {"armed": None, "last_capture": None}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {"armed": None, "last_capture": None}


def arm(project, mode):
    if mode not in VALID_MODES:
        raise ValueError("Expected catchup or dump mode")
    with locked(project) as folder:
        state = read_state(project)
        state["armed"] = {"mode": mode, "at": now(), "token": secrets.token_hex(12)}
        atomic_write(folder / "next-prompt.json", json.dumps(state, indent=2) + "\n")
        return state["armed"]


def cancel(project):
    with locked(project) as folder:
        state = read_state(project)
        was_armed = state.get("armed")
        state["armed"] = None
        atomic_write(folder / "next-prompt.json", json.dumps(state, indent=2) + "\n")
        return bool(was_armed)


def task_path(project, token):
    if not re.fullmatch(r"[a-f0-9]{24}", token):
        raise ValueError("Invalid task token")
    return root(project) / "prompt-imports" / (token + ".json")


def read_task(path):
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


def recent_task(project):
    folder = root(project) / "prompt-imports"
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        task = read_task(path)
        if task.get("project_alias") == project["alias"]:
            task["task_path"] = str(path)
            return task
    return None


def imported_prompt_hashes(project):
    """Known Jan prompt imports for filtering model conversation history."""
    folder = root(project) / "prompt-imports"
    result = set()
    if folder.is_dir():
        for path in folder.glob("*.json"):
            entry = read_task(path)
            if entry.get("project_alias") == project["alias"] and entry.get("prompt_hash"):
                result.add(entry["prompt_hash"])
    return result


def pending_tasks(project):
    folder = root(project) / "prompt-imports"
    if not folder.is_dir():
        return []
    jobs = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.name):
        task = read_task(path)
        if (task.get("project_alias") == project["alias"] and
                task.get("status") in ("queued", "running")):
            jobs.append((path, task))
    return jobs


def update_task(path, **values):
    task = read_task(path)
    if not task:
        raise ValueError("Task does not exist")
    task.update(values, updated_at=now())
    atomic_write(path, json.dumps(task, ensure_ascii=False, indent=2) + "\n")
    return task


def request_pause(project, token):
    path = task_path(project, token)
    task = read_task(path)
    if task.get("project_alias") != project["alias"]:
        raise ValueError("This prompt import does not belong to the selected project")
    if task.get("status") not in ("running", "queued"):
        raise ValueError("Only queued/running imports can be paused")
    atomic_write(path.with_suffix(".pause"), now() + "\n")
    if task["status"] == "queued":
        return update_task(path, status="paused")
    return task


def retry_task(project, token):
    path = task_path(project, token)
    task = read_task(path)
    if task.get("project_alias") != project["alias"]:
        raise ValueError("Task not found for this project")
    if task.get("status") not in ("paused", "error"):
        raise ValueError("Only paused or failed imports can resume")
    if not Path(task["source"]).is_file():
        raise ValueError("Saved prompt input is missing")
    path.with_suffix(".pause").unlink(missing_ok=True)
    return update_task(path, status="queued", error=None)


def capture(project, text):
    """Consume the armed mode ONLY after safely persisting the user's text.

    Returns None in ordinary Jan mode; returns {error:...} while still armed
    on bad input; otherwise returns the captured task. A retry with exactly
    the same input is acknowledged without forwarding it to the writer.
    """
    if not isinstance(text, str):
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with locked(project) as folder:
        state = read_state(project)
        armed = state.get("armed")
        if not armed:
            last = state.get("last_capture") or {}
            if (last.get("hash") == digest and
                    time.time() - float(last.get("time", 0)) < RETRY_WINDOW):
                task = read_task(task_path(project, last["token"]))
                if task:
                    return {**task, "duplicate": True}
            return None
        if len(text.strip()) < MIN_CHARS:
            return {"error": f"Paste at least {MIN_CHARS} characters of story or lore; "
                             "the import mode is still armed."}
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_CHARS:
            return {"error": "Prompt exceeds the 32 MiB import limit. "
                             "Use the file-import option instead; mode is still armed."}
        mode = armed["mode"]
        token = armed["token"]
        input_dir = (Path(project["folder"]).expanduser().resolve()
                     / ".storyteller" / "imports" / "inputs")
        input_dir.mkdir(parents=True, exist_ok=True)
        source = input_dir / ("jan-" + mode + "-" + token + ".md")
        atomic_write(source, text)
        jobs = folder / "prompt-imports"
        jobs.mkdir(parents=True, exist_ok=True)
        task = {
            "token": token, "project_alias": project["alias"], "mode": mode,
            "source": str(source), "status": "queued", "created_at": now(),
            "updated_at": now(), "characters": len(text), "bytes": len(encoded),
            "prompt_hash": digest
        }
        atomic_write(jobs / (token + ".json"), json.dumps(task, indent=2) + "\n")
        state["armed"] = None
        state["last_capture"] = {"hash": digest, "token": token,
                                 "time": time.time()}
        atomic_write(folder / "next-prompt.json", json.dumps(state, indent=2) + "\n")
        return task
