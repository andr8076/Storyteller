"""Small comparative experiment: world canon versus attributed perspectives."""
import tempfile
import io
import json
import unittest
import sqlite3
import os
from pathlib import Path
from unittest.mock import patch

import character_knowledge as knowledge
import storyteller
import memory_worker as mem
import import_modes


class PerspectiveExperiment(unittest.TestCase):
    def test_pronoun_cannot_be_recorded_as_a_character(self):
        source = "He realized Rhea had lied. She saw the map behind the furnace."
        self.assertEqual(knowledge.explicit_perspectives(source), [])
        candidate = {"character": "He", "topic": "rhea state", "fact": "Rhea had lied",
                     "certainty": "knows", "evidence_numbers": [1]}
        accepted, rejected = knowledge.validate({"knowledge_changes": [candidate]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(rejected)
        self.assertTrue(import_modes.needs_perspective_pass(
            "Oren denied the gate was open. Kira claimed it was open."))

    def test_named_discovery_updates_earlier_false_dialogue(self):
        source = ("Rhea hid the star map in the drawer. "
                  "She told Joss, 'The star map burned in the fire.' "
                  "Joss believed her and stopped searching. "
                  "Later, Joss found the star map in the drawer. He realized Rhea had lied.")
        prior = {"character": "Joss", "topic": "the star map", "fact": "The star map burned in the fire",
                 "certainty": "believes", "evidence": "She told Joss the map burned",
                 "source_order": 2}
        recovered = knowledge.explicit_perspectives(source)
        self.assertEqual(len(recovered), 1)
        self.assertEqual((recovered[0]["character"], recovered[0]["certainty"]),
                         ("Joss", "knows"))
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "deception", "folder": folder}
            knowledge.record(project, "scene-1", knowledge.combine([prior], recovered))
            current = knowledge.current(project)
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["certainty"], "knows")
            history = knowledge.relevant(project, "What did Joss believe about the map before?",
                                          lambda s: set(s.lower().split()))
            self.assertIn("Earlier, Joss believes: The star map burned in the fire", history)

    def test_denial_then_observation_preserves_changing_knowledge(self):
        source = ("Kira said the north gate was open. "
                  "Oren denied that the north gate was open. "
                  "In fact, the north gate was sealed. "
                  "Later, Oren saw the sealed north gate.")
        suggestions = {"knowledge_changes": [
            {"character": "Oren", "topic": "north gate status", "fact": "the north gate was open",
             "certainty": "denies", "evidence_numbers": [2]},
            {"character": "Oren", "topic": "north gate status", "fact": "the north gate was sealed",
             "certainty": "knows", "evidence_numbers": [4]}]}
        entries, errors = knowledge.validate(suggestions, source)
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "gate", "folder": folder}
            self.assertEqual(knowledge.record(project, "scene-1", entries), 2)
            self.assertEqual(knowledge.current(project)[0]["certainty"], "knows")
            historical = knowledge.relevant(project, "What did Oren believe about the gate before?",
                                            lambda s: set(s.lower().split()))
            self.assertIn("Earlier, Oren denies: the north gate was open", historical)

    def test_live_style_dialogue_merges_model_and_literal_history(self):
        scene = ("Lena told Dorian, 'The brass key is in the attic.' "
                 "Dorian believed the brass key was in the attic. "
                 "Later, Dorian discovered the brass key was in the cellar.")
        raw = {"knowledge_changes": [
            {"character": "Dorian", "topic": "location of the brass key",
             "fact": "The brass key is in the attic.", "certainty": "believes",
             "evidence_numbers": [1]},
            {"character": "Dorian", "topic": "location of the brass key",
             "fact": "The brass key is in the cellar.", "certainty": "knows",
             "evidence_numbers": [2]}]}
        model, errors = knowledge.validate(raw, scene)
        self.assertEqual(errors, [])
        literal = knowledge.explicit_perspectives(scene)
        self.assertEqual([row["topic"] for row in literal], ["key location", "key location"])
        merged = knowledge.combine(model, literal)
        self.assertEqual([(row["certainty"], row["fact"]) for row in merged],
                         [("believes", "The brass key was in the attic"),
                          ("knows", "The brass key was in the cellar")])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "dialogue", "folder": folder}
            self.assertEqual(knowledge.record(project, "scene-1", merged), 2)
            self.assertEqual(len(knowledge.current(project)), 1)
            self.assertEqual(knowledge.current(project)[0]["certainty"], "knows")
            history = knowledge.relevant(project, "What did Dorian believe about the brass key before?",
                                          lambda s: set(s.lower().split()))
            self.assertIn("Earlier, Dorian believes: The brass key was in the attic", history)

    def test_topic_reconciliation_keeps_distinct_objects_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "keys", "folder": folder}
            common = {"character": "Dorian", "certainty": "knows", "evidence": "source"}
            rows = [
                {**common, "topic": "brass key location", "fact": "The brass key was in the cellar"},
                {**common, "topic": "silver key location", "fact": "The silver key was in the loft"},
                {**common, "topic": "location of the brass key", "fact": "The brass key was in the chest"},
            ]
            for i, row in enumerate(rows):
                knowledge.record(project, f"scene-{i}", [row])
            current = knowledge.current(project)
            self.assertEqual(len(current), 2)
            self.assertEqual({row["fact"] for row in current},
                             {"The brass key was in the chest", "The silver key was in the loft"})

    def test_scene_actions_cannot_become_timeless_identity(self):
        scene = ("Demi was being tracked by the Director. "
                 "Amos removed the power cell from his pulse rifle. "
                 "Amos inspected its contacts. Demi is the ship's AI.")
        proposals = [
            ("Demi", "Demi is a character who is being tracked by the Director", 1),
            ("Amos", "Amos is the character who removed the power cell", 2),
            ("Amos", "Amos inspected its contacts", 3),
            ("Demi", "Demi is the ship's AI", 4),
        ]
        response = {"changes": [{"entity": name, "category": "identity", "fact": fact,
                    "evidence_numbers": [number], "confidence": "high"}
                    for name, fact, number in proposals]}
        canon, rejected, _ = import_modes.validate_records(response, scene, scene, "catchup")
        self.assertFalse(any(row["category"] == "identity" and
                         row["entity"] == "Amos" for row in canon))
        self.assertEqual(sum("transient action" in row["reason"] for row in rejected), 3)
        self.assertTrue(mem.durable_identity_statement("Demi is the ship's AI"))

    def test_import_splits_truncated_model_json_preserving_original_citations(self):
        requests = []
        def fake_request(req, timeout):
            requests.append(json.loads(req.data))
            content = ('{"changes":[' if len(requests) == 1 else
                       json.dumps({"changes": [{"evidence_numbers": [1 if len(requests) == 2 else 4]}],
                                   "summary": "ok"}))
            response = {"choices": [{"message": {"content": content},
                                     "finish_reason": "length" if len(requests) == 1 else "stop"}]}
            return io.BytesIO(json.dumps(response).encode())
        with patch.object(import_modes, "urlopen", side_effect=fake_request):
            result = import_modes.request_model("Extract", "[1] Ivo found a key.\n"
                "[2] He opened the door.\n[3] Mara watched.\n[4] She went home.")
        self.assertEqual([x["evidence_numbers"] for x in result["changes"]], [[1], [4]])
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request["max_tokens"] == 1350 for request in requests))
        self.assertIn("[2] He opened", requests[2]["messages"][1]["content"])
        self.assertIn("[4] She went", requests[2]["messages"][1]["content"])

    def test_suspicion_then_discovery_preserves_history_and_latest_state(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            source1 = "Ivo suspected the vault was beneath the lake."
            source2 = "Ivo discovered the vault was beneath the lake."
            claim = {"character": "Ivo", "topic": "vault location",
                     "fact": "The vault was beneath the lake", "evidence_numbers": [1]}
            first, errors = knowledge.validate({"knowledge_changes": [
                {**claim, "certainty": "suspects"}]}, source1)
            self.assertEqual(errors, [])
            self.assertEqual(knowledge.record(project, "scene-1", first), 1)
            self.assertEqual(knowledge.record(project, "scene-1", first), 0)
            prompt = [{"role": "user", "content": "What does Ivo think about the vault?"}]
            injection, _ = storyteller.select_memory(project, prompt)
            self.assertIn("Ivo suspects: The vault was beneath the lake", injection)
            second, errors = knowledge.validate({"knowledge_changes": [
                {**claim, "certainty": "knows"}]}, source2)
            self.assertEqual(errors, [])
            self.assertEqual(knowledge.record(project, "scene-2", second), 1)
            injection, _ = storyteller.select_memory(project, prompt)
            self.assertIn("Ivo knows: The vault was beneath the lake", injection)
            self.assertNotIn("Ivo suspects:", injection)
            self.assertIn("Ivo — vault location", (Path(folder) / ".storyteller" /
                "memory" / "character-knowledge.md").read_text())
            with knowledge.database(project) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 2)

    def test_historical_question_recovers_earlier_belief_with_source(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            knowledge.record(project, "scene-before", [{"character": "Ivo",
                "topic": "vault location", "fact": "The vault was beneath the lake",
                "certainty": "suspects", "evidence": "Ivo suspected the vault was beneath the lake."}])
            knowledge.record(project, "scene-after", [{"character": "Ivo",
                "topic": "vault location", "fact": "The vault was beneath the lake",
                "certainty": "knows", "evidence": "Ivo discovered the vault was beneath the lake."}])
            old_query = [{"role": "user", "content": "What did Ivo believe before he found the vault?"}]
            historical, _ = storyteller.select_memory(project, old_query)
            self.assertIn("Earlier, Ivo suspects: The vault was beneath the lake", historical)
            self.assertIn("scene-before", historical)
            self.assertIn("Ivo knows: The vault was beneath the lake", historical)
            ordinary, _ = storyteller.select_memory(
                project, [{"role": "user", "content": "Continue with Ivo at the vault."}])
            self.assertNotIn("Earlier, Ivo suspects", ordinary)
            self.assertIn("Ivo knows:", ordinary)

    def test_unsupported_perspective_and_other_project_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as other:
            project = {"alias": "first", "folder": folder}
            suggestion = {"character": "Ivo", "topic": "vault location",
                          "fact": "The vault was beneath the lake", "certainty": "knows",
                          "evidence_numbers": [1]}
            accepted, errors = knowledge.validate({"knowledge_changes": [suggestion]},
                                                   "Ivo walked beside the lake.")
            self.assertEqual(accepted, [])
            self.assertTrue(errors)
            self.assertEqual(storyteller.select_memory(
                {"alias": "other", "folder": other},
                [{"role": "user", "content": "Ivo vault"}]), ("", []))

    def test_one_model_response_keeps_suspicion_out_of_world_canon(self):
        text = "Ivo suspected the vault was beneath the lake."
        response = {"changes": [{"entity": "Vault", "category": "place",
                    "fact": "The vault was beneath the lake", "evidence_numbers": [1],
                    "confidence": "high"}],
                    "knowledge_changes": [{"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence_numbers": [1]}]}
        with patch.object(mem, "request_json", return_value=response) as request:
            canon, _, perspectives = mem.extract(text, include_knowledge=True)
        request.assert_called_once()
        self.assertEqual(canon, [])
        self.assertEqual(len(perspectives), 1)
        imported, _, _ = import_modes.validate_records(response, text, text, "catchup")
        self.assertEqual(imported, [])

    def test_wrong_belief_requires_source_correction(self):
        source = "Ivo believed the gate was open."
        row = {"character": "Ivo", "topic": "gate state", "fact": "The gate was open",
               "certainty": "believes_wrongly", "evidence_numbers": [1]}
        accepted, errors = knowledge.validate({"knowledge_changes": [row]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(errors)

    def test_correction_is_not_the_belief_and_literal_fallback_recovers_it(self):
        source = "Mara wrongly believed the gate was open, but in fact it was sealed."
        mistaken = {"character": "Mara", "topic": "gate state", "fact": "The gate was sealed",
                    "certainty": "believes_wrongly", "evidence_numbers": [1]}
        accepted, errors = knowledge.validate({"knowledge_changes": [mistaken]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(errors)
        recovered = knowledge.explicit_perspectives(source)
        self.assertEqual(recovered[0]["fact"], "The gate was open")
        self.assertEqual(recovered[0]["certainty"], "believes_wrongly")

    def test_missing_model_knowledge_recovers_suspicion_and_discovery(self):
        suspicion = knowledge.explicit_perspectives("Ivo suspected the vault was beneath the lake.")
        discovery = knowledge.explicit_perspectives("Ivo discovered the vault was beneath the lake.")
        self.assertEqual((suspicion[0]["topic"], suspicion[0]["certainty"]),
                         ("vault location", "suspects"))
        self.assertEqual((discovery[0]["topic"], discovery[0]["certainty"]),
                         ("vault location", "knows"))

    def test_adverbial_scene_prefix_recovers_proposition_not_mental_verb(self):
        scene = ("At the observatory, Nera suspected the signal came from the tower. "
                 "Later, Nera discovered the signal came from the tower.")
        rows = knowledge.explicit_perspectives(scene)
        self.assertEqual([(r["topic"], r["certainty"]) for r in rows],
                         [("signal source", "suspects"), ("signal source", "knows")])
        self.assertTrue(all(r["fact"] == "The signal came from the tower" for r in rows))

    def test_model_identity_misclassification_does_not_pollute_canon(self):
        scene = "Nera discovered the signal came from the tower."
        response = {"changes": [{"entity": "Nera", "category": "identity",
                    "fact": "Nera discovered the signal came from the tower",
                    "evidence_numbers": [1], "confidence": "high"}]}
        with patch.object(mem, "request_json", return_value=response):
            canon, _, rows = mem.extract(scene, include_knowledge=True)
        self.assertEqual(canon, [])
        self.assertEqual(rows[0]["fact"], "The signal came from the tower")

    def test_import_rejects_confirmed_knowledge_as_identity(self):
        source = "Later, Nera discovered the signal came from the tower."
        response = {"changes": [{"entity": "Nera", "category": "identity",
                    "fact": "Nera later confirmed the signal came from the tower",
                    "evidence_numbers": [1], "confidence": "high"}]}
        canon, rejected, _ = import_modes.validate_records(response, source, source, "catchup")
        self.assertEqual(canon, [])
        self.assertTrue(rejected)

    def test_literal_fallback_skips_pronoun_and_separates_other_character(self):
        deception = ("She told him the key was in the attic. "
                     "Dorian believed her and searched the attic.")
        self.assertEqual(knowledge.explicit_perspectives(deception), [])
        contrast = "Ari suspected the hatch was rigged, but Cyra believed it was safe."
        rows = knowledge.explicit_perspectives(contrast)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fact"], "The hatch was rigged")
        self.assertEqual(rows[0]["character"], "Ari")

    def test_observation_can_ground_character_knowledge(self):
        scene = "Joss saw the map behind the furnace and knew she had deceived him."
        row = {"character": "Joss", "topic": "map location",
               "fact": "The map is behind the furnace", "certainty": "knows",
               "evidence_numbers": [1]}
        accepted, rejected = knowledge.validate({"knowledge_changes": [row]}, scene)
        self.assertEqual(rejected, [])
        self.assertEqual(len(accepted), 1)

    def test_named_observation_recovers_location_after_false_dialogue(self):
        scene = ("Rhea hid the map behind the furnace. "
                 "She told Joss, 'The map burned in the fire.' Joss believed her and stopped searching. "
                 "Later, Joss saw the map behind the furnace and knew she had deceived him.")
        raw = {"knowledge_changes": [
            {"character": "Joss", "topic": "the map's location", "fact": "The map burned in the fire",
             "certainty": "believes", "evidence_numbers": [2]},
            {"character": "Joss", "topic": "Rhea's statement about the map",
             "fact": "Rhea deceived him about the map's location",
             "certainty": "knows", "evidence_numbers": [3]}]}
        model, rejected = knowledge.validate(raw, scene)
        combined = knowledge.combine(model, knowledge.explicit_perspectives(scene))
        self.assertTrue(rejected)
        self.assertEqual([row["certainty"] for row in combined], ["believes", "knows"])
        self.assertIn("map was behind the furnace", combined[-1]["fact"])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            knowledge.record(project, "map-scene", combined)
            current = knowledge.current(project)
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["certainty"], "knows")

    def test_adjacent_pronoun_discovery_links_to_single_named_subject(self):
        source = ("Lena hid the key in the cellar. She told him, 'The key is in the attic.' "
                  "Dorian believed her and searched the attic. Later, he found the key in the cellar.")
        numbered = mem.split_sentences(source)
        self.assertEqual(len(numbered), 3)
        response = {"knowledge_changes": [
            {"character": "Dorian", "topic": "key location", "fact": "The key is in the attic",
             "certainty": "believes", "evidence_numbers": [2]},
            {"character": "Dorian", "topic": "key location", "fact": "The key is in the cellar",
             "certainty": "knows", "evidence_numbers": [3]}]}
        accepted, rejected = knowledge.validate(response, source)
        self.assertEqual(rejected, [])
        self.assertEqual([r["certainty"] for r in accepted], ["believes", "knows"])
        self.assertIn("Dorian believed her", accepted[1]["evidence"])
        self.assertIn("he found the key", accepted[1]["evidence"])

    def test_pronoun_with_competing_person_is_not_attributed(self):
        response = {"knowledge_changes": [{"character": "Dorian", "topic": "key location",
            "fact": "The key is in the cellar", "certainty": "knows", "evidence_numbers": [2]}]}
        for prior in ("Dorian met Marcus.", "Dorian met the guard.",
                      "The guard saw Dorian."):
            accepted, rejected = knowledge.validate(response,
                prior + " He found the key in the cellar.")
            self.assertEqual(accepted, [], prior)
            self.assertTrue(rejected)

    def test_belief_cannot_borrow_a_different_sentence_truth(self):
        source = ("Lena hid the key in the cellar. "
                  "She told Dorian, 'The key is in the attic.' Dorian believed her.")
        wrong = {"character": "Dorian", "topic": "key location",
                 "fact": "The key is in the cellar", "certainty": "believes",
                 "evidence_numbers": [1, 2]}
        accepted, rejected = knowledge.validate({"knowledge_changes": [wrong]}, source)
        self.assertEqual(accepted, [])
        self.assertTrue(rejected)

    def test_model_output_order_cannot_rewind_current_belief(self):
        source = ("Ivo suspected the vault was beneath the lake. "
                  "Later, Ivo discovered the vault was beneath the lake.")
        response = {"knowledge_changes": [
            {"character": "Ivo", "topic": "vault location", "fact": "The vault was beneath the lake",
             "certainty": "knows", "evidence_numbers": [2]},
            {"character": "Ivo", "topic": "vault location", "fact": "The vault was beneath the lake",
             "certainty": "suspects", "evidence_numbers": [1]}]}
        accepted, rejected = knowledge.validate(response, source)
        self.assertEqual(rejected, [])
        self.assertEqual([item["certainty"] for item in accepted], ["suspects", "knows"])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            knowledge.record(project, "scene-1", accepted)
            self.assertEqual(knowledge.current(project)[0]["certainty"], "knows")

    def test_dialogue_belief_then_pronoun_discovery_survive_combination(self):
        source = ("Lena put the brass key in the cellar before Dorian entered. "
                  "She told him, 'The key is in the attic.' Dorian believed her and searched the attic. "
                  "Later, he found the key in the cellar and realized she had lied.")
        rows = {"knowledge_changes": [
            {"character": "Lena", "topic": "key location", "fact": "The key is in the cellar",
             "certainty": "knows", "evidence_numbers": [1]},
            {"character": "Dorian", "topic": "key location", "fact": "The key is in the attic",
             "certainty": "believes", "evidence_numbers": [2]},
            {"character": "Dorian", "topic": "key location", "fact": "The key is in the cellar",
             "certainty": "knows", "evidence_numbers": [3]}]}
        model, rejected = knowledge.validate(rows, source)
        combined = knowledge.combine(model, knowledge.explicit_perspectives(source))
        self.assertEqual(len(rejected), 1)  # Lena's placement does not prove her knowledge.
        self.assertEqual([r["certainty"] for r in combined], ["believes", "knows"])
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            self.assertEqual(knowledge.record(project, "catchup-1", combined), 2)
            state = knowledge.current(project)
            self.assertEqual(len(state), 1)
            self.assertEqual((state[0]["certainty"], state[0]["fact"]),
                             ("knows", "The key is in the cellar"))
            injection, _ = storyteller.select_memory(
                project, [{"role": "user", "content": "Where does Dorian think the key is now?"}])
            self.assertIn("Dorian knows: The key is in the cellar", injection)
            self.assertNotIn("Dorian believes:", injection)

    def test_selective_local_perspective_pass_in_catchup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "chapter.txt"
            source.write_text("Rhea hid the map behind the furnace. She told Joss, 'The map burned.' "
                              "Joss believed her. Later, Joss saw the map behind the furnace.")
            project = {"alias": "fiction", "name": "Fiction", "folder": str(root / "project")}
            focused = {"knowledge_changes": [
                {"character": "Joss", "topic": "map location", "fact": "The map burned",
                 "certainty": "believes", "evidence_numbers": [2]},
                {"character": "Joss", "topic": "map location", "fact": "The map is behind the furnace",
                 "certainty": "knows", "evidence_numbers": [3]}]}
            endpoint = "http://127.0.0.1:19999/v1/chat/completions"
            with (patch.dict(os.environ, {"STORYTELLER_PERSPECTIVE_ENDPOINT": endpoint}),
                  patch.object(mem, "is_healthy", return_value=True),
                  patch.object(import_modes, "request_model", return_value={"changes": [], "summary": "", "knowledge_changes": []}),
                  patch.object(import_modes, "request_perspectives", return_value=focused) as extra):
                status = import_modes.run_import(project, "catchup", source)
            self.assertEqual(status["state"], "complete")
            extra.assert_called_once()
            self.assertEqual([x["certainty"] for x in knowledge.current(project)], ["knows"])
            self.assertEqual(knowledge.current(project)[0]["fact"], "The map was behind the furnace")
            self.assertEqual(source.read_text().count("Rhea hid"), 1)

    def test_perspective_endpoint_rejects_remote_and_skips_plain_dump(self):
        with patch.dict(os.environ, {"STORYTELLER_PERSPECTIVE_ENDPOINT":
                                      "https://example.com/v1/chat/completions"}):
            with self.assertRaises(ValueError):
                import_modes.perspective_endpoint()
        self.assertFalse(import_modes.needs_perspective_pass("The ship has a single reactor."))
        self.assertTrue(import_modes.needs_perspective_pass("Joss believed the map burned."))

    def test_unavailable_optional_pass_keeps_literal_catchup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "chapter.txt"
            source.write_text("Ivo suspected the vault was beneath the lake.")
            project = {"alias": "fiction", "name": "Fiction", "folder": str(root / "project")}
            with (patch.dict(os.environ, {"STORYTELLER_PERSPECTIVE_ENDPOINT":
                                      "http://127.0.0.1:19999/v1/chat/completions"}),
                  patch.object(mem, "is_healthy", return_value=True),
                  patch.object(import_modes, "request_model", return_value={"changes": [], "summary": ""}),
                  patch.object(import_modes, "request_perspectives", side_effect=TimeoutError)):
                status = import_modes.run_import(project, "catchup", source)
            self.assertEqual(status["state"], "complete")
            self.assertEqual(knowledge.current(project)[0]["certainty"], "suspects")

    def test_topic_variants_link_without_merging_another_belief(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            base = {"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence": "Ivo suspected the vault was beneath the lake."}
            knowledge.record(project, "scene-1", [base])
            knowledge.record(project, "scene-2", [
                {**base, "topic": "location of vault", "certainty": "knows"}])
            knowledge.record(project, "scene-3", [
                {**base, "topic": "vault ownership", "fact": "Ivo owns the vault",
                 "evidence": "Ivo learned he owns the vault.", "certainty": "knows"}])
            states = knowledge.current(project)
            self.assertEqual(len(states), 2)
            self.assertEqual({r["topic"] for r in states}, {"vault location", "vault ownership"})

    def test_dump_baseline_cannot_rewind_later_story_knowledge(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            base = {"character": "Ivo", "topic": "vault location",
                    "fact": "The vault was beneath the lake", "certainty": "suspects",
                    "evidence": "Ivo suspected the vault was beneath the lake."}
            knowledge.record(project, "dump-1", [base], timeline=False)
            knowledge.record(project, "scene-1", [{**base, "certainty": "knows"}])
            knowledge.record(project, "dump-2", [base], timeline=False)
            self.assertEqual(knowledge.current(project)[0]["certainty"], "knows")
            with knowledge.database(project) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 3)

    def test_existing_experimental_ledger_reads_and_migrates_additively(self):
        with tempfile.TemporaryDirectory() as folder:
            project = {"alias": "fiction", "folder": folder}
            root = Path(folder) / ".storyteller" / "memory"
            root.mkdir(parents=True)
            with sqlite3.connect(root / "character-knowledge.sqlite3") as db:
                db.execute("""CREATE TABLE knowledge (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    character TEXT NOT NULL, topic TEXT NOT NULL, fact TEXT NOT NULL,
                    certainty TEXT NOT NULL, evidence TEXT NOT NULL, source_id TEXT NOT NULL)""")
                db.execute("INSERT INTO knowledge (id,character,topic,fact,certainty,evidence,source_id) "
                           "VALUES ('old','Ivo','gate state','The gate is open','suspects',"
                           "'Ivo suspected the gate is open.','old-scene')")
            self.assertEqual(knowledge.current(project)[0]["fact"], "The gate is open")
            with knowledge.database(project) as db:
                self.assertIn("scope", {row[1] for row in db.execute("PRAGMA table_info(knowledge)")})
            self.assertEqual(knowledge.current(project)[0]["fact"], "The gate is open")


if __name__ == "__main__":
    unittest.main()
