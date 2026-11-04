#!/usr/bin/env python3
"""Persistent per-project narrative continuation point.

Only successful Catch Up imports and completed, qualifying creative-writing
responses advance this state. Dump Info is deliberately excluded.

source_excerpt is verbatim. summaries are extracted model output and must be
treated as potentially imperfect. No original chapter is edited.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def location(project):
    # World/character facts are shared by folder, but the continuation point
    # belongs to the PROJECT ALIAS. Several projects may reference one folder.
    alias = str(project.get("alias", ""))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", alias):
        raise ValueError("Invalid Storyteller project alias")
    return (Path(project["folder"]).expanduser().resolve()
            / ".storyteller" / "project-state" / alias)


def current(project):
    path = location(project) / "story-state.json"
    if not path.is_file() or path.is_symlink():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def excerpt_from_text(text, max_characters=1500):
    """Use the actual ending of prose as the continuity anchor."""
    text = text.strip()
    if len(text) <= max_characters:
        return text
    tail = text[-max_characters:]
    # Avoid starting in the middle of a sentence when a nearby boundary exists.
    match = re.search(r"(?<=[.!?])\s+(?=[A-Z\"“])", tail[:min(340, len(tail))])
    if match and len(tail[match.end():]) >= 800:
        tail = tail[match.end():]
    return tail.strip()


def is_narrative_exchange(user_prompt, scene):
    """Conservatively classify generated narrative for automatic progression.

    We avoid advancing for Q&A, reference dumps, summaries, outlines, and
    generic assistant text. Classification is necessarily heuristic because
    Jan's OpenAI-compatible API does not expose a trusted "canon" flag.
    """
    if not isinstance(scene, str) or len(scene.strip()) < 120:
        return False
    if not isinstance(user_prompt, str):
        return False
    prompt = user_prompt.strip().lower()
    if not prompt:
        return False
    # A bare reference to the "story" does not establish a new scene.
    # Factual questions and reference descriptions should not move the timeline.
    explicit_action = bool(re.search(
        r"\b(write|draft|compose|narrate|continue|rewrite|expand)\b"
        r".{0,75}\b(scene|chapter|story|fiction|prose|dialogue|narrative|paragraphs?)\b",
        prompt))
    continuation = bool(re.search(
        r"\b(continue (?:from|the story|the scene)|rewrite|expand|"
        r"write this out|write it out|narrate)\b", prompt))
    non_narrative = bool(re.search(
        r"\b(summary|summarize|outline|profile|data dump|catalogue|"
        r"list facts|explain|analy[sz]e|brainstorm|plot ideas|"
        r"character sheet|describe the character)\b", prompt))
    if non_narrative:
        return False
    if (re.match(r"^(what|who|which|why|where|how|is|are|does|did|can|"
                 r"could|would|should)\b", prompt)
            and not explicit_action):
        return False
    if "?" in prompt and not explicit_action and not continuation:
        return False
    return explicit_action or continuation


def build_catchup_state(project, jobid, docs, summaries=()):
    """Assemble a continuation point from the chronologically LAST input.

    Source order is the same natural filename/chunk order used during import.
    Earlier chapters are canonical history but not the current time position.
    """
    if not docs or not docs[-1].get("text", "").strip():
        raise ValueError("Catch Up has no completed source text")
    last = docs[-1]
    end = last["text"].strip()
    summaries = list(summaries)
    # Find the last section's model synopsis, when available.
    latest_summary = str(summaries[-1][2]).strip() if summaries else ""
    return {
        "version": 1,
        "source_type": "catchup",
        "source_id": jobid,
        "project_alias": project["alias"],
        "position": "End of imported material",
        "source_file": str(last["path"]),
        "source_section": len(last.get("chunks", [])),
        "source_excerpt": excerpt_from_text(end),
        "scene_summary": latest_summary[:750],
    }


def build_scene_state(project, exchange_id, scene, summary=""):
    if not scene.strip():
        raise ValueError("Cannot advance to an empty scene")
    return {
        "version": 1,
        "source_type": "generated_scene",
        "source_id": exchange_id,
        "project_alias": project["alias"],
        "position": "After the last completed generated scene",
        "source_file": "",
        "source_section": None,
        "source_excerpt": excerpt_from_text(scene),
        "scene_summary": str(summary).strip()[:750],
    }


def markdown(data):
    source_type = data.get("source_type", "unknown")
    loc = data.get("source_file") or "Jan / completed generated scene"
    position = str(data.get("position", ""))
    scene_summary = str(data.get("scene_summary", "")).strip()
    excerpt = str(data.get("source_excerpt", "")).strip()
    excerpt = excerpt.replace("<!--", "< !--")
    lines = [
        "# Current story position",
        "",
        "**This is the narrative continuation point, not the newest fact in the encyclopedia.**",
        f"**Position:** {position}",
        f"**Source:** {loc}",
        f"**Type:** {source_type}",
        "",
    ]
    if scene_summary:
        lines.extend([
            "## Interpretation of the final scene (AI-generated; verify accuracy)",
            scene_summary, "",
        ])
    lines.extend([
        "## Verbatim final story excerpt",
        "The next scene starts AFTER this excerpt. Do not repeat it unless asked.",
        "",
        excerpt, "",
    ])
    return "\n".join(lines)


@contextmanager
def write_lock(folder):
    """Serialize state writes between GUI, import worker, and Jan gateway."""
    folder.mkdir(parents=True, exist_ok=True)
    lockfile = folder / ".story-state.lock"
    with open(lockfile, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _atomic(path, data):
    temp = path.with_name(path.name + ".tmp." + str(os.getpid()))
    try:
        with open(temp, "w", encoding="utf-8") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def advance(project, state):
    """Commit an explicit narrative progression; return True if changed."""
    if state.get("source_type") not in ("catchup", "generated_scene"):
        raise ValueError("Dump Info cannot advance the story")
    if state.get("project_alias") != project.get("alias"):
        raise ValueError("Project alias mismatch")
    if not state.get("source_excerpt", "").strip():
        raise ValueError("Missing story ending")
    base = location(project)
    with write_lock(base):
        old = current(project)
        if old and old.get("source_type") == state["source_type"] and old.get("source_id") == state["source_id"]:
            return False
        # Previous state stays available for inspection and recovery.
        journal = base / "story-state-history.jsonl"
        state = {**state, "updated_at": datetime.now(timezone.utc).isoformat()}
        _atomic(base / "story-state.md", markdown(state))
        _atomic(base / "story-state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        with open(journal, "a", encoding="utf-8") as file:
            file.write(json.dumps(state, ensure_ascii=False) + "\n")
        return True
