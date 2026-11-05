"""Source-statement validation independent of gateway, model and storage."""
import unittest
import dump_grounding as grounding


class GroundingTests(unittest.TestCase):
    def test_preserves_negation_numbers_and_qualifiers(self):
        source = ['The Beacon cannot cross more than two gates.']
        self.assertEqual(grounding.ground('Beacon', source[0], source, [1]), source[0])
        for fact in ['The Beacon can cross more than two gates.',
                     'The Beacon cannot cross more than three gates.',
                     'The Beacon cannot cross gates.']:
            self.assertIsNone(grounding.ground('Beacon', fact, source, [1]))

    def test_citation_cannot_borrow_uncited_statement(self):
        source = ['Nera has silver eyes.', 'Orin has blue eyes.']
        self.assertIsNone(grounding.ground('Nera', source[0], source, [2]))
        self.assertIsNone(grounding.ground('Orin', source[0], source, [1]))

    def test_nested_heading_scope_and_numbered_titles(self):
        source = ['### 2.1 Nera', '#### Appearance', '- Has silver eyes.',
                  '### 2.2 Orin', '- Has blue eyes.']
        self.assertEqual(grounding.ground('Nera', 'Has silver eyes.', source, [3]),
                         '### 2.1 Nera\n- Has silver eyes.')
        self.assertIsNone(grounding.ground('Nera', 'Has blue eyes.', source, [5]))

    def test_mentioned_object_is_not_the_owner_of_a_heading_bullet(self):
        source = ['### Beacon', '- Produces a blue portal.']
        self.assertIsNone(grounding.ground('portal', 'Produces a blue portal.', source, [2]))
        self.assertEqual(grounding.ground('Beacon', 'Produces a blue portal.', source, [2]),
                         '### Beacon\n- Produces a blue portal.')

    def test_question_is_not_an_established_fact(self):
        question = 'Does Nera have silver eyes?'
        self.assertIsNone(grounding.ground('Nera', question, [question], [1]))

    def test_formatting_does_not_change_claim(self):
        source = ['- Nera has\n  silver eyes.']
        self.assertEqual(grounding.ground('Nera', 'Nera has silver eyes', source, [1]), source[0])
        self.assertIsNone(grounding.ground('Nera', 'Nera has blue eyes', source, [1]))


if __name__ == '__main__':
    unittest.main()
