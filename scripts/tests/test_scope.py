"""Scope behavior through the complete public handoff in temporary repositories."""
import json
from pathlib import Path
import unittest
import test_cross_review as handoff


class ScopeTests(unittest.TestCase):
    setUp = handoff.HandoffTests.setUp
    git = handoff.HandoffTests.git
    run_cli = handoff.HandoffTests.run_cli
    review = handoff.HandoffTests.review
    begin = handoff.HandoffTests.begin

    def test_owned_implementation_preserves_and_excludes_dirty_start(self):
        (self.repo / "staged-other.txt").write_text("other staged work\n")
        self.git("add", "staged-other.txt")
        (self.repo / "existing.txt").write_text("other unstaged work\n")
        (self.repo / "new-other.txt").write_text("other new work\n")
        before = self.git("status", "--porcelain")
        self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), "--path", "feature")
        (self.repo / "feature").mkdir()
        (self.repo / "feature/committed.txt").write_text("first issue\n")
        self.git("add", "feature/committed.txt")
        self.git("commit", "--only", "-qm", "first issue", "--", "feature/committed.txt")
        (self.repo / "feature/staged.txt").write_text("staged implementation\n")
        self.git("add", "feature/staged.txt")
        (self.repo / "feature/committed.txt").write_text("unstaged implementation\n")
        (self.repo / "feature/new[1].txt").write_text("new implementation\n")
        reviewed = self.review()
        patch = reviewed["scope"]["diff"]
        for name in ("committed.txt", "staged.txt", "new[1].txt"):
            self.assertIn(name, patch)
        for text in ("other staged", "other unstaged", "other new"):
            self.assertNotIn(text, patch)
        captures = [json.loads((self.log / (a + ".json")).read_text()) for a in ("standards", "spec")]
        self.assertEqual(captures[0]["prompt"]["scope"], captures[1]["prompt"]["scope"])
        source = Path(reviewed["artifacts"]) / "source"
        self.assertEqual((source / "existing.txt").read_text(), "before\n")
        self.assertFalse((source / "staged-other.txt").exists())
        paths = reviewed["scope"]["commit_paths"]
        self.git("--literal-pathspecs", "add", "--", *paths)
        self.run_cli("gate", "--session", str(self.session))
        self.git("--literal-pathspecs", "commit", "--only", "-qm", "reviewed implementation", "--", *paths)
        self.assertEqual(before, self.git("status", "--porcelain"))
        committed = self.git("show", "--format=", "--name-only", "HEAD")
        self.assertNotIn("other", committed)
        self.assertNotIn("existing.txt", committed)

    def test_integration_baseline_covers_all_issue_commits_and_rejects_rewritten_history(self):
        baseline = self.git("rev-parse", "HEAD")
        for name in ("first.txt", "second.txt"):
            (self.repo / name).write_text("issue implementation\n")
            self.git("add", name)
            self.git("commit", "-qm", name)
        self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), "--baseline", baseline)
        result = self.review()
        self.assertIn("first.txt", result["scope"]["diff"])
        self.assertIn("second.txt", result["scope"]["diff"])
        self.assertEqual(result["scope"]["baseline"], baseline)
        self.git("checkout", "--orphan", "unrelated-history")
        self.git("commit", "-qm", "different root")
        self.assertFalse(self.review(expected=2)["allowed"])

    def test_overlap_blocks_without_touching_preexisting_work(self):
        (self.repo / "existing.txt").write_text("user staged version\n")
        self.git("add", "existing.txt")
        (self.repo / "existing.txt").write_text("user unstaged version\n")
        before = self.git("diff", "--cached")
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session),
                              "--path", "existing.txt", expected=2)
        self.assertIn("Ambiguous", result["error"])
        self.assertEqual(before, self.git("diff", "--cached"))
        self.assertEqual((self.repo / "existing.txt").read_text(), "user unstaged version\n")
        self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), "--path", "feature.txt")
        (self.repo / "feature.txt").write_text("implementation\n")
        self.review()
        (self.repo / "existing.txt").write_text("another actor changed user work\n")
        self.run_cli("gate", "--session", str(self.session), expected=2)
        result = self.review(expected=2)
        self.assertIn("ambiguous", result["error"])
        self.assertEqual(before, self.git("diff", "--cached"))

    def test_mutation_during_review_preserves_reports_but_invalidates_approval(self):
        self.begin()
        (self.repo / "existing.txt").write_text("reviewed implementation\n")
        cli = self.bin / "claude"
        cli.write_text(handoff.STAND_IN.replace('mode = os.environ.get("REVIEW_MODE")',
            'if axis == "standards":\n    pathlib.Path(os.environ["LIVE_FILE"]).write_text("concurrent implementation\\n")\n'
            'mode = os.environ.get("REVIEW_MODE")'))
        self.env["LIVE_FILE"] = str(self.repo / "existing.txt")
        result = self.review(expected=2)
        self.assertIn("stale", result["error"])
        self.assertEqual(set(result["reviews"]), {"standards", "spec"})
        for axis in ("standards", "spec"):
            capture = json.loads((self.log / (axis + ".json")).read_text())
            self.assertEqual(capture["files"]["existing.txt"], "reviewed implementation\n")
        self.assertEqual((self.repo / "existing.txt").read_text(), "concurrent implementation\n")
        self.run_cli("gate", "--session", str(self.session), expected=2)
        cli.write_text(handoff.STAND_IN)
        refreshed = self.review()
        self.assertIn("concurrent implementation", refreshed["scope"]["diff"])
        self.run_cli("gate", "--session", str(self.session))

    def test_binary_and_deleted_files_are_reviewed_and_scoped_for_commit(self):
        self.begin()
        binary = b"\x00\xff\x01binary implementation\x00"
        (self.repo / "feature.bin").write_bytes(binary)
        (self.repo / "existing.txt").unlink()
        result = self.review()
        self.assertIn("GIT binary patch", result["scope"]["diff"])
        self.assertIn("deleted file mode", result["scope"]["diff"])
        self.assertEqual(set(result["scope"]["commit_paths"]), {"existing.txt", "feature.bin"})
        source = Path(result["artifacts"]) / "source"
        self.assertEqual((source / "feature.bin").read_bytes(), binary)
        self.assertFalse((source / "existing.txt").exists())

    def test_hidden_index_entries_and_submodules_stop_instead_of_omitting_work(self):
        self.git("update-index", "--assume-unchanged", "existing.txt")
        (self.repo / "existing.txt").write_text("hidden implementation\n")
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)
        self.assertIn("Hidden", result["error"])
        self.assertEqual((self.repo / "existing.txt").read_text(), "hidden implementation\n")
        self.git("update-index", "--no-assume-unchanged", "existing.txt")
        self.git("update-index", "--add", "--cacheinfo", "160000", self.git("rev-parse", "HEAD"), "submodule")
        (self.repo / "submodule").mkdir()
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)
        self.assertIn("Submodules", result["error"])
