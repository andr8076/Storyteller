"""Evidence-backed character identity links for narrative retrieval."""
import tempfile
import unittest

import character_knowledge as knowledge
import storyteller


class CharacterAliasTests(unittest.TestCase):
    def test_only_explicit_identity_language_creates_alias(self):
        self.assertEqual(
            knowledge.explicit_aliases(
                "Mara Ashford, also known as the Red Fox, entered the room."),
            [{"alias": "the Red Fox", "canonical": "Mara Ashford",
              "evidence": "Mara Ashford, also known as the Red Fox, entered the room."}])
        self.assertEqual(
            knowledge.explicit_aliases(
                "Mara Ashford met the Red Fox beside the observatory."), [])

    def test_alias_maps_old_and_new_observations_to_one_character(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder, "narrative_memory": True}
            source = "Mara Ashford, also known as the Red Fox, entered the room."
            row = {"character": "Red Fox", "topic": "key location",
                   "fact": "The key was inside the drawer", "certainty": "knows",
                   "evidence": "The Red Fox saw the key inside the drawer.",
                   "source_order": 1}
            self.assertEqual(knowledge.record(project, "scene", [row], source=source), 2)
            current = knowledge.current(project)
            self.assertEqual(current[0]["character"], "Mara Ashford")
            self.assertEqual(current[0]["aliases"], ["the Red Fox"])
            retrieved = knowledge.relevant(project, "Where is the Red Fox key?",
                                           storyteller.terms)
            self.assertIn("Mara Ashford", retrieved)
            self.assertIn("The Red Fox", retrieved)

    def test_conflicting_alias_is_left_unresolved(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            first = "Mara Ashford, also known as the Raven, entered the room."
            second = "Rhea Vale, also known as the Raven, watched the door."
            self.assertEqual(knowledge.record_aliases(project, "one", first), 1)
            self.assertEqual(knowledge.record_aliases(project, "two", second), 1)
            row = {"character": "the Raven", "topic": "door watch",
                   "fact": "The door was watched", "certainty": "knows",
                   "evidence": "The Raven watched the door.", "source_order": 1}
            knowledge.record(project, "three", [row])
            self.assertEqual(knowledge.current(project)[0]["character"], "the Raven")
            self.assertEqual(knowledge.aliases(project, "Mara Ashford"), [])


if __name__ == "__main__":
    unittest.main()
