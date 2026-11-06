"""Corrections change the current view without losing evidence or unrelated facts."""
import json
from pathlib import Path
import tempfile
import unittest

import character_knowledge
import narrative_ledger as ledger
import narrative_projection as projection
from experiments.compare_narrative_memory import projection_probe


def episode(claim, *, actor="Oren", certainty="knows", order=2, topic="flute"):
    return {"kind": "observation" if certainty == "knows" else "belief",
            "actor": actor, "recipient": "", "claim": claim, "topic": topic,
            "certainty": certainty, "source_id": "scene", "source_order": order,
            "evidence": claim, "start": 0, "end": len(claim)}


class NarrativeProjectionTests(unittest.TestCase):
    def test_recorded_correction_becomes_current_with_evidence_link(self):
        probe = projection_probe()
        self.assertTrue(probe["passed"])
        entries, rejected = ledger.validate(probe["proposals"], probe["source"])
        self.assertEqual(rejected, [])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "correction", "folder": folder}
            self.assertEqual(ledger.record(project, "scene", entries), 2)
            self.assertEqual(ledger.record(project, "scene", entries), 0)
            history = ledger.character_view(project, "Oren", historical=True)
            self.assertEqual([r["status"] for r in history], ["historical", "current"])
            self.assertEqual(history[0]["superseded_by"], 1)
            self.assertEqual(history[1]["supersedes"], [0])
            self.assertIn("never been destroyed", history[1]["evidence"])
            self.assertEqual(len(ledger.timeline(project)), 2)
            self.assertNotIn("status", ledger.timeline(project)[0])

    def test_topic_labels_alone_cannot_retract_unrelated_knowledge(self):
        prior = episode("The flute was destroyed", certainty="believes", order=1)
        cases = [
            episode("The flute was silver"),
            episode("The flute was inside the cabinet"),
            episode("The drum was never destroyed"),
            episode("The flute was never destroyed", actor="Sera"),
            episode("The flute was never destroyed", certainty="suspects"),
            episode("The flute was never destroyed", order=1),
            episode("The flute was not destroyed yesterday"),
            episode("The flute was not only destroyed but replaced"),
        ]
        for incoming in cases:
            with self.subTest(incoming=incoming):
                view = projection.project([prior, incoming])
                self.assertEqual([r["status"] for r in view], ["current", "current"])

    def test_later_corrections_across_sources_keep_complete_history(self):
        rows = [episode("The north gate was open", order=1, topic="gate access"),
                episode("The north gate was not open", topic="gate condition"),
                dict(episode("The north gate is open", order=1, topic="gate state"),
                     source_id="later-scene")]
        view = projection.project(rows)
        self.assertEqual([r["status"] for r in view], ["historical", "historical", "current"])
        self.assertEqual([r["superseded_by"] for r in view], [1, 2, None])
        self.assertTrue(all("status" not in row for row in rows))

    def test_captured_model_fragment_is_replaced_by_complete_validated_fact(self):
        fixture = json.loads((Path(__file__).parents[1] / "experiments" / "results" /
                              "narrative_memory_2026-11-06.json").read_text())
        text = fixture["source"]
        broad, errors = ledger.validate(fixture["calls"]["episodes"]["raw"], text)
        self.assertEqual(errors, [])
        focused, _ = character_knowledge.validate(fixture["calls"]["focused"]["raw"], text)
        perspectives = character_knowledge.combine(focused, character_knowledge.explicit_perspectives(text))
        merged = ledger.combine_perspectives(broad, perspectives, text)
        observations = [r for r in merged if r["actor"] == "Oren" and r["kind"] == "observation"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["claim"], "The flute was inside the cedar cabinet")
        self.assertEqual(text[observations[0]["start"]:observations[0]["end"]],
                         observations[0]["evidence"])
        self.assertEqual(len(merged), 6)
        self.assertEqual(ledger.combine_perspectives(merged, perspectives, text), merged)
        self.assertEqual(broad[-1]["claim"], "the flute inside the cedar cabinet")

    def test_equivalence_preserves_details_negation_and_word_order(self):
        original = episode("The flute was inside the cedar cabinet")
        for claim in ("The flute was not inside the cedar cabinet",
                      "The flute was near the cedar cabinet",
                      "The flute was inside the oak cabinet",
                      "The cedar cabinet was inside the flute"):
            with self.subTest(claim=claim):
                self.assertFalse(projection.equivalent(original, episode(claim)))
        self.assertTrue(projection.equivalent(original, episode("the flute inside the cedar cabinet")))
        self.assertTrue(projection.prefer_complete(episode("the flute inside the cedar cabinet"), original))
        self.assertFalse(projection.prefer_complete(original, episode("the flute inside the cedar cabinet")))


if __name__ == "__main__":
    unittest.main()
