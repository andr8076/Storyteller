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

    def test_numbering_excludes_headings_and_preserves_original_indexes(self):
        source = ['### Beacon', '- Opens blue gates.', '### Lantern', '- Glows red.']
        numbered, headings = grounding.numbered_source(source)
        self.assertNotIn('[1]', numbered)
        self.assertNotIn('[3]', numbered)
        self.assertIn('[2] - Opens blue gates.', numbered)
        self.assertIn('[4] - Glows red.', numbered)
        self.assertEqual(headings, [(3, 'Lantern', '### Lantern')])

    def test_original_heading_carries_across_chunks_but_not_siblings(self):
        _, headings = grounding.numbered_source(['### Beacon', '- Opens blue gates.'])
        source = ['- Cannot operate underwater.', '### Lantern', '- Glows red.']
        numbered, final = grounding.numbered_source(source, headings)
        self.assertIn('Source heading path: Beacon', numbered)
        self.assertEqual(grounding.ground('Beacon', 'Cannot operate underwater.', source, [1], prior_headings=headings),
                         '### Beacon\n- Cannot operate underwater.')
        self.assertIsNone(grounding.ground('Beacon', 'Glows red.', source, [3], prior_headings=headings))
        self.assertEqual(headings, [(3, 'Beacon', '### Beacon')])
        self.assertEqual(final[0][1], 'Lantern')

    def test_plain_section_label_is_context_not_a_fact(self):
        source = ['### Beacon', 'Visual Effects', '- Glows red.']
        numbered, _ = grounding.numbered_source(source)
        self.assertNotIn('[2]', numbered)
        self.assertIn('[3] - Glows red.', numbered)
        self.assertIsNone(grounding.ground('Beacon', 'Visual Effects', source, [2]))
        self.assertFalse(grounding.is_section_label('Nera has silver eyes'))
        self.assertFalse(grounding.is_section_label('- Silver Eyes'))

    def test_question_is_not_an_established_fact(self):
        question = 'Does Nera have silver eyes?'
        self.assertIsNone(grounding.ground('Nera', question, [question], [1]))

    def test_formatting_does_not_change_claim(self):
        source = ['- Nera has\n  silver eyes.']
        self.assertEqual(grounding.ground('Nera', 'Nera has silver eyes', source, [1]), source[0])
        self.assertIsNone(grounding.ground('Nera', 'Nera has blue eyes', source, [1]))


if __name__ == '__main__':
    unittest.main()
