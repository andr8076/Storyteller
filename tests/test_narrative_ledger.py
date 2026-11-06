"""A reversible comparison of event-first records against current profiles."""
import tempfile
import unittest
from pathlib import Path

import character_knowledge
import narrative_ledger as ledger


class NarrativeLedgerExperiment(unittest.TestCase):
    def test_live_witness_output_links_one_claim_to_named_listener(self):
        source = ("Mira watched Rhea place the blue ledger under the floorboard. "
                  "Rhea told Joss, 'The ledger burned.' Later, Joss believed Rhea's claim.")
        proposals = [
            {"kind": "action", "actor": "Rhea", "claim": "placed the blue ledger under the floorboard",
             "topic": "", "evidence_numbers": [1]},
            {"kind": "observation", "actor": "Mira",
             "claim": "Rhea placed the blue ledger under the floorboard",
             "topic": "", "certainty": "knows", "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The ledger burned", "topic": "", "evidence_numbers": [2]},
            {"kind": "belief", "actor": "Joss", "claim": "The ledger burned",
             "topic": "", "certainty": "believes", "evidence_numbers": [2]},
        ]
        entries, rejected = ledger.validate(proposals, source)
        self.assertEqual(rejected, [])
        self.assertEqual([r["kind"] for r in entries],
                         ["action", "observation", "assertion", "belief"])
        self.assertEqual(entries[-1]["source_order"], 2)  # See numbered sentence split.
        self.assertIn("Joss believed Rhea's claim", entries[-1]["evidence"])
        self.assertEqual(entries[1]["actor"], "Mira")

    def test_local_model_verb_phrases_and_missing_certainty_are_grounded(self):
        source = ("Rhea hid the map in the drawer. "
                  "Rhea told Joss, 'The map burned in the fire.' Joss believed her. "
                  "Later, Joss found the map in the drawer.")
        # The local 9B model produced this shape on the live comparison.
        proposals = [
            {"kind": "action", "actor": "Rhea", "claim": "hid the map in the drawer",
             "topic": "the map", "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map burned in the fire", "topic": "the map",
             "evidence_numbers": [2]},
            {"kind": "belief", "actor": "Joss", "claim": "The map burned in the fire",
             "topic": "the map", "evidence_numbers": [2]},
            {"kind": "observation", "actor": "Joss", "claim": "found the map in the drawer",
             "topic": "the map", "evidence_numbers": [3]},
        ]
        accepted, rejected = ledger.validate(proposals, source)
        self.assertEqual(rejected, [])
        self.assertEqual([r["kind"] for r in accepted],
                         ["action", "assertion", "belief", "observation"])
        self.assertEqual(accepted[0]["claim"], "Rhea hid the map in the drawer")
        self.assertEqual(accepted[-1]["claim"], "Joss found the map in the drawer")
        self.assertEqual(accepted[2]["certainty"], "believes")
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "isolated", "folder": folder}
            ledger.record(project, "live-fixture", accepted)
            self.assertEqual([r["kind"] for r in ledger.character_view(project, "Joss",
                                                                        historical=True)],
                             ["belief", "observation"])

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
            human = (Path(folder)/".storyteller"/"memory"/"narrative-ledger.md").read_text()
            self.assertIn("**ASSERTION** Rhea to Joss", human)
            self.assertIn("Statements and beliefs below are attributed", human)
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
            {"kind": "state", "actor": "map", "claim": "The map burned in the fire",
             "evidence_numbers": [1]},
            {"kind": "belief", "actor": "Joss", "topic": "map state", "certainty": "believes",
             "claim": "The map was in the drawer", "evidence_numbers": [2]},
        ]
        accepted, rejected = ledger.validate(proposals, source)
        self.assertEqual([row["kind"] for row in accepted], ["assertion"])
        self.assertEqual(len(rejected), 3)

    def test_linked_belief_refuses_competing_statements(self):
        source = ("Rhea told Joss, 'The map burned.' "
                  "Rhea told Joss, 'The map was hidden.' Joss believed Rhea's claim.")
        proposals = [
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map burned", "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map was hidden", "evidence_numbers": [1]},
            {"kind": "belief", "actor": "Joss", "topic": "map state",
             "claim": "The map was hidden", "evidence_numbers": [1]},
        ]
        accepted, rejected = ledger.validate(proposals, source)
        self.assertEqual([row["kind"] for row in accepted], ["assertion", "assertion"])
        self.assertTrue(rejected)

    def test_narrator_state_is_accepted_separately_from_quoted_claim(self):
        source = "The map was intact. Rhea told Joss, 'The map burned.'"
        proposals = [
            {"kind": "state", "actor": "map", "claim": "The map was intact",
             "evidence_numbers": [1]},
            {"kind": "state", "actor": "map", "claim": "The map burned",
             "evidence_numbers": [2]},
        ]
        accepted, rejected = ledger.validate(proposals, source)
        self.assertEqual([r["claim"] for r in accepted], ["The map was intact"])
        self.assertEqual(len(rejected), 1)


if __name__ == "__main__":
    unittest.main()
