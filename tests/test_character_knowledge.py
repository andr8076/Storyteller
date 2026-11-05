"""Small comparative experiment: world canon versus attributed perspectives."""
import tempfile
import unittest
import sqlite3
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

    def test_correction_is_not_the_belief_and_literal_fallback_recovers_it(self):
        source = "Mara wrongly believed the gate was open, but in fact it was sealed."
        mistaken = {"character": "Mara", "topic": "gate state", "fact": "The gate was sealed",
                    "certainty": "believes_wrongly", "evidence_numbers": [1]}
        accepted, errors = knowledge.validate({"knowledge_changes": [mistaken]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(errors)
        recovered = knowledge.explicit_perspectives(source)
        self.assertEqual(recovered[0]["fact"], "The gate was open")
        self.assertEqual(recovered[0]["certainty"], "believes_wrongly")

    def test_missing_model_knowledge_recovers_suspicion_and_discovery(self):
        suspicion = knowledge.explicit_perspectives("Ivo suspected the vault was beneath the lake.")
        discovery = knowledge.explicit_perspectives("Ivo discovered the vault was beneath the lake.")
        self.assertEqual((suspicion[0]["topic"], suspicion[0]["certainty"]),
                         ("vault location", "suspects"))
        self.assertEqual((discovery[0]["topic"], discovery[0]["certainty"]),
                         ("vault location", "knows"))

    def test_adverbial_scene_prefix_recovers_proposition_not_mental_verb(self):
        scene = ("At the observatory, Nera suspected the signal came from the tower. "
                 "Later, Nera discovered the signal came from the tower.")
        rows = knowledge.explicit_perspectives(scene)
        self.assertEqual([(r["topic"], r["certainty"]) for r in rows],
                         [("signal source", "suspects"), ("signal source", "knows")])
        self.assertTrue(all(r["fact"] == "The signal came from the tower" for r in rows))

    def test_model_identity_misclassification_does_not_pollute_canon(self):
        scene = "Nera discovered the signal came from the tower."
        response = {"changes": [{"entity": "Nera", "category": "identity",
                    "fact": "Nera discovered the signal came from the tower",
                    "evidence_numbers": [1], "confidence": "high"}]}
        with patch.object(mem, "request_json", return_value=response):
            canon, _, rows = mem.extract(scene, include_knowledge=True)
        self.assertEqual(canon, [])
        self.assertEqual(rows[0]["fact"], "The signal came from the tower")

    def test_topic_variants_link_without_merging_another_belief(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            base = {"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence": "Ivo suspected the vault was beneath the lake."}
            knowledge.record(project, "scene-1", [base])
            knowledge.record(project, "scene-2", [
                {**base, "topic": "location of vault", "certainty": "knows"}])
            knowledge.record(project, "scene-3", [
                {**base, "topic": "vault ownership", "fact": "Ivo owns the vault",
                 "evidence": "Ivo learned he owns the vault.", "certainty": "knows"}])
            states = knowledge.current(project)
            self.assertEqual(len(states), 2)
            self.assertEqual({r["topic"] for r in states}, {"vault location", "vault ownership"})

    def test_dump_baseline_cannot_rewind_later_story_knowledge(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            base = {"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence": "Ivo suspected the vault was beneath the lake."}
            knowledge.record(project, "dump-1", [base], timeline=False)
            knowledge.record(project, "scene-1", [{**base, "certainty": "knows"}])
            knowledge.record(project, "dump-2", [base], timeline=False)
            self.assertEqual(knowledge.current(project)[0]["certainty"], "knows")
            with knowledge.database(project) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 3)

    def test_existing_experimental_ledger_reads_and_migrates_additively(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            root = Path(folder) / ".storyteller" / "memory"
            root.mkdir(parents=True)
            with sqlite3.connect(root / "character-knowledge.sqlite3") as db:
                db.execute("""CREATE TABLE knowledge (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    character TEXT NOT NULL, topic TEXT NOT NULL, fact TEXT NOT NULL,
                    certainty TEXT NOT NULL, evidence TEXT NOT NULL, source_id TEXT NOT NULL)""")
                db.execute("INSERT INTO knowledge (id,character,topic,fact,certainty,evidence,source_id) "
                           "VALUES ('old','Ivo','gate state','The gate is open','suspects',"
                           "'Ivo suspected the gate is open.','old-scene')")
            self.assertEqual(knowledge.current(project)[0]["fact"], "The gate is open")
            with knowledge.database(project) as db:
                self.assertIn("scope", {row[1] for row in db.execute("PRAGMA table_info(knowledge)")})
            self.assertEqual(knowledge.current(project)[0]["fact"], "The gate is open")


if __name__ == "__main__":
    unittest.main()
