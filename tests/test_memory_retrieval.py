"""Retrieval checks using disposable, unrelated fictional worlds."""
import tempfile
import unittest
from pathlib import Path

import storyteller


class MemoryRetrievalTests(unittest.TestCase):
    def test_late_profile_fact_is_retrieved_by_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".storyteller" / "memory" / "characters"
            root.mkdir(parents=True)
            (root / "mara.md").write_text(
                "# Mara\n" + "- Ordinary archival note.\n" * 110 +
                "- The blue lantern opens the sealed observatory.\n", encoding="utf-8")
            project = {"alias": "test-world", "folder": directory}
            messages = [{"role": "user", "content": "Where does the blue lantern lead?"}]
            injection, selected = storyteller.select_memory(project, messages)
            self.assertIn("blue lantern opens the sealed observatory", injection)
            self.assertIn("characters/mara.md", selected)
            self.assertNotIn("Ordinary archival note", injection)

    def test_memory_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            root = Path(first) / ".storyteller" / "memory" / "characters"
            root.mkdir(parents=True)
            (root / "mara.md").write_text("# Mara\n- The blue lantern is hidden here.\n")
            messages = [{"role": "user", "content": "blue lantern"}]
            self.assertEqual(storyteller.select_memory(
                {"alias": "other", "folder": second}, messages), ("", []))


if __name__ == "__main__":
    unittest.main()
