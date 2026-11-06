"""Full-spec handoff through the beta entrypoint, with real Git and stand-in CLIs."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_cross_review as fixture
import test_providers as providers


ENTRY = Path(__file__).resolve().parents[2] / 'skills/in-progress/implement-spec-cross-review/scripts/review.py'


class SpecHandoffTests(unittest.TestCase):
    setUp = fixture.HandoffTests.setUp
    git = fixture.HandoffTests.git
    begin = fixture.HandoffTests.begin
    review = fixture.HandoffTests.review
    use_claude_author = providers.ProviderHandoffTests.use_claude_author

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ENTRY), *args], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_full_spec_cannot_complete_with_a_missing_parent_spec(self):
        self.begin()
        (self.repo / 'existing.txt').write_text('implementation\n')
        self.env['EXPECT_PAIR'] = '0'
        result = self.run_cli('review', '--session', str(self.session), '--no-spec-reason',
                              'No parent spec was found', expected=2)
        self.assertIn('parent spec', result['error'])
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])
        self.assertFalse(list(self.log.glob('*.started')))

    def assemble_issues(self):
        self.git('switch', '-c', 'integration')
        self.baseline = self.git('rev-parse', 'HEAD')
        self.spec.write_text('Parent #1: combine A and B to produce 5.\n'
                             'Issue A: supply value 2.\nIssue B: independently supply value 3.\n'
                             'Issue C: depends on A and B; publish their sum.\n')
        (self.repo / 'user-notes.txt').write_text('unrelated unfinished work\n')
        self.run_cli('begin', '--repo', str(self.repo), '--session', str(self.session),
                     '--baseline', self.baseline, '--path', 'issue-a.txt', '--path', 'issue-b.txt',
                     '--path', 'issue-c.txt', '--path', 'regression.txt')
        self.worktrees = {}
        # A and B start from the same frontier; C starts only after both merge.
        for name in ('a', 'b'):
            directory = self.root / ('implement-' + name)
            self.git('worktree', 'add', '-b', 'issue-' + name, str(directory), 'integration')
            self.worktrees[name] = directory
        for name, value in (('a', '2\n'), ('b', '3\n'), ('c', '4\n')):
            if name == 'c':
                directory = self.root / 'implement-c'
                self.git('worktree', 'add', '-b', 'issue-c', str(directory), 'integration')
                self.worktrees[name] = directory
                self.assertEqual((directory / 'issue-a.txt').read_text(), '2\n')
                self.assertEqual((directory / 'issue-b.txt').read_text(), '3\n')
            directory = self.worktrees[name]
            (directory / ('issue-' + name + '.txt')).write_text(value)
            self.git('-C', str(directory), 'add', '.')
            self.git('-C', str(directory), 'commit', '-qm', 'Implement issue ' + name)
            self.git('-C', str(directory), 'merge', '--no-edit', 'integration')
            self.git('merge', '--no-ff', '--no-edit', 'issue-' + name)
        self.assertFalse(list(self.log.glob('*.started')))
        self.assertFalse(self.run_cli('gate', '--session', str(self.session), expected=2)['allowed'])

    def assess(self, resolution='fixed'):
        path = self.root / 'assessments.json'
        path.write_text(json.dumps([{'axis': 'spec', 'index': 0, 'resolution': resolution,
                                    'explanation': 'Author checked the combined requirement and validation.'}]))
        return self.run_cli('assess', '--session', str(self.session), '--assessments', str(path), expected=2)

    def test_combined_issue_scope_and_author_fix_receive_fresh_integration_review(self):
        self.assemble_issues()
        cli = self.bin / 'claude'
        cli.write_text(fixture.STAND_IN.replace('mode = os.environ.get("REVIEW_MODE")',
            'mode = "findings" if pathlib.Path("source/issue-c.txt").read_text() != "5\\n" else None'))
        first = self.review(expected=2)
        self.assertEqual(first['scope']['baseline'], self.baseline)
        self.assertEqual(set(first['scope']['paths']), {'issue-a.txt', 'issue-b.txt', 'issue-c.txt'})
        captures = [json.loads((self.log / (axis + '.json')).read_text()) for axis in ('standards', 'spec')]
        self.assertEqual(captures[0]['prompt']['scope'], captures[1]['prompt']['scope'])
        self.assertEqual(captures[1]['prompt']['spec'], self.spec.read_text())
        self.assertNotIn('user-notes.txt', captures[1]['files'])
        self.assertEqual(captures[1]['files']['issue-a.txt'], '2\n')
        self.assertEqual(captures[1]['files']['issue-b.txt'], '3\n')
        self.assertEqual(first['rounds_started'], 1)
        self.run_cli('gate', '--session', str(self.session), expected=2)
        (self.repo / 'issue-c.txt').write_text('5\n')
        (self.repo / 'regression.txt').write_text('2 + 3 = 5\n')
        self.assess()
        second = self.review()
        self.assertEqual(second['rounds_completed'], 2)
        self.assertEqual(set(second['scope']['paths']),
                         {'issue-a.txt', 'issue-b.txt', 'issue-c.txt', 'regression.txt'})
        capture = json.loads((self.log / 'spec.json').read_text())
        self.assertEqual(capture['files']['issue-c.txt'], '5\n')
        self.assertEqual(capture['files']['regression.txt'], '2 + 3 = 5\n')
        self.assertEqual(capture['prompt']['previous_review']['author_assessments'][0]['resolution'], 'fixed')
        self.assertEqual(len(second['resolved_findings']), 1)
        self.run_cli('gate', '--session', str(self.session))
        # Final commits are allowed, but change the revision and need fresh review.
        self.git('add', 'issue-c.txt', 'regression.txt')
        self.git('commit', '-qm', 'Fix combined requirement')
        self.run_cli('gate', '--session', str(self.session), expected=2)
        third = self.review()
        self.assertEqual(third['rounds_completed'], 3)
        self.run_cli('gate', '--session', str(self.session))
        self.assertEqual((self.repo / 'user-notes.txt').read_text(), 'unrelated unfinished work\n')
        self.assertTrue(all(path.exists() for path in self.worktrees.values()))

    def test_failed_integration_review_preserves_commits_and_unfinished_work(self):
        self.assemble_issues()
        head = self.git('rev-parse', 'HEAD')
        (self.worktrees['c'] / 'unfinished.txt').write_text('recoverable author work\n')
        self.env['REVIEW_MODE'] = 'authentication'
        first = self.review(expected=2)
        self.assertEqual(first['rounds_started'], 1)
        self.assertEqual(first['rounds_completed'], 0)
        self.run_cli('gate', '--session', str(self.session), expected=2)
        self.env.pop('REVIEW_MODE')
        self.assertEqual(self.review(expected=2)['rounds_started'], 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual((self.worktrees['c'] / 'unfinished.txt').read_text(), 'recoverable author work\n')
        self.assertTrue(all(path.exists() for path in self.worktrees.values()))
        self.assertEqual((self.repo / 'user-notes.txt').read_text(), 'unrelated unfinished work\n')

    def test_integration_disagreement_blocks_clean_replacement_review(self):
        self.assemble_issues()
        self.env['REVIEW_MODE'] = 'findings'
        self.review(expected=2)
        result = self.assess('disagree')
        self.assertEqual(len(result['disagreements']), 1)
        self.env.pop('REVIEW_MODE')
        self.assertEqual(self.review(expected=2)['rounds_started'], 1)
        self.run_cli('gate', '--session', str(self.session), expected=2)
        self.assertTrue(all(path.exists() for path in self.worktrees.values()))

    def test_three_integration_rounds_with_remaining_findings_block_delivery(self):
        self.assemble_issues()
        self.env['REVIEW_MODE'] = 'findings'
        for number in range(1, 4):
            result = self.review(expected=2)
            self.assertEqual(result['rounds_started'], number)
            self.assertEqual(result['scope']['baseline'], self.baseline)
            (self.repo / 'issue-c.txt').write_text('author revision {}\n'.format(number))
            self.assess()
        process = (self.log / 'spec.started').read_text()
        self.env.pop('REVIEW_MODE')
        result = self.review(expected=2)
        self.assertEqual(result['rounds_started'], 3)
        self.assertTrue(result['remaining_findings'])
        self.assertEqual((self.log / 'spec.started').read_text(), process)
        self.run_cli('gate', '--session', str(self.session), expected=2)
        self.assertEqual((self.repo / 'issue-c.txt').read_text(), 'author revision 3\n')

    def test_integration_change_during_review_invalidates_both_frozen_reports(self):
        self.assemble_issues()
        cli = self.bin / 'claude'
        cli.write_text(fixture.STAND_IN.replace('mode = os.environ.get("REVIEW_MODE")',
            'if axis == "standards":\n    pathlib.Path(os.environ["LIVE_FILE"]).write_text("concurrent fix\\n")\n'
            'mode = os.environ.get("REVIEW_MODE")'))
        self.env['LIVE_FILE'] = str(self.repo / 'issue-c.txt')
        first = self.review(expected=2)
        self.assertIn('stale', first['error'])
        for axis in ('standards', 'spec'):
            capture = json.loads((self.log / (axis + '.json')).read_text())
            self.assertEqual(capture['files']['issue-c.txt'], '4\n')
            self.assertEqual(capture['prompt']['scope']['baseline'], self.baseline)
        self.run_cli('gate', '--session', str(self.session), expected=2)
        cli.write_text(fixture.STAND_IN)
        second = self.review()
        self.assertEqual(second['rounds_completed'], 2)
        self.assertIn('concurrent fix', second['scope']['diff'])
        self.run_cli('gate', '--session', str(self.session))

    def test_claude_coordinator_reviews_the_same_combined_scope_through_codex(self):
        self.use_claude_author()
        self.assemble_issues()
        result = self.review()
        for axis in ('standards', 'spec'):
            self.assertEqual(result['reviews'][axis]['tool'], 'codex')
            capture = json.loads((self.log / (axis + '.json')).read_text())
            self.assertEqual(set(capture['prompt']['scope']['paths']),
                             {'issue-a.txt', 'issue-b.txt', 'issue-c.txt'})
        self.assertEqual(result['scope']['baseline'], self.baseline)
        self.run_cli('gate', '--session', str(self.session))
