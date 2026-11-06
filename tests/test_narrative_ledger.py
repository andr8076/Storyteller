"""A reversible comparison of event-first records against current profiles."""
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import character_knowledge
import narrative_ledger as ledger
import import_modes
import memory_worker as mem


class NarrativeLedgerExperiment(unittest.TestCase):
    def test_existing_validated_perspective_fills_missing_belief_and_witness(self):
        source = ("Mira watched Rhea place the blue ledger under the floorboard. "
                  "Rhea told Joss, 'The ledger burned.' Later, Joss believed Rhea's claim.")
        proposals = [
            {"kind": "action", "actor": "Rhea",
             "claim": "placed the blue ledger under the floorboard",
             "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The ledger burned", "evidence_numbers": [2]},
        ]
        episodes, rejected = ledger.validate(proposals, source)
        self.assertEqual(rejected, [])
        focused = {"knowledge_changes": [{"character": "Mira",
                   "topic": "ledger placement", "fact": "Rhea placed the blue ledger under the floorboard",
                   "certainty": "knows", "evidence_numbers": [1]}]}
        perspective, rejected = character_knowledge.validate(focused, source)
        self.assertEqual(rejected, [])
        merged = ledger.combine_perspectives(episodes, perspective, source)
        self.assertEqual([r["kind"] for r in merged],
                         ["action", "observation", "assertion"])
        self.assertEqual(merged[1]["evidence"], source.split(". ")[0] + ".")
        self.assertEqual(ledger.combine_perspectives(merged, perspective, source), merged)
        # Shared nouns in one sentence do not erase a different proposition.
        other = dict(perspective[0], fact="Rhea burned the blue ledger under the floorboard")
        self.assertEqual(len(ledger.combine_perspectives(merged, [other], source)),
                         len(merged) + 1)

    def test_opt_in_shadow_catchup_keeps_existing_memory_authoritative(self):
        source_text = ("Rhea hid the map in the drawer. "
                       "Rhea told Joss, 'The map burned in the fire.' "
                       "Joss believed her. Later, Joss found the map in the drawer.")
        proposals = [
            {"kind": "action", "actor": "Rhea", "claim": "hid the map in the drawer",
             "evidence_numbers": [1]},
            {"kind": "assertion", "actor": "Rhea", "recipient": "Joss",
             "claim": "The map burned in the fire", "evidence_numbers": [2]},
            {"kind": "belief", "actor": "Joss", "claim": "The map burned in the fire",
             "evidence_numbers": [2]},
            {"kind": "observation", "actor": "Joss", "claim": "found the map in the drawer",
             "evidence_numbers": [3]},
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "chapter.txt"
            source.write_text(source_text)
            project = {"alias": "ledger-test", "name": "Ledger test",
                       "folder": str(root / "project")}
            endpoint = "http://127.0.0.1:18986/v1/chat/completions"
            with (patch.dict(os.environ, {"STORYTELLER_LEDGER_SHADOW_ENDPOINT": endpoint,
                                       "STORYTELLER_PERSPECTIVE_ENDPOINT": ""}),
                  patch.object(mem, "is_healthy", return_value=True),
                  patch.object(import_modes, "request_model", return_value={
                      "changes": [], "summary": "", "knowledge_changes": []}),
                  patch.object(ledger, "propose", return_value=proposals) as propose):
                status = import_modes.run_import(project, "catchup", source)
                again = import_modes.run_import(project, "catchup", source)
            self.assertEqual(status["state"], "complete")
            self.assertEqual(again["state"], "complete")
            propose.assert_called_once_with(source_text, endpoint)
            self.assertEqual([r["kind"] for r in ledger.timeline(project)],
                             ["action", "assertion", "belief", "observation"])
            self.assertEqual([r["kind"] for r in ledger.character_view(
                project, "Joss", historical=True)], ["belief", "observation"])
            self.assertEqual(len(character_knowledge.current(project)), 1)
            self.assertEqual(source.read_text(), source_text)

    def test_shadow_failure_does_not_fail_completed_catchup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "chapter.txt"
            source.write_text("Ivo suspected the vault was beneath the lake.")
            project = {"alias": "ledger-test", "name": "Ledger test",
                       "folder": str(root / "project")}
            with (patch.dict(os.environ, {
                      "STORYTELLER_LEDGER_SHADOW_ENDPOINT":
                      "http://127.0.0.1:18986/v1/chat/completions",
                      "STORYTELLER_PERSPECTIVE_ENDPOINT": ""}),
                  patch.object(mem, "is_healthy", return_value=True),
                  patch.object(import_modes, "request_model", return_value={
                      "changes": [], "summary": "", "knowledge_changes": []}),
                  patch.object(ledger, "propose", side_effect=TimeoutError)):
                result = import_modes.run_import(project, "catchup", source)
            self.assertEqual(result["state"], "complete")
            self.assertEqual(character_knowledge.current(project)[0]["certainty"],
                             "suspects")

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
