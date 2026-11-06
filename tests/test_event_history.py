"""Older source-backed scenes must remain retrievable after recent-event rollover."""
import json
import tempfile
import unittest
from pathlib import Path

import storyteller


class EventHistoryTests(unittest.TestCase):
    def test_early_event_returns_with_evidence_after_recent_rollover(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            memory = Path(folder) / ".storyteller" / "memory"
            memory.mkdir(parents=True)
            early = {"event_id": "early", "category": "event", "entity": "Mara",
                     "fact": "Mara opened the silver observatory during the eclipse",
                     "evidence": "Mara unlocked the silver observatory as the eclipse began.",
                     "source_exchange": "import:catchup:chapter-1"}
            later = [{"event_id": f"late-{i}", "category": "event", "entity": "Rhea",
                      "fact": f"Rhea crossed bridge {i}",
                      "evidence": f"Rhea crossed bridge {i}.",
                      "source_exchange": f"scene-{i}"} for i in range(70)]
            (memory / "events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in [early] + later))
            (memory / "recent-events.md").write_text(
                "# Recent events\n" + "\n".join(f"- {x['fact']}" for x in later[-25:]))
            project = {"alias": "fiction", "folder": folder}
            prompt = [{"role": "user", "content": "What happened at the silver observatory eclipse?"}]
            injection, selected = storyteller.select_memory(project, prompt)
            self.assertIn(early["fact"], injection)
            self.assertIn(early["evidence"], injection)
            self.assertIn("import:catchup:chapter-1", injection)
            self.assertIn("memory/events.jsonl", selected)
            stemmed, _ = storyteller.select_memory(
                project, [{"role": "user", "content": "Who unlocks?"}])
            self.assertIn(early["fact"], stemmed)
            self.assertNotIn("Rhea crossed bridge 69",
                             injection.split("### recent-events.md")[0])
            self.assertEqual(storyteller.select_memory(
                {"alias": "other", "folder": other}, prompt), ("", []))

    def test_incremental_append_truncation_and_incomplete_line(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Path(folder) / ".storyteller" / "memory"
            memory.mkdir(parents=True)
            log = memory / "events.jsonl"
            project = {"alias": "fiction", "folder": folder}
            first = {"event_id": "a", "entity": "Ivo", "category": "event",
                     "fact": "Ivo broke the amber compass", "evidence": "Ivo broke the amber compass.",
                     "source_exchange": "scene-a"}
            second = {"event_id": "b", "entity": "Ivo", "category": "event",
                      "fact": "Ivo repaired the copper astrolabe",
                      "evidence": "Ivo repaired the copper astrolabe.",
                      "source_exchange": "scene-b"}
            log.write_text(json.dumps(first) + "\n")
            query = lambda text: storyteller.select_memory(
                project, [{"role": "user", "content": text}])[0]
            self.assertIn("amber compass", query("What happened to the amber compass?"))
            with log.open("a") as out:
                out.write(json.dumps(second))
            self.assertNotIn("copper astrolabe", query("copper astrolabe"))
            with log.open("a") as out:
                out.write("\n")
            self.assertIn("copper astrolabe", query("copper astrolabe"))
            log.write_text(json.dumps(second) + "\n")
            self.assertNotIn("amber compass", query("amber compass"))

    def test_dump_event_is_not_presented_as_past_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Path(folder) / ".storyteller" / "memory"
            memory.mkdir(parents=True)
            (memory / "events.jsonl").write_text(json.dumps({
                "event_id": "dump", "category": "event", "entity": "Lena",
                "fact": "Lena sailed the cobalt sea", "evidence": "Lena sailed the cobalt sea.",
                "source_exchange": "import:dump:reference"}) + "\n")
            injection, _ = storyteller.select_memory(
                {"alias": "fiction", "folder": folder},
                [{"role": "user", "content": "cobalt sea"}])
            self.assertNotIn("Lena sailed", injection)

    def test_specific_event_does_not_pull_other_same_character_scenes(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = Path(folder) / ".storyteller" / "memory"
            memory.mkdir(parents=True)
            rows = [{"event_id": str(i), "category": "event", "entity": "Mara",
                     "fact": f"Mara crossed bridge {i}",
                     "evidence": f"Mara crossed bridge {i}.",
                     "source_exchange": f"scene-{i}"} for i in list(range(60)) + [4231]]
            (memory / "events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            injection, _ = storyteller.select_memory(
                {"alias": "fiction", "folder": folder},
                [{"role": "user", "content": "Mara bridge 42"}])
            self.assertIn("Mara crossed bridge 42", injection)
            self.assertNotIn("Mara crossed bridge 0", injection)


if __name__ == "__main__":
    unittest.main()
