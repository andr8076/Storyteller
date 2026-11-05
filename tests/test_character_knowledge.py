"""Small comparative experiment: world canon versus attributed perspectives."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import character_knowledge as knowledge
import storyteller
import memory_worker as mem
import import_modes


class PerspectiveExperiment(unittest.TestCase):
    def test_suspicion_then_discovery_preserves_history_and_latest_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            source1 = "Ivo suspected the vault was beneath the lake."
            source2 = "Ivo discovered the vault was beneath the lake."
            claim = {"character": "Ivo", "topic": "vault location",
                     "fact": "The vault was beneath the lake", "evidence_numbers": [1]}
            first, errors = knowledge.validate({"knowledge_changes": [
                {**claim, "certainty": "suspects"}]}, source1)
            self.assertEqual(errors, [])
            self.assertEqual(knowledge.record(project, "scene-1", first), 1)
            self.assertEqual(knowledge.record(project, "scene-1", first), 0)
            prompt = [{"role": "user", "content": "What does Ivo think about the vault?"}]
            injection, _ = storyteller.select_memory(project, prompt)
            self.assertIn("Ivo suspects: The vault was beneath the lake", injection)
            second, errors = knowledge.validate({"knowledge_changes": [
                {**claim, "certainty": "knows"}]}, source2)
            self.assertEqual(errors, [])
            self.assertEqual(knowledge.record(project, "scene-2", second), 1)
            injection, _ = storyteller.select_memory(project, prompt)
            self.assertIn("Ivo knows: The vault was beneath the lake", injection)
            self.assertNotIn("Ivo suspects:", injection)
            self.assertIn("Ivo — vault location", (Path(folder) / ".storyteller" /
                "memory" / "character-knowledge.md").read_text())
            with knowledge.database(project) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 2)

    def test_unsupported_perspective_and_other_project_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            project = {"alias": "first", "folder": folder}
            suggestion = {"character": "Ivo", "topic": "vault location",
                          "fact": "The vault was beneath the lake", "certainty": "knows",
                          "evidence_numbers": [1]}
            accepted, errors = knowledge.validate({"knowledge_changes": [suggestion]},
                                                   "Ivo walked beside the lake.")
            self.assertEqual(accepted, [])
            self.assertTrue(errors)
            self.assertEqual(storyteller.select_memory(
                {"alias": "other", "folder": other},
                [{"role": "user", "content": "Ivo vault"}]), ("", []))

    def test_one_model_response_keeps_suspicion_out_of_world_canon(self):
        text = "Ivo suspected the vault was beneath the lake."
        response = {"changes": [{"entity": "Vault", "category": "place",
                    "fact": "The vault was beneath the lake", "evidence_numbers": [1],
                    "confidence": "high"}],
                    "knowledge_changes": [{"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence_numbers": [1]}]}
        with patch.object(mem, "request_json", return_value=response) as request:
            canon, _, perspectives = mem.extract(text, include_knowledge=True)
        request.assert_called_once()
        self.assertEqual(canon, [])
        self.assertEqual(len(perspectives), 1)
        imported, _, _ = import_modes.validate_records(response, text, text, "catchup")
        self.assertEqual(imported, [])

    def test_wrong_belief_requires_source_correction(self):
        source = "Ivo believed the gate was open."
        row = {"character": "Ivo", "topic": "gate state", "fact": "The gate was open",
               "certainty": "believes_wrongly", "evidence_numbers": [1]}
        accepted, errors = knowledge.validate({"knowledge_changes": [row]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
