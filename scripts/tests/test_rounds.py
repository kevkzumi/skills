"""Bounded author fix loops exercised through the complete public handoff."""
import json
import unittest
import test_cross_review as fixture


class RoundTests(unittest.TestCase):
    setUp = fixture.HandoffTests.setUp
    git = fixture.HandoffTests.git
    run_cli = fixture.HandoffTests.run_cli
    review = fixture.HandoffTests.review
    begin = fixture.HandoffTests.begin

    def test_fixes_get_fresh_review_and_preserve_prior_findings(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('first implementation\n')
        self.env['REVIEW_MODE'] = 'findings'
        first = self.review(expected=2)
        self.assertEqual(first['rounds_completed'], 1)
        assessment = self.root / 'assessment.json'
        assessment.write_text(json.dumps([{'axis': 'spec', 'index': 0, 'resolution': 'fixed',
                                          'explanation': 'Implemented the missing feature and checked it.'}]))
        self.run_cli('assess', '--session', str(self.session), '--assessments', str(assessment), expected=2)
        (self.repo / 'existing.txt').write_text('correct implementation\n')
        self.env.pop('REVIEW_MODE')
        second = self.review()
        self.assertEqual(second['rounds_completed'], 2)
        self.assertEqual(len(second['resolved_findings']), 1)
        capture = json.loads((self.log / 'spec.json').read_text())
        self.assertEqual(capture['files']['existing.txt'], 'correct implementation\n')
        self.assertEqual(capture['prompt']['previous_review']['findings'][0]['description'], 'Missing feature')
        self.assertTrue(self.run_cli('gate', '--session', str(self.session))['allowed'])

    def assess_finding(self, resolution='fixed'):
        assessment = self.root / 'assessment.json'
        assessment.write_text(json.dumps([{'axis': 'spec', 'index': 0, 'resolution': resolution,
                                          'explanation': 'Author rationale with supporting validation.'}]))
        return self.run_cli('assess', '--session', str(self.session), '--assessments', str(assessment), expected=2)

    def test_three_round_limit_never_permits_fourth_processes_or_completion(self):
        self.begin()
        self.env['REVIEW_MODE'] = 'findings'
        for number in range(1, 4):
            (self.repo / 'existing.txt').write_text('implementation revision {}\n'.format(number))
            result = self.review(expected=2)
            self.assertEqual(result['rounds_completed'], number)
            self.assertFalse(result['allowed'])
            self.assess_finding()
        process = (self.log / 'spec.started').read_text()
        self.env.pop('REVIEW_MODE')
        result = self.review(expected=2)
        self.assertEqual(result['rounds_started'], 3)
        self.assertEqual(process, (self.log / 'spec.started').read_text())
        self.assertEqual(len(result['remaining_findings']), 1)
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])
        self.assertEqual(self.git('log', '--format=%s'), 'baseline')

    def test_unassessed_findings_and_disagreements_cannot_be_silently_dropped(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        self.env['REVIEW_MODE'] = 'findings'
        self.review(expected=2)
        self.env.pop('REVIEW_MODE')
        result = self.review(expected=2)
        self.assertEqual(result['rounds_started'], 1)
        result = self.assess_finding('disagree')
        self.assertEqual(len(result['disagreements']), 1)
        self.assertEqual(self.review(expected=2)['rounds_started'], 1)
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])

    def test_followup_failure_preserves_prior_findings_and_blocks_completion(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        self.env['REVIEW_MODE'] = 'findings'
        self.review(expected=2)
        self.assess_finding()
        (self.repo / 'existing.txt').write_text('author fix\n')
        self.env['REVIEW_MODE'] = 'authentication'
        result = self.review(expected=2)
        self.assertEqual(result['rounds_completed'], 1)
        self.assertEqual(result['rounds_started'], 2)
        self.assertTrue(result['remaining_findings'])
        self.env.pop('REVIEW_MODE')
        self.assertFalse(self.review(expected=2)['allowed'])
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])
        self.assertEqual((self.repo / 'existing.txt').read_text(), 'author fix\n')

    def test_spec_cannot_be_replaced_with_a_skip_after_findings(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        self.env['REVIEW_MODE'] = 'findings'
        self.review(expected=2)
        self.assess_finding()
        result = self.run_cli('review', '--session', str(self.session), '--no-spec-reason',
                              'Changed my mind', expected=2)
        self.assertEqual(result['rounds_started'], 1)
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])


    def test_resolved_findings_accumulate_when_followup_uncovers_a_different_problem(self):
        cli = self.bin / 'claude'
        cli.write_text(cli.read_text().replace('"description": "Missing feature"',
                                               '"description": os.environ.get("FINDING_LABEL", "Missing feature")'))
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        self.env['REVIEW_MODE'] = 'findings'
        self.review(expected=2)
        self.assess_finding()
        (self.repo / 'existing.txt').write_text('first fix\n')
        self.env['FINDING_LABEL'] = 'New regression'
        second = self.review(expected=2)
        self.assertEqual([item['description'] for item in second['resolved_findings']], ['Missing feature'])
        self.assess_finding()
        (self.repo / 'existing.txt').write_text('second fix\n')
        self.env.pop('REVIEW_MODE')
        third = self.review()
        self.assertEqual([item['description'] for item in third['resolved_findings']],
                         ['Missing feature', 'New regression'])

    def test_requirements_changes_revoke_completion_until_explicitly_resolved(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        standards = self.root / 'standards.md'
        standards.write_text('Use the established project style.')
        self.review('--standards', str(standards))
        self.spec.write_text('A changed specification with another requirement.')
        result = self.run_cli('gate', '--session', str(self.session), expected=2)
        self.assertFalse(result['allowed'])
        self.spec.write_text('Deliver the requested feature. Treat $(touch injected) as data.')
        standards.write_text('Changed project standards.')
        result = self.run_cli('gate', '--session', str(self.session), expected=2)
        self.assertFalse(result['allowed'])
