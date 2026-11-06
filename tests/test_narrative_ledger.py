"""A reversible comparison of event-first records against current profiles."""
import tempfile
import unittest

import character_knowledge
import narrative_ledger as ledger


class NarrativeLedgerExperiment(unittest.TestCase):
    def test_deception_retains_action_speech_belief_and_later_observation(self):
        source = ("Rhea hid the map in the drawer. "
                  "Rhea told Joss, 'The map burned in the fire.' Joss believed her. "
                  "Later, Joss found the map in the drawer.")
        proposals = [
            {"kind": "action", "actor": "Rhea", "claim": "Rhea hid the map in the drawer",
             "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map burned in the fire", "evidence_numbers": [2]},
            {"kind": "belief", "actor": "Joss", "topic": "map location", "certainty": "believes",
             "claim": "The map burned in the fire", "evidence_numbers": [2]},
            {"kind": "observation", "actor": "Joss", "topic": "map location",
             "claim": "The map was in the drawer", "evidence_numbers": [3]},
        ]
        entries, rejected = ledger.validate(proposals, source)
        self.assertEqual(rejected, [])
        self.assertEqual([r["kind"] for r in entries],
                         ["action", "assertion", "belief", "observation"])
        self.assertEqual(len(character_knowledge.explicit_perspectives(source)), 1)
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "isolated", "folder": folder}
            self.assertEqual(ledger.record(project, "scene-1", entries), 4)
            self.assertEqual(ledger.record(project, "scene-1", entries), 0)
            self.assertEqual(len(ledger.character_view(project, "Joss")), 1)
            self.assertEqual(ledger.character_view(project, "Joss")[0]["kind"], "observation")
            self.assertEqual(len(ledger.character_view(project, "Joss", historical=True)), 2)
            self.assertEqual(source[entries[1]["start"]:entries[1]["end"]].strip(),
                             entries[1]["evidence"])

    def test_assertion_and_ambiguous_speech_never_become_world_action(self):
        source = ("Rhea told Joss, 'The map burned in the fire.' "
                  "Rhea told Joss, 'The map was in the drawer.' Joss believed her.")
        proposals = [
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map burned in the fire", "evidence_numbers": [1]},
            {"kind": "action", "actor": "Rhea", "claim": "Rhea burned the map in the fire",
             "evidence_numbers": [1]},
            {"kind": "belief", "actor": "Joss", "topic": "map state", "certainty": "believes",
             "claim": "The map was in the drawer", "evidence_numbers": [2]},
        ]
        accepted, rejected = ledger.validate(proposals, source)
        self.assertEqual([row["kind"] for row in accepted], ["assertion"])
        self.assertEqual(len(rejected), 2)


if __name__ == "__main__":
    unittest.main()
