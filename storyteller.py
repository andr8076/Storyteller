#!/usr/bin/env python3
"""Storyteller 0.7.1: project-scoped lore injection, recording, CPU memory extraction.

No daemon, plugins, Jan file editing, or extra Python packages required.
All traffic stays on 127.0.0.1. Run only when wanted.
"""
import argparse
import hashlib
import http.client
import json
import os
import re
import signal
import time
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from memory_worker import Worker
import story_state
import prompt_mode
import prompt_affixes
import memory_retrieval
import character_knowledge

ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("STORYTELLER_CONFIG", ROOT / "projects.json"))
LOCK = threading.Lock()
DEFAULT_MODEL = "Cydonia-24B-v4.3-heretic-v3.IQ4_XS.gguf"
STOPWORDS = {"the", "and", "but", "that", "this", "with", "from", "into", "would",
             "about", "could", "have", "what", "when", "where", "their", "they",
             "after", "before", "does", "just", "here", "there", "write", "scene"}
MEMORY_MAX = 12000


def load_config():
    if not CONFIG.exists():
        return {"version": 1, "projects": []}
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(config.get("projects"), list):
        raise ValueError("Invalid projects.json")
    return config


def save_config(config):
    temp = CONFIG.with_suffix(".tmp")
    temp.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(CONFIG)


def ensure_memory(folder):
    memory = folder / ".storyteller" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "characters").mkdir(exist_ok=True)
    (memory / "world").mkdir(exist_ok=True)
    return memory


def add_project(args):
    config = load_config()
    alias = args.alias
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", alias):
        raise SystemExit("Alias must be lowercase letters/numbers/hyphens, 2-64 characters.")
    if any(p["alias"] == alias for p in config["projects"]):
        raise SystemExit("Alias already registered. No files changed.")
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit("Folder must exist: " + str(folder))
    ensure_memory(folder)
    config["projects"].append({"alias": alias, "name": args.name,
                               "folder": str(folder), "model": args.model})
    save_config(config)
    print(f"Added {args.name}: {alias} -> {folder}")
    print("Put established facts in " + str(folder / ".storyteller/memory/canon.md"))
    print("Character files go in .storyteller/memory/characters/")


def terms(value):
    return {v for v in re.findall(r"[a-z0-9]{3,}", value.lower())
            if v not in STOPWORDS}


def text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") if isinstance(x.get("text"), str) else ""
                        for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def without_imported_history(project, messages):
    """Exclude imported Jan user text and acknowledgement pairs from the writer.

    The text is already archived by Catch Up/Dump Info. Sending it to the
    creative model again could exhaust its context and undo the benefit of
    segmented import. Jan's visible transcript is not modified.
    """
    hashes = prompt_mode.imported_prompt_hashes(project)
    if not hashes:
        return messages, 0
    cleaned = []
    skipped = 0
    index = 0
    while index < len(messages):
        item = messages[index]
        if (isinstance(item, dict) and item.get("role") == "user"
                and index + 1 < len(messages)):
            body = text_from_content(item.get("content"))
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            next_item = messages[index+1]
            ack = (text_from_content(next_item.get("content"))
                   if isinstance(next_item, dict) and next_item.get("role") == "assistant"
                   else "")
            if digest in hashes and (
                ack.startswith("Catch Up: captured") or
                ack.startswith("Dump Info: captured") or
                ack.startswith("That Catch Up message was already captured") or
                ack.startswith("That Dump Info message was already captured")
            ):
                index += 2
                skipped += 2
                continue
        cleaned.append(item)
        index += 1
    return cleaned, skipped


def select_memory(project, messages):
    root = Path(project["folder"]) / ".storyteller" / "memory"
    if not root.is_dir():
        return "", []
    latest = [text_from_content(m.get("content")) for m in messages[-6:]
              if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    current = terms(" ".join(latest))
    files = []
    for name in ("canon.md", "summary.md", "recent-events.md"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            files.append(path)
    scored = []
    profile_bodies = {}
    for sub in ("characters", "world"):
        folder = root / sub
        if not folder.is_dir():
            continue
        for path in folder.glob("*.md"):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                continue
            title = path.stem.replace("-", " ").replace("_", " ")
            try:
                body = path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            score = memory_retrieval.profile_score(title, body, current, terms)
            if score:
                scored.append((score, path))
                profile_bodies[path] = body
    scored.sort(key=lambda pair: (-pair[0], pair[1].name))
    # Raw excerpts from Catch Up and Dump Info retain details the 2B model
    # might miss. Retrieve only a few relevant excerpts, not the entire archive.
    reference_candidates = []
    notes = root / "reference-notes"
    if notes.is_dir() and current:
        for path in notes.glob("*/section-*.md"):
            if path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(root.resolve()):
                continue
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matched = current & terms(body)
            if matched:
                # Reward multiple distinct query words, not just repetitions.
                score = len(matched)
                reference_candidates.append((score, path))
    reference_candidates.sort(key=lambda v: (-v[0], str(v[1])))
    matched_profiles = [path for _, path in scored[:5]]
    matched_notes = [path for _, path in reference_candidates[:3]]
    files = [p for p in files if p.name != "recent-events.md"]
    files.extend(matched_profiles)
    files.extend(matched_notes)
    recent = root / "recent-events.md"
    if recent.is_file() and not recent.is_symlink():
        files.append(recent)
    state_file = story_state.location(project) / "story-state.md"
    ledger_file = root / "character-knowledge.sqlite3"
    if not files and not state_file.is_file() and not ledger_file.is_file():
        return "", []
    parts, selected = [], []
    remaining = MEMORY_MAX
    if state_file.is_file() and not state_file.is_symlink():
        text = state_file.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            snippet = text[:min(remaining, 3400)]
            parts.append("### CURRENT STORY POSITION (project-specific)\n" + snippet)
            selected.append("project-state/" + project["alias"] + "/story-state.md")
            remaining -= len(snippet)
    perspective = character_knowledge.relevant(project, " ".join(latest), terms,
                                                limit=min(remaining, 1700))
    if perspective:
        parts.append("### CHARACTER PERSPECTIVES (attributed; beliefs are not world facts)\n" + perspective)
        selected.append("memory/character-knowledge.sqlite3")
        remaining -= len(perspective)
    for path in files:
        body = profile_bodies.get(path)
        if body is None:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        if not body:
            continue
        # Reserve context for relevant character files and original source notes.
        relative = str(path.relative_to(root))
        if path.name == "story-state.md":
            limit = 3400
        elif path.name == "canon.md":
            limit = 3100
        elif path.name == "summary.md":
            limit = 2700
        elif path.name == "recent-events.md":
            limit = 1500
        elif relative.startswith("reference-notes/"):
            limit = 2300
        else:
            limit = 1900
        budget = min(remaining, limit)
        snippet = (memory_retrieval.profile_excerpt(body, current, terms, budget)
                   if path in profile_bodies else body[:budget])
        if not snippet:
            break
        parts.append(f"### {path.relative_to(root)}\n{snippet}")
        selected.append(relative)
        remaining -= len(snippet)
        if remaining <= 0:
            break
    if not parts:
        return "", []
    prompt = ("ESTABLISHED STORY REFERENCE (provided by the user's local Storyteller files).\n"
              "The CURRENT STORY POSITION is the authoritative place to CONTINUE FROM.\n"
              "Treat Dump Info as timeless facts, never as a reason to advance time.\n"
              "Do not restart or repeat the latest scene; continue after it.\n"
              "Use other files to preserve continuity. Do not treat dialogue or speculation as canon.\n"              "VERBATIM IMPORT EXCERPTS are source material; assess whether claims are real story facts,\n"              "not merely rumors, dialogue, hypothetical ideas or unreliable narration.\n"
              "If the user's new writing explicitly changes a fact, follow the new event.\n\n"
              + "\n\n".join(parts))
    return prompt, selected


def record_complete(project, messages, response, selected):
    if not response.strip():
        return
    last_user = next((text_from_content(m.get("content")) for m in reversed(messages)
                      if isinstance(m, dict) and m.get("role") == "user"), "")
    request_hash = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False)
                                  .encode("utf-8")).hexdigest()
    entry = {"utc": datetime.now(timezone.utc).isoformat(),
             "project": project["alias"], "request_hash": request_hash,
             "last_user_message": last_user, "assistant_response": response,
             "learn_response": project.get("learn_from_responses", True),
             "memory_files_used": selected, "status": "pending_analysis"}
    # One append per completed exchange, never modifying the original story.
    target = Path(project["folder"]) / ".storyteller" / "pending-exchanges.jsonl"
    with LOCK:
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    return entry


def record_user_message(project, messages):
    """Queue only the latest normal user prompt after retrieval has taken its snapshot."""
    last_user = next((text_from_content(m.get("content")) for m in reversed(messages)
                      if isinstance(m, dict) and m.get("role") == "user"), "")
    if not last_user.strip():
        return
    request_hash = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False)
                                  .encode("utf-8")).hexdigest()
    entry = {"project": project["alias"], "request_hash": request_hash,
             "source_kind": "user_prompt", "last_user_message": last_user}
    target = Path(project["folder"]) / ".storyteller" / "pending-exchanges.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    projects = {}
    upstream = None

    @staticmethod
    def get_projects():
        # Always read current project mapping; edits in the control panel apply
        # to new requests without reloading the writing or memory models.
        return {p["alias"]: p for p in load_config()["projects"]}

    def send_json(self, status, data):
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self):
        if self.path in ("/v1/models", "/models"):
            projects = self.get_projects()
            self.send_json(200, {"object": "list", "data": [
                {"id": key, "object": "model", "owned_by": "storyteller"} for key in projects]})
        elif self.path in ("/health", "/v1/health"):
            self.send_json(200, {"status": "ready", "projects": list(self.get_projects())})
        else:
            self.send_json(404, {"error": "Unknown endpoint"})

    def persist_completed_reply(self, project, original_messages, output, used_files):
        """Save the result and advance state before Jan sees completion."""
        try:
            record = record_complete(project, original_messages, output, used_files)
            if story_state.is_narrative_exchange(record["last_user_message"], output):
                source_id = hashlib.sha256(
                    (record["request_hash"] + "|" + output).encode("utf-8")).hexdigest()
                if story_state.advance(
                    project, story_state.build_scene_state(project, source_id, output)):
                    self.log("advanced continuation point after completed scene")
            self.log(f"queued completed exchange, chars={len(output)}")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.log(f"could not persist completed reply: {type(exc).__name__}: {exc}")

    def reply_to_import(self, project, task, streaming):
        mode = task["mode"]
        title = "Catch Up" if mode == "catchup" else "Dump Info"
        duplicate = bool(task.get("duplicate"))
        if duplicate:
            text = (f"That {title} message was already captured. "
                    "It will not be imported twice. Check the Storyteller terminal for progress.")
        else:
            text = (f"{title}: captured {task['characters']:,} characters for "
                    f"project '{project['name']}'.\\n\\n"
                    "The small CPU model will catalogue the information section by section "
                    "while Storyteller is open. No story was generated from this message.\\n"
                    + ("When the import completes, the next story scene will begin after "
                       "the end of the supplied material."
                       if mode == "catchup" else
                       "The current story position will remain unchanged.")
                    + "\\n\\nIn Storyteller, press J to check import progress.")
        text = text.replace("\\n", "\n")
        model_alias = project["alias"]
        identifier = "chatcmpl-storyteller-import-" + task["token"]
        if streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            frames = [
                {"id": identifier, "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model_alias,
                 "choices": [{"index": 0, "delta": {
                     "role": "assistant", "content": text}, "finish_reason": None}]},
                {"id": identifier, "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": model_alias,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            ]
            for event in frames:
                self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self.send_json(200, {
                "id": identifier, "object": "chat.completion",
                "created": int(time.time()), "model": model_alias,
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": text},
                    "finish_reason": "stop"}]})
        self.log(f"captured one-shot {mode} prompt for {model_alias}: "
                 f"{task['characters']} chars, duplicate={duplicate}")

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_json(404, {"error": "Unknown endpoint"})
            return
        started = False
        conn = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            # Allow large, explicitly armed imports. Jan may impose its own
            # smaller client-side input/context limit before reaching us.
            if length < 1 or length > 36_000_000:
                return self.send_json(413, {"error": "Request exceeds 36 MB limit"})
            data = json.loads(self.rfile.read(length))
            project = self.get_projects().get(data.get("model"))
            if not project:
                return self.send_json(400, {"error": "Unknown project/model alias"})
            messages = data.get("messages")
            if not isinstance(messages, list):
                return self.send_json(400, {"error": "Missing messages"})
            if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
                last_prompt = text_from_content(messages[-1].get("content"))
                captured = prompt_mode.capture(project, last_prompt)
                if captured is not None:
                    if captured.get("error"):
                        return self.send_json(400, {"error": captured["error"]})
                    return self.reply_to_import(project, captured,
                                                bool(data.get("stream", False)))
            elif prompt_mode.read_state(project).get("armed"):
                return self.send_json(400, {"error": "The armed mode is waiting for a user text message in Jan."})
            unmodified_messages = list(messages)
            messages, skipped = without_imported_history(project, messages)
            if skipped:
                self.log(f"removed {skipped} archived import-history messages "
                         "before forwarding to writing model")
            injection, used_files = select_memory(project, messages)
            # Affixes are part of the actual USER prompt sent to Jan's writer.
            # Do not mutate the Jan transcript, or the raw messages recorded
            # for continuity extraction, and never decorate import captures.
            forwarded, decorated = prompt_affixes.wrap_user_message(messages, project)
            if decorated:
                self.log(f"applied configured user-message prefix/suffix for {project['alias']}")
            data["model"] = project["model"]
            data["messages"] = ([{"role": "system", "content": injection}] if injection
                                else []) + forwarded
            # Extraction may run while the writer responds, but this request's
            # injected memory was already selected from the preceding snapshot.
            record_user_message(project, messages)
            headers = {"Content-Type": "application/json"}
            authorization = self.headers.get("Authorization")
            if authorization:
                headers["Authorization"] = authorization
            host = self.upstream.hostname
            conn = http.client.HTTPConnection(host, self.upstream.port or 80, timeout=600)
            conn.request("POST", "/v1/chat/completions", body=json.dumps(data).encode("utf-8"),
                         headers=headers)
            upstream_reply = conn.getresponse()
            streaming = "text/event-stream" in upstream_reply.getheader("Content-Type", "")
            self.send_response(upstream_reply.status)
            self.send_header("Content-Type", upstream_reply.getheader("Content-Type", "application/json"))
            if streaming:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.close_connection = True
            else:
                raw = upstream_reply.read()
                self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            started = True
            self.log(f"project={project['alias']} model={project['model']} "
                     f"memory={used_files} status={upstream_reply.status} stream={streaming}")
            output = ""
            complete = False
            persisted = False
            if streaming:
                leftover = b""
                while True:
                    block = upstream_reply.read1(4096)
                    if not block:
                        break
                    leftover += block
                    while b"\n" in leftover:
                        line, leftover = leftover.split(b"\n", 1)
                        line = line.strip()
                        if not line.startswith(b"data: "):
                            continue
                        payload = line[6:]
                        if payload == b"[DONE]":
                            complete = True
                            continue
                        try:
                            event = json.loads(payload)
                            delta = event.get("choices", [{}])[0].get("delta", {})
                            piece = delta.get("content") if isinstance(delta, dict) else None
                            if isinstance(piece, str):
                                output += piece
                        except (ValueError, IndexError, AttributeError, TypeError):
                            pass
                    # Commit before forwarding the block containing [DONE].
                    if complete and not persisted and output and 200 <= upstream_reply.status < 300:
                        self.persist_completed_reply(project, unmodified_messages, output, used_files)
                        persisted = True
                    self.wfile.write(block)
                    self.wfile.flush()
            else:
                if 200 <= upstream_reply.status < 300:
                    try:
                        output = json.loads(raw)["choices"][0]["message"]["content"]
                        complete = isinstance(output, str)
                    except (ValueError, KeyError, IndexError, TypeError):
                        pass
                # Commit before sending the non-streamed HTTP response body.
                if complete and output and 200 <= upstream_reply.status < 300:
                    self.persist_completed_reply(project, unmodified_messages, output, used_files)
                    persisted = True
                self.wfile.write(raw)
            if not persisted and upstream_reply.status == 200:
                self.log("not queued (empty or interrupted exchange)")
        except (BrokenPipeError, ConnectionResetError):
            self.log("client disconnected; incomplete reply was not recorded")
            self.close_connection = True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.log(f"error {type(exc).__name__}: {str(exc)[:160]}")
            if not started:
                try:
                    self.send_json(502, {"error": f"Upstream failure: {type(exc).__name__}"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
            self.close_connection = True
        finally:
            if conn:
                conn.close()

    def log(self, message):
        print("[Storyteller] " + message, flush=True)

    def log_message(self, fmt, *args):
        print("[HTTP] " + (fmt % args), flush=True)


def serve(args):
    config = load_config()
    projects = {p["alias"]: p for p in config["projects"]}
    address = urlsplit(args.upstream)
    if address.hostname not in ("127.0.0.1", "localhost") or address.scheme != "http":
        raise SystemExit("Only localhost HTTP upstream is supported")
    Gateway.projects = projects
    Gateway.upstream = address
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Gateway)
    print(f"Storyteller 0.7.1 on http://127.0.0.1:{args.port}/v1", flush=True)
    for project in projects.values():
        print(f"  {project['alias']} -> {project['folder']}", flush=True)
    print("Only runs while this terminal is open. Press Ctrl+C to stop.", flush=True)
    worker = Worker(lambda: list(load_config()["projects"])) if not args.no_analysis else None
    if worker:
        worker.start()
    from prompt_import_worker import PromptImportWorker
    imports_worker = PromptImportWorker(lambda: list(load_config()["projects"]))
    imports_worker.start()

    def stop_on_signal(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGHUP, stop_on_signal)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        imports_worker.stop()
        if worker:
            worker.stop()
        print("Storyteller stopped.", flush=True)


def main():
    p = argparse.ArgumentParser(description="Storyteller local memory injection gateway")
    s = p.add_subparsers(dest="command", required=True)
    a = s.add_parser("add-project", help="Register a folder and model alias")
    a.add_argument("--name", required=True)
    a.add_argument("--alias", required=True)
    a.add_argument("--folder", required=True)
    a.add_argument("--model", default=DEFAULT_MODEL)
    a.set_defaults(func=add_project)
    l = s.add_parser("list-projects")
    l.set_defaults(func=lambda _: [print(f"{x['alias']} | {x['name']} | "
                                         f"{x['folder']} | model={x['model']}")
                                   for x in load_config()["projects"]])
    v = s.add_parser("serve")
    v.add_argument("--port", type=int, default=18880)
    v.add_argument("--upstream", default="http://127.0.0.1:1337")
    v.add_argument("--no-analysis", action="store_true", help="Disable CPU memory extraction")
    v.set_defaults(func=serve)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
