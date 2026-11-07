#!/usr/bin/env python3
"""Disposable live Jan/Cydonia acceptance check. Starts no permanent services.

Requires an already installed Jan CLI and local writing/memory model weights.
Four small writing requests check current/history retrieval and delayed learning.
"""
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def stream_text(raw):
    pieces = []
    complete = False
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            complete = True
            continue
        for choice in json.loads(payload).get("choices", []):
            piece = choice.get("delta", {}).get("content")
            if isinstance(piece, str):
                pieces.append(piece)
    if not complete:
        raise ValueError("Incomplete streaming response")
    return "".join(pieces)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--jan", default=str(Path.home() / ".local/bin/jan"))
    parser.add_argument("--model", default="Cydonia-24B-v4.3-heretic-v3.IQ4_XS.gguf")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for port in (1337, 18885):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"Port {port} is occupied; leave existing services untouched") from exc
    sys.path.insert(0, str(args.repo))
    report = {"model": args.model, "requests": [], "checks": {}, "scope": "Live Jan CLI model API and Storyteller HTTP gateway; no Jan UI automation"}
    jan = worker = server = thread = None
    with tempfile.TemporaryDirectory(prefix="storyteller-acceptance-") as folder:
        root = Path(folder)
        config = root / "projects.json"
        os.environ["STORYTELLER_CONFIG"] = str(config)
        for key in ("STORYTELLER_PERSPECTIVE_ENDPOINT", "STORYTELLER_PERSPECTIVE_MODEL_FILE",
                    "STORYTELLER_LEDGER_SHADOW_ENDPOINT"):
            os.environ.pop(key, None)
        import storyteller
        import storyteller_ui
        import character_knowledge as knowledge
        import memory_worker
        from http.server import ThreadingHTTPServer
        project = {"alias": "acceptance", "name": "Disposable acceptance",
                   "folder": str(root / "project"), "model": args.model,
                   "learn_from_responses": False, "narrative_memory": False}
        storyteller.save_config({"projects": [project]})
        storyteller.ensure_memory(Path(project["folder"]))
        storyteller_ui.toggle_narrative_memory("acceptance")
        project = storyteller.load_config()["projects"][0]
        report["checks"]["persistent_setting"] = project["narrative_memory"] is True
        for identifier, source, fact, certainty, topic in [
            ("old-import", "Oren believed the flute was destroyed.", "The flute was destroyed", "believes", "flute fate"),
            ("later-correction", "Oren discovered the flute had never been destroyed.", "The flute had never been destroyed", "knows", "flute condition")]:
            rows, errors = knowledge.validate({"knowledge_changes": [{"character": "Oren", "topic": topic,
                "fact": fact, "certainty": certainty, "evidence_numbers": [1]}]}, source)
            assert not errors, errors
            knowledge.record(project, identifier, rows)
        snapshots = []
        select = storyteller.select_memory
        def capture(*values):
            selected = select(*values)
            snapshots.append(selected[0])
            return selected
        storyteller.select_memory = capture
        log_path = args.output.with_suffix(".server.log")
        api_key = secrets.token_urlsafe(24)
        with log_path.open("w") as log:
            try:
                jan = subprocess.Popen([args.jan, "serve", args.model, "--port", "1337",
                                        "--ctx-size", "4096", "--timeout", "120", "--api-key", api_key],
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 125
                while True:
                    if jan.poll() is not None:
                        raise RuntimeError("Jan server exited; inspect server log")
                    try:
                        with urlopen("http://127.0.0.1:1337/v1/models", timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("Jan server startup timed out")
                    time.sleep(.5)
                worker = memory_worker.Worker(lambda: storyteller.load_config()["projects"])
                if not worker.start():
                    raise RuntimeError("Local memory model did not start")
                storyteller.Gateway.upstream = urlsplit("http://127.0.0.1:1337")
                server = ThreadingHTTPServer(("127.0.0.1", 0), storyteller.Gateway)
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
                thread.start()
                endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
                def ask(text, stream=False):
                    start = time.monotonic()
                    request = Request(endpoint, data=json.dumps({"model": "acceptance", "temperature": 0,
                        "max_tokens": 90, "stream": stream,
                        "messages": [{"role": "user", "content": text}]}).encode(),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
                    with urlopen(request, timeout=180) as response:
                        raw = response.read().decode()
                    if stream:
                        answer = stream_text(raw)
                    else:
                        answer = json.loads(raw)["choices"][0]["message"]["content"]
                    report["requests"].append({"prompt": text, "answer": answer,
                        "seconds": round(time.monotonic() - start, 2), "stream": stream,
                        "injected_memory": snapshots[-1]})
                    print(json.dumps(report["requests"][-1]), flush=True)
                    args.output.write_text(json.dumps(report, indent=2) + "\n")
                ask("What does Oren know about the flute now? Answer in one short sentence.")
                ask("What did Oren believe about the flute before the correction? Answer in one short sentence.")
                ask("Oren suspected the observatory was beneath the lake. Reply only OK.")
                deadline = time.monotonic() + 100
                while not any("observatory" in row["fact"].lower() for row in knowledge.current(project)):
                    if time.monotonic() > deadline:
                        raise TimeoutError("The new user fact was not learned")
                    time.sleep(.5)
                ask("What does Oren suspect about the observatory? Answer in one short sentence.", stream=True)
                base = Path(project["folder"]) / ".storyteller"
                deadline = time.monotonic() + 10
                while True:
                    lines = (base / "pending-exchanges.jsonl").read_text().splitlines()
                    replies = [(hashlib.sha256(line.encode()).hexdigest(), json.loads(line))
                               for line in lines if json.loads(line).get("assistant_response")]
                    with sqlite3.connect(base / "processing.sqlite3", timeout=20) as db:
                        statuses = dict(db.execute("SELECT id, status FROM processed"))
                    disabled = len(replies) == 4 and all(
                        row.get("learn_response") is False and statuses.get(identifier) == "disabled"
                        for identifier, row in replies)
                    if disabled or time.monotonic() > deadline:
                        break
                    time.sleep(.2)
                report["checks"].update({
                    "current_corrected": "Current, Oren knows: The flute had never been destroyed" in snapshots[0]
                        and "Oren believes:" not in snapshots[0],
                    "history_available": "Earlier, Oren believes: The flute was destroyed" in snapshots[1],
                    "same_request_isolated": "observatory" not in snapshots[2].lower(),
                    "next_request_learned": "observatory" in snapshots[3].lower(),
                    "reply_learning_off": disabled,
                    "streaming_response": bool(report["requests"][-1]["answer"].strip())})
                report["knowledge"] = knowledge.history(project)
                if not all(report["checks"].values()):
                    raise AssertionError(f"Acceptance checks failed: {report['checks']}")
            except Exception as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                if server:
                    server.shutdown()
                    server.server_close()
                if thread:
                    thread.join(timeout=2)
                if worker:
                    worker.stop()
                if jan:
                    try:
                        os.killpg(jan.pid, signal.SIGTERM)
                        jan.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(jan.pid, signal.SIGKILL)
                        jan.wait()
                    except ProcessLookupError:
                        pass
                report["cleanup_completed"] = True
                args.output.write_text(json.dumps(report, indent=2) + "\n")
                print("Acceptance processes stopped", flush=True)


if __name__ == "__main__":
    main()
