"""Conflict policy is independent of model inference and gateway transport."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

import canon_review


def fact(text):
    return dict(entity='Nera', category='appearance', fact=text,
                evidence=text, confidence='high')


def decision(relation='compatible', confidence='high', index=0):
    return dict(index=index, relation=relation, confidence=confidence)


class CanonReviewTests(unittest.TestCase):
    def test_changed_clause_cannot_be_approved_by_a_mistaken_model(self):
        old, new = fact('Nera has silver eyes.'), fact('Nera has bright blue eyes.')
        classify = Mock(return_value={'decisions': [decision()]})
        accepted, rejected = canon_review.review([new], [old], classify)
        self.assertEqual(accepted, [])
        self.assertEqual(len(rejected), 1)
        classify.assert_not_called()

    def test_revision_guard_handles_negation_without_blocking_different_attributes(self):
        self.assertTrue(canon_review.possible_revision('Beacon', 'The Beacon can open gates.', 'The Beacon cannot open gates.'))
        self.assertTrue(canon_review.possible_revision('Orin', 'Orin is alive.', 'Orin is dead.'))
        self.assertFalse(canon_review.possible_revision('Nera', 'Nera has silver eyes.', 'Nera is a navigator.'))
        self.assertFalse(canon_review.possible_revision('Nera', 'Nera has silver eyes.', 'Nera has silver hair.'))

    def test_handwritten_profile_participates_in_conflict_review(self):
        with tempfile.TemporaryDirectory() as directory:
            project = dict(folder=directory, alias='test-dump')
            profile = Path(directory) / '.storyteller/memory/characters/nera.md'
            profile.parent.mkdir(parents=True)
            profile.write_text('# Nera\n\n- Eye colour: blue.\n')
            existing = canon_review.existing_facts(project, ['Nera'])
            self.assertEqual(len(existing), 1)
            self.assertEqual(existing[0]['fact'], 'Eye colour: blue.')
            new = fact('Nera has silver eyes.')
            accepted, rejected = canon_review.review([new], existing, lambda *_: {'decisions': [decision('conflict')]})
            self.assertEqual(accepted, [])
            self.assertEqual(rejected[0]['comparisons'][0]['prior']['source_profile'], 'characters/nera.md')

    def test_conflict_is_quarantined_with_both_sources(self):
        old, new = fact('Nera has blue eyes.'), fact('Nera has silver eyes.')
        classify = Mock(return_value={'decisions': [decision('conflict')]})
        accepted, rejected = canon_review.review([new], [old], classify)
        self.assertEqual(accepted, [])
        self.assertEqual(rejected[0]['candidate'], new)
        self.assertEqual(rejected[0]['comparisons'][0]['prior'], old)
        classify.assert_not_called()

    def test_new_attribute_can_coexist(self):
        old, new = fact('Nera has blue eyes.'), fact('Nera is a navigator.')
        accepted, rejected = canon_review.review([new], [old], lambda *_: {'decisions': [decision()]})
        self.assertEqual(accepted, [new])
        self.assertEqual(rejected, [])

    def test_missing_duplicate_or_low_confidence_assessment_is_not_accepted(self):
        old, new = fact('Nera has blue eyes.'), fact('Nera is a navigator.')
        for result in ({}, {'decisions': []}, {'decisions': [decision(index=99)]},
                       {'decisions': [decision(confidence='low')]},
                       {'decisions': [decision('uncertain')]},
                       {'decisions': [decision(), decision()]}):
            with self.subTest(result=result):
                accepted, rejected = canon_review.review([new], [old], lambda *_: result)
                self.assertEqual(accepted, [])
                self.assertEqual(len(rejected), 1)

    def test_identical_fact_does_not_require_model_review(self):
        existing = fact('Nera has blue eyes.')
        classify = Mock(side_effect=AssertionError('No model request needed'))
        self.assertEqual(canon_review.review([existing], [existing], classify), ([existing], []))

    def test_candidates_in_one_import_are_compared_to_each_other(self):
        first, second = fact('Nera has blue eyes.'), fact('Nera has silver eyes.')
        accepted, rejected = canon_review.review([first, second], [], lambda *_: {'decisions': [decision('conflict')]})
        self.assertEqual(accepted, [first])
        self.assertEqual(len(rejected), 1)

    def test_all_prior_batches_must_be_reviewed(self):
        prior = [fact(f'Nera owns instrument {i}.') for i in range(25)]
        calls = []
        def classify(_, batch):
            calls.append(len(batch))
            return {'decisions': [decision(index=i) for i in range(len(batch))]}
        new = fact('Nera has silver eyes.')
        self.assertEqual(canon_review.review([new], prior, classify), ([new], []))
        self.assertEqual(calls, [12, 12, 1])


if __name__ == '__main__':
    unittest.main()
