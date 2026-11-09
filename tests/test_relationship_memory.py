"""Evidence-backed relationship state transitions and retrieval."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import character_knowledge
import import_modes
import memory_worker
import relationship_memory
import storyteller


def relation(fact, evidence=None, source_order=1):
    return {"category": "relationship", "entity": fact.split()[0],
            "fact": fact, "evidence": evidence or fact,
            "source_order": source_order}


class RelationshipMemoryTests(unittest.TestCase):
    def test_parser_accepts_explicit_states_and_rejects_actions(self):
        self.assertEqual(
            relationship_memory.parse(relation("Mara trusts Rhea")),
            {"subject": "Mara", "object": "Rhea", "relation": "trust",
             "polarity": 1, "fact": "Mara trusts Rhea"})
        self.assertEqual(
            relationship_memory.parse(relation("Mara no longer trusts Rhea")),
            {"subject": "Mara", "object": "Rhea", "relation": "trust",
             "polarity": 0, "fact": "Mara no longer trusts Rhea"})
        self.assertEqual(
            relationship_memory.parse(relation("Mara and Rhea are not allies"))["polarity"], 0)
        self.assertEqual(
            relationship_memory.parse(relation("Mara is no longer allied with Rhea"))["polarity"], 0)
        self.assertEqual(
            relationship_memory.parse(relation("Mara was not married to Rhea"))["polarity"], 0)
        self.assertEqual(
            relationship_memory.parse(relation("Mara is Rhea's sister"))["relation"], "sibling")
        self.assertIsNone(relationship_memory.parse(relation("Mara met Rhea")))

    def test_missing_evidence_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            self.assertEqual(relationship_memory.record(
                project, "scene-1", [{"category": "relationship",
                                      "fact": "Mara trusts Rhea"}]), 0)
            self.assertEqual(relationship_memory.history(project), [])

    def test_later_state_supersedes_current_view_but_not_history(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            self.assertEqual(relationship_memory.record(
                project, "chapter-1", [relation("Mara trusts Rhea")], timeline=False), 1)
            self.assertEqual(relationship_memory.record(
                project, "scene-2", [relation("Mara no longer trusts Rhea")]), 1)

            current = relationship_memory.current(project)
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["polarity"], 0)
            self.assertEqual(current[0]["source_id"], "scene-2")

            present = relationship_memory.relevant(
                project, "Does Mara trust Rhea now?", storyteller.terms)
            self.assertIn("Current relationship: Mara no longer trusts Rhea", present)
            self.assertNotIn("Mara trusts Rhea [source: chapter-1", present)

            past = relationship_memory.relevant(
                project, "What did Mara trust before?", storyteller.terms)
            self.assertIn("Current relationship: Mara no longer trusts Rhea", past)
            self.assertIn("Earlier relationship: Mara trusts Rhea", past)

    def test_symmetric_relationships_reconcile_reversed_name_order(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            relationship_memory.record(
                project, "chapter-1", [relation("Mara and Rhea are married")])
            relationship_memory.record(
                project, "scene-2", [relation("Rhea and Mara are no longer married")])
            self.assertEqual(len(relationship_memory.current(project)), 1)
            self.assertEqual(relationship_memory.current(project)[0]["polarity"], 0)

    def test_alias_learned_later_reconciles_existing_relationship_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            relationship_memory.record(
                project, "scene-1", [relation("Red Fox trusts Rhea")])
            character_knowledge.record_aliases(
                project, "scene-2", "Mara Ashford, also known as the Red Fox, entered.")
            current = relationship_memory.current(project)
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["subject"], "Mara Ashford")
            retrieved = relationship_memory.relevant(
                project, "Does Mara Ashford trust Rhea?", storyteller.terms)
            self.assertIn("Red Fox trusts Rhea", retrieved)

    def test_markdown_and_project_isolation_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            project = {"alias": "fiction", "folder": folder}
            relationship_memory.record(
                project, "scene-1", [relation("Mara loves Rhea",
                                                "Mara told Rhea that she loved her.")])
            exported = Path(folder) / ".storyteller/memory/relationship-history.md"
            self.assertIn("Mara loves Rhea", exported.read_text(encoding="utf-8"))
            self.assertIn("Mara told Rhea", exported.read_text(encoding="utf-8"))
            self.assertEqual(relationship_memory.history({"alias": "other", "folder": other}), [])
            self.assertEqual(storyteller.select_memory(
                {"alias": "other", "folder": other},
                [{"role": "user", "content": "Mara loves Rhea"}]), ("", []))

    def test_gateway_retrieves_current_relationship_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            relationship_memory.record(
                project, "old", [relation("Mara trusts Rhea")], timeline=False)
            relationship_memory.record(
                project, "new", [relation("Mara no longer trusts Rhea")])
            injection, selected = storyteller.select_memory(
                project, [{"role": "user", "content": "Does Mara trust Rhea now?"}])
            self.assertIn("### RELATIONSHIP STATES", injection)
            self.assertIn("Current relationship: Mara no longer trusts Rhea", injection)
            self.assertNotIn("Mara trusts Rhea [source: old", injection)
            self.assertIn("memory/relationship-history.sqlite3", selected)

    def test_normal_learning_records_relationship_through_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder, "model": "writer",
                       "learn_from_responses": False}
            storyteller.ensure_memory(Path(folder))
            storyteller.record_user_message(project, [{
                "role": "user", "content": "Mara trusts Rhea."}])
            model_result = {"changes": [{
                "entity": "Mara", "category": "relationship",
                "fact": "Mara trusts Rhea", "evidence_numbers": [1],
                "confidence": "high"}], "knowledge_changes": []}
            with patch.object(memory_worker, "request_json", return_value=model_result):
                memory_worker.Worker([project]).process_project(project)
            rows = relationship_memory.history(project)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fact"], "Mara trusts Rhea")

    def test_catchup_records_relationship_states_without_editing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "chapter.txt"
            original = "Mara trusts Rhea. Later, Mara no longer trusts Rhea."
            source.write_text(original, encoding="utf-8")
            project = {"alias": "fiction", "name": "Fiction",
                       "folder": str(root / "project"), "narrative_memory": True}
            result = {"changes": [
                {"entity": "Mara", "category": "relationship",
                 "fact": "Mara trusts Rhea", "evidence_numbers": [1], "confidence": "high"},
                {"entity": "Mara", "category": "relationship",
                 "fact": "Mara no longer trusts Rhea", "evidence_numbers": [2], "confidence": "high"}],
                "summary": "Trust changed."}
            with patch.object(memory_worker, "is_healthy", return_value=True), \
                 patch.object(import_modes, "request_model", return_value=result):
                self.assertEqual(import_modes.run_import(project, "catchup", source)["state"],
                                 "complete")
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertEqual(len(relationship_memory.current(project)), 1)
            self.assertEqual(relationship_memory.current(project)[0]["polarity"], 0)


if __name__ == "__main__":
    unittest.main()
