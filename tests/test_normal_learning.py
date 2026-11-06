"""Normal prompt learning and the persistent reply-learning switch."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_worker as mem
import storyteller
import storyteller_ui


class NormalLearningTests(unittest.TestCase):
    def test_user_fact_becomes_available_only_after_current_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            project = {"alias": "world-one", "name": "World One", "folder": folder,
                       "model": "writer"}
            storyteller.ensure_memory(Path(folder))
            message = [{"role": "user", "content": "Ivo suspected the vault was beneath the lake."}]
            current, _ = storyteller.select_memory(project, message)
            self.assertNotIn("Ivo suspects", current)
            storyteller.record_user_message(project, message)
            with patch.object(mem, "request_json", return_value={"changes": [], "knowledge_changes": [
                {"character": "Ivo", "topic": "vault location",
                 "fact": "The vault was beneath the lake", "certainty": "suspects",
                 "evidence_numbers": [1]}]}) as model:
                worker = mem.Worker([])
                worker.process_project(project)
                worker.process_project(project)
            model.assert_called_once()
            self.assertTrue(model.call_args.kwargs["author_message"])
            next_request, _ = storyteller.select_memory(project, message)
            self.assertIn("Ivo suspects: The vault was beneath the lake", next_request)
            isolated, _ = storyteller.select_memory(
                {"alias": "world-two", "folder": other}, message)
            self.assertEqual(isolated, "")

    def test_writing_instruction_is_not_a_canon_source(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            storyteller.record_user_message(project, [
                {"role": "user", "content": "Write a scene where Ivo discovers the vault."}])
            with patch.object(mem, "request_json") as model:
                mem.Worker([]).process_project(project)
            model.assert_not_called()
            self.assertEqual(mem.authorial_statements(
                "Write the next scene. Ivo suspects the vault is under the lake."),
                "Ivo suspects the vault is under the lake.")
            self.assertEqual(mem.authorial_statements(
                "In the next scene, Ivo discovers the vault. Have Ivo open its door."), "")

    def test_reply_learning_switch_persists_and_does_not_disable_user_learning(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "projects.json"
            project = {"alias": "fiction", "name": "Fiction", "folder": folder,
                       "model": "writer"}
            with patch.object(storyteller, "CONFIG", config_path):
                storyteller.save_config({"version": 1, "projects": [project]})
                self.assertFalse(storyteller_ui.toggle_response_learning("fiction"))
                configured = storyteller.load_config()["projects"][0]
                self.assertIs(configured["learn_from_responses"], False)
                messages = [{"role": "user", "content": "Ivo suspected the vault was beneath the lake."}]
                storyteller.record_user_message(configured, messages)
                storyteller.record_complete(configured, messages,
                                            "Ivo walked into the vault and saw a flame.", [])
                with patch.object(mem, "request_json", return_value={"changes": []}) as model:
                    mem.Worker([]).process_project(configured)
                model.assert_called_once()  # Only the user source was analyzed.
                self.assertFalse((Path(folder) / ".storyteller" / "memory" /
                                  "reference-notes" / "generated").exists())
                with sqlite3.connect(Path(folder) / ".storyteller" / "processing.sqlite3") as db:
                    statuses = [x[0] for x in db.execute("SELECT status FROM processed")]
                self.assertCountEqual(statuses, ["done", "disabled"])
                self.assertTrue(storyteller_ui.toggle_response_learning("fiction"))
                self.assertTrue(storyteller.load_config()["projects"][0]["learn_from_responses"])


if __name__ == "__main__":
    unittest.main()
