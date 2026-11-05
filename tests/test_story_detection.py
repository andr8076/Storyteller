"""Regression coverage for scene classification and skipped-scene recovery.

Run: python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_worker
import story_state


def prose(paragraph="The captain opened the hatch."):
    return (paragraph + " The engine hummed. A distant voice spoke. "
            "The lights flickered across the deck.\n\n"
            '"We need to keep going," the captain said.\n'
            "A signal flashed. The crew stood silent.\n") * 5


class SceneClassificationTests(unittest.TestCase):
    def test_negative_analysis_instruction_still_story(self):
        prompt = ("Continue the story directly from where this scene ends. "
                  "Write the next scene in prose. Do not analyze, summarize, "
                  "or explain the story.")
        self.assertTrue(story_state.is_narrative_exchange(prompt, prose()))

    def test_raw_story_without_instruction_is_story(self):
        self.assertTrue(story_state.is_narrative_exchange(prose() * 4, prose()))

    def test_analysis_output_is_never_story(self):
        self.assertFalse(story_state.is_narrative_exchange(
            "Continue the story. Write the next scene.",
            "# Analysis of Scene Context\n" + prose()))

    def test_summary_and_factual_questions_are_not_story(self):
        self.assertFalse(story_state.is_narrative_exchange(
            "Summarize the following conversation into a title. " + prose(),
            "A title about the captain"))
        self.assertFalse(story_state.is_narrative_exchange(
            "What happened in the story?", prose()))
        self.assertFalse(story_state.is_narrative_exchange(
            "Write me a character profile from the last scene.", prose()))

    def test_do_not_analyze_does_not_save_meta_output(self):
        prompt = "Continue the story. Do not analyze or summarize."
        self.assertFalse(story_state.is_narrative_exchange(
            prompt, "# Analysis\n" + prose()))


class RecoverSkippedTests(unittest.TestCase):
    def test_reprocesses_previously_skipped_story_but_not_titles(self):
        with tempfile.TemporaryDirectory(prefix="storyteller-recovery-test-") as tmp:
            project = {"alias": "storyteller-recovery",
                       "folder": str(Path(tmp) / "Story")}
            folder = Path(project["folder"]) / ".storyteller"
            folder.mkdir(parents=True)
            exchange = {
                "project": project["alias"],
                "last_user_message": ("Continue the story, write the next scene "
                                      "in prose. Do not analyze, summarize, or explain."),
                "assistant_response": prose(),
            }
            title = {
                "project": project["alias"],
                "last_user_message": "Summarize the conversation in a title.",
                "assistant_response": "The Mysterious Vessel",
            }
            jsonlines = [json.dumps(entry) for entry in (exchange, title)]
            (folder / "pending-exchanges.jsonl").write_text(
                "\n".join(jsonlines) + "\n", encoding="utf-8")
            database = folder / "processing.sqlite3"
            with sqlite3.connect(database) as db:
                db.execute("""CREATE TABLE processed
                           (id TEXT PRIMARY KEY, status TEXT NOT NULL,
                            changes INTEGER DEFAULT 0, reason TEXT,
                            processed_at TEXT)""")
                for line in jsonlines:
                    db.execute("INSERT INTO processed VALUES (?, ?, ?, ?, ?)",
                               (hashlib.sha256(line.encode()).hexdigest(),
                                "skipped", 0, "short or non-narrative response", "earlier"))
            worker = memory_worker.Worker([project])
            with patch.object(memory_worker, "extract", return_value=([], [], [])) as mock_extract, \
                 patch.object(memory_worker, "apply_changes", return_value=1) as mock_apply:
                worker.process_project(project)
                worker.process_project(project)  # must not process duplicate
            self.assertEqual(mock_extract.call_count, 1)
            self.assertEqual(mock_apply.call_count, 1)
            with sqlite3.connect(database) as db:
                result = [db.execute("SELECT status FROM processed WHERE id=?",
                         (hashlib.sha256(line.encode()).hexdigest(),)).fetchone()[0]
                          for line in jsonlines]
            self.assertEqual(result, ["done", "skipped"])


class MemoryGroundingTests(unittest.TestCase):
    def test_rejects_misattributed_facts(self):
        invalid = [
            ("Demi", "Demi is a human",
             "Three figures stood in the doorway."),
            ("The Director", "The Director was betrayed by his people",
             "We sent the message because we are the only ones left."),
            ("The Director",
             "The Director has a laser scar running down his left forearm.",
             "The Director sized him up quickly: middle-aged, military bearing "
             "despite the civilian clothes, a laser scar running down his left forearm."),
        ]
        for entity, fact, evidence in invalid:
            with self.subTest(fact=fact):
                self.assertFalse(memory_worker.evidence_supports_claim(
                    entity, fact, evidence))
        self.assertTrue(memory_worker.evidence_supports_claim(
            "Demi", "Demi wears silver earrings",
            "Demi wears silver earrings."))

    def test_exact_story_archives_even_when_no_facts_are_found(self):
        with tempfile.TemporaryDirectory(prefix="storyteller-scene-archive-") as tmp:
            project = {"alias": "storyteller-archive", "folder": tmp}
            scene = prose() * 5
            memory_worker.archive_generated_scene(project, "example123", scene)
            archive = (Path(tmp) / ".storyteller" / "memory" /
                       "reference-notes" / "generated-example123")
            chunks = [file.read_text(encoding="utf-8").split("\n\n", 1)[1]
                      for file in sorted(archive.glob("section-*.md"))]
            self.assertEqual("".join(chunks), scene)


if __name__ == "__main__":
    unittest.main()
