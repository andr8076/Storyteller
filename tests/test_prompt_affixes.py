"""Tests for user-message prompt affixes. Run: python3 -m unittest discover -s tests -v."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prompt_affixes


class AffixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="storyteller-affixes-")
        self.addCleanup(self.temp.cleanup)
        self.project = {"alias": "storyteller-test", "folder": self.temp.name}
        self.other = {"alias": "storyteller-other", "folder": self.temp.name}

    def test_opt_in_and_original_unchanged(self):
        messages = [
            {"role": "user", "content": "Yesterday's message"},
            {"role": "assistant", "content": "Yesterday's response"},
            {"role": "user", "content": "Continue."},
        ]
        self.assertEqual(prompt_affixes.wrap_user_message(messages, self.project),
                         (messages, False))
        prompt_affixes.set_text(self.project, "prefix", "BEGIN")
        prompt_affixes.set_text(self.project, "suffix", "END")
        wrapped, used = prompt_affixes.wrap_user_message(messages, self.project)
        self.assertTrue(used)
        self.assertEqual(wrapped[-1]["content"], "BEGIN\n\nContinue.\n\nEND")
        self.assertEqual(messages[-1]["content"], "Continue.")
        self.assertIs(wrapped[0], messages[0])

    def test_toggle_independently(self):
        prompt_affixes.set_text(self.project, "prefix", "Prefix")
        prompt_affixes.set_text(self.project, "suffix", "Suffix")
        prompt_affixes.set_enabled(self.project, "prefix", False)
        message = [{"role": "user", "content": "Body"}]
        wrapped, used = prompt_affixes.wrap_user_message(message, self.project)
        self.assertTrue(used)
        self.assertEqual(wrapped[0]["content"], "Body\n\nSuffix")
        self.assertEqual(prompt_affixes.get_text(self.project, "prefix"), "Prefix")
        prompt_affixes.set_enabled(self.project, "suffix", False)
        self.assertFalse(prompt_affixes.wrap_user_message(message, self.project)[1])

    def test_isolation_even_with_shared_folder(self):
        prompt_affixes.set_text(self.project, "prefix", "Only this one")
        message = [{"role": "user", "content": "Hello"}]
        self.assertFalse(prompt_affixes.wrap_user_message(message, self.other)[1])

    def test_multimodal_keeps_images_inside_user_message(self):
        prompt_affixes.set_text(self.project, "prefix", "Start")
        prompt_affixes.set_text(self.project, "suffix", "Finish")
        message = [{"role": "user", "content": [
            {"type": "text", "text": "Inspect"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,example"}},
        ]}]
        modified, used = prompt_affixes.wrap_user_message(message, self.project)
        self.assertTrue(used)
        self.assertEqual(modified[0]["role"], "user")
        self.assertEqual(modified[0]["content"][0]["text"], "Start\n\n")
        self.assertEqual(modified[0]["content"][-1]["text"], "\n\nFinish")
        self.assertEqual(modified[0]["content"][2], message[0]["content"][1])
        self.assertEqual(len(message[0]["content"]), 2)

    def test_non_user_last_message_not_modified(self):
        prompt_affixes.set_text(self.project, "prefix", "Prefix")
        messages = [{"role": "assistant", "content": "Already replied"}]
        self.assertFalse(prompt_affixes.wrap_user_message(messages, self.project)[1])

    def test_empty_cannot_enable(self):
        with self.assertRaises(ValueError):
            prompt_affixes.set_enabled(self.project, "prefix", True)
        with self.assertRaises(ValueError):
            prompt_affixes.set_text(self.project, "suffix", "X" * 40000)

if __name__ == "__main__":
    unittest.main()
