"""Release boundaries for narrative memory, exercised through the HTTP gateway."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import character_knowledge as knowledge
import import_modes
import memory_worker
import narrative_ledger
import narrative_memory
import prompt_mode
import storyteller
import storyteller_ui


def store(project, source_id, source, fact, certainty, topic, *, timeline=True):
    rows, errors = knowledge.validate({"knowledge_changes": [{
        "character": "Oren", "topic": topic, "fact": fact, "certainty": certainty,
        "evidence_numbers": [1]}]}, source)
    assert not errors, errors
    return knowledge.record(project, source_id, rows, timeline=timeline)


@contextmanager
def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class NarrativeMemoryTests(unittest.TestCase):
    def test_http_gateway_current_history_snapshot_and_import_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "name": "Fiction", "folder": folder,
                       "model": "mock", "narrative_memory": True}
            received = []
            class Writer(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_POST(self):
                    received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                    body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "Done."},
                                                     "finish_reason": "stop"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            class Gateway(storyteller.Gateway):
                def get_projects(self):
                    return {project["alias"]: project}
                def log(self, *args):
                    pass
                def log_message(self, *args):
                    pass
            store(project, "catchup", "Oren believed the flute was destroyed.",
                  "The flute was destroyed", "believes", "flute fate")
            original_record = storyteller.record_user_message
            correction = "Oren discovered the flute had never been destroyed."
            def learn_after_snapshot(project, messages):
                original_record(project, messages)
                store(project, "new-conversation", correction,
                      "The flute had never been destroyed", "knows", "flute condition")
            with serve(Writer) as writer:
                Gateway.upstream = urlsplit(writer)
                with serve(Gateway) as gateway:
                    def send(message):
                        request = Request(gateway + "/v1/chat/completions", data=json.dumps({
                            "model": "fiction", "messages": [{"role": "user", "content": message}]
                        }).encode(), headers={"Content-Type": "application/json"})
                        with urlopen(request, timeout=5) as response:
                            self.assertEqual(response.status, 200)
                            return json.load(response)
                    with patch.object(storyteller, "record_user_message", side_effect=learn_after_snapshot):
                        send(correction)
                    first = received[-1]["messages"][0]["content"]
                    self.assertIn("Current, Oren believes: The flute was destroyed", first)
                    self.assertNotIn("Current, Oren knows", first)
                    send("What does Oren know about the flute now?")
                    current = received[-1]["messages"][0]["content"]
                    self.assertIn("Current, Oren knows: The flute had never been destroyed", current)
                    self.assertNotIn("Oren believes:", current)
                    send("What did Oren believe about the flute before?")
                    historical = received[-1]["messages"][0]["content"]
                    self.assertIn("Earlier, Oren believes: The flute was destroyed", historical)
                    self.assertIn("superseded by source: new-conversation", historical)
                    count = len(received)
                    for mode in ("catchup", "dump"):
                        prompt_mode.arm(project, mode)
                        send("Captured fictional information for " + mode)
                    self.assertEqual(len(received), count)

    def test_scope_precedence_isolation_and_budget(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            project = {"alias": "fiction", "folder": folder, "narrative_memory": True}
            store(project, "normal", "Oren discovered the flute had never been destroyed.",
                  "The flute had never been destroyed", "knows", "flute condition")
            store(project, "later-dump", "Oren believed the flute was destroyed.",
                  "The flute was destroyed", "believes", "flute fate", timeline=False)
            snippet = knowledge.relevant(project, "Oren flute", storyteller.terms)
            self.assertIn("Oren knows", snippet)
            self.assertNotIn("Oren believes", snippet)
            self.assertEqual(knowledge.relevant({**project, "folder": other}, "Oren flute", storyteller.terms), "")
            for limit in (0, 20, 250):
                self.assertLessEqual(len(knowledge.relevant(project, "Oren flute", storyteller.terms,
                                                           limit=limit)), limit)

    def test_narrative_catchup_publishes_witness_to_shared_history_once(self):
        fixture = json.loads((Path(__file__).parents[1] / "experiments/results/narrative_memory_2026-11-06.json").read_text())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "scene.txt"
            source.write_text(fixture["source"])
            project = {"alias": "fiction", "name": "Fiction", "folder": str(root / "project"),
                       "narrative_memory": True}
            with (patch.dict(os.environ, {"STORYTELLER_PERSPECTIVE_ENDPOINT":
                      "http://127.0.0.1:18986/v1/chat/completions", "STORYTELLER_LEDGER_SHADOW_ENDPOINT": ""}),
                  patch.object(memory_worker, "is_healthy", return_value=True),
                  patch.object(import_modes, "request_model", return_value={"changes": [], "summary": ""}),
                  patch.object(import_modes, "request_perspectives") as focused,
                  patch.object(narrative_ledger, "propose", return_value=fixture["calls"]["episodes"]["raw"]) as broad):
                self.assertEqual(import_modes.run_import(project, "catchup", source)["state"], "complete")
                import_modes.run_import(project, "catchup", source)
            broad.assert_called_once()
            focused.assert_not_called()
            self.assertEqual(len(knowledge.history(project)), 3)
            injected, _ = storyteller.select_memory(project, [{"role": "user", "content": "What did Sera witness?"}])
            self.assertIn("Sera knows: Dain locked the silver flute", injected)
            self.assertEqual(source.read_text(), fixture["source"])

    def test_old_database_is_read_without_migration_and_toggle_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "name": "Fiction", "folder": folder, "narrative_memory": True}
            path = Path(folder) / ".storyteller/memory/character-knowledge.sqlite3"
            path.parent.mkdir(parents=True)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE knowledge (sequence INTEGER PRIMARY KEY, character TEXT, topic TEXT, "
                           "fact TEXT, certainty TEXT, evidence TEXT, source_id TEXT)")
                db.execute("INSERT INTO knowledge VALUES (1,'Oren','flute fate','The flute was destroyed',"
                           "'believes','Oren believed the flute was destroyed.','old')")
            original = path.read_bytes()
            self.assertIn("Oren believes", knowledge.relevant(project, "Oren flute", storyteller.terms))
            self.assertEqual(path.read_bytes(), original)
            with patch.object(storyteller, "CONFIG", Path(folder) / "projects.json"):
                storyteller.save_config({"projects": [dict(project, narrative_memory=False)]})
                self.assertTrue(storyteller_ui.toggle_narrative_memory("fiction"))
                self.assertTrue(storyteller.load_config()["projects"][0]["narrative_memory"])
                self.assertFalse(storyteller_ui.toggle_narrative_memory("fiction"))


if __name__ == "__main__":
    unittest.main()
