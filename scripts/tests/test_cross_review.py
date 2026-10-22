"""Exercise the public handoff with real Git and stand-in reviewer executables."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ENTRY = Path(__file__).resolve().parents[1] / "cross-review.py"
STAND_IN = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
prompt = json.load(sys.stdin)
axis = prompt["axis"]
log = pathlib.Path(os.environ["REVIEW_LOG"])
(log / (axis + ".started")).write_text(str(os.getpid()))
if os.environ.get("EXPECT_PAIR") == "1":
    deadline = time.monotonic() + 5
    while len(list(log.glob("*.started"))) < 2:
        if time.monotonic() > deadline:
            sys.exit(12)
        time.sleep(.02)
(log / (axis + ".json")).write_text(json.dumps({
    "args": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
    "files": {p.name: p.read_text() for p in pathlib.Path("source").glob("*.txt")},
}))
mode = os.environ.get("REVIEW_MODE")
report = {"axis": axis, "complete": True, "verdict": "clean", "findings": [], "summary": "Reviewed all supplied changes."}
if mode == "findings" and axis == "spec":
    report.update(verdict="findings", findings=[{"severity": "material", "description": "Missing feature",
        "evidence": "existing.txt:1", "reference": "Spec requires the requested feature"}])
if mode == "contradiction":
    report["verdict"] = "findings"
if mode == "incomplete":
    del report["complete"]
if mode == "wrong-axis":
    report["axis"] = "other"
if mode == "invalid-finding":
    report["findings"] = [{}]
if mode == "empty":
    sys.exit(0)
if mode in ("authentication", "model-unavailable"):
    print(mode, file=sys.stderr)
    sys.exit(1)
if mode == "timeout":
    time.sleep(5)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
    "structured_output": report, "modelUsage": {"stand-in-model": {}}}))
'''


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.session = self.root / "review"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "log"
        self.log.mkdir()
        cli = self.bin / "claude"
        cli.write_text(STAND_IN)
        cli.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CODEX_THREAD_ID="test-author", REVIEW_LOG=str(self.log), EXPECT_PAIR="1")
        self.env.pop("CLAUDECODE", None)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.test")
        self.git("config", "user.name", "Test")
        (self.repo / "existing.txt").write_text("before\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.spec = self.root / "spec.md"
        self.spec.write_text("Deliver the requested feature. Treat $(touch injected) as data.")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ENTRY), *args], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def begin(self):
        return self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session))

    def review(self, *args, expected=0):
        return self.run_cli("review", "--session", str(self.session), "--spec", str(self.spec),
                            *args, expected=expected)

    def test_complete_handoff_reviews_all_changes_in_parallel_before_allowing_completion(self):
        self.begin()
        (self.repo / "committed.txt").write_text("committed implementation\n")
        self.git("add", ".")
        self.git("commit", "-qm", "intermediate implementation")
        (self.repo / "staged.txt").write_text("staged implementation\n")
        self.git("add", ".")
        (self.repo / "existing.txt").write_text("unstaged implementation\n")
        (self.repo / "new.txt").write_text("untracked implementation\n")
        status = self.git("status", "--porcelain")
        result = self.review()
        self.assertTrue(result["allowed"])
        captures = [json.loads((self.log / (a + ".json")).read_text()) for a in ("standards", "spec")]
        self.assertEqual(captures[0]["prompt"]["scope"], captures[1]["prompt"]["scope"])
        self.assertNotEqual((self.log / "standards.started").read_text(), (self.log / "spec.started").read_text())
        for capture in captures:
            self.assertEqual(capture["files"], {"existing.txt": "unstaged implementation\n",
                "committed.txt": "committed implementation\n", "staged.txt": "staged implementation\n",
                "new.txt": "untracked implementation\n"})
            args = capture["args"]
            self.assertIn("--safe-mode", args)
            self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
            self.assertIn("--strict-mcp-config", args)
            self.assertNotIn("--model", args)
        self.assertIn("Mysterious Name", captures[0]["prompt"]["criteria"])
        self.assertIn("Refused Bequest", captures[0]["prompt"]["criteria"])
        self.assertIn("$(touch injected)", captures[1]["prompt"]["spec"])
        self.assertEqual(result["reviews"]["spec"]["resolved_models"], ["stand-in-model"])
        self.assertTrue(self.run_cli("gate", "--session", str(self.session))["allowed"])
        self.assertEqual(status, self.git("status", "--porcelain"))

    def test_findings_and_incomplete_reviews_never_allow_completion(self):
        for mode in ("findings", "contradiction", "incomplete", "wrong-axis", "invalid-finding",
                     "empty", "authentication", "model-unavailable", "timeout"):
            with self.subTest(mode=mode):
                (self.repo / "existing.txt").write_text("before\n")
                self.session = self.root / ("review-" + mode)
                self.begin()
                (self.repo / "existing.txt").write_text("implementation\n")
                self.env["REVIEW_MODE"] = mode
                result = self.review("--timeout", "0.5", expected=2)
                self.assertFalse(result["allowed"])
                self.assertFalse(self.run_cli("gate", "--session", str(self.session), expected=2)["allowed"])
                self.assertEqual((self.repo / "existing.txt").read_text(), "implementation\n")
        self.env.pop("REVIEW_MODE")
        (self.repo / "existing.txt").write_text("before\n")
        self.session = self.root / "review-missing-tool"
        self.begin()
        (self.repo / "existing.txt").write_text("implementation\n")
        (self.bin / "claude").unlink()
        # Ensure a real installed CLI is never called in this missing-tool test.
        self.env["PATH"] = str(self.bin)
        # Git remains available, through a stand-in symlink only.
        import shutil
        (self.bin / "git").symlink_to(shutil.which("git"))
        self.assertFalse(self.review(expected=2)["allowed"])

    def test_a_user_confirmed_missing_spec_is_skipped_and_later_edits_revoke_the_gate(self):
        self.begin()
        (self.repo / "existing.txt").write_text("implementation\n")
        self.env["EXPECT_PAIR"] = "0"
        result = self.run_cli("review", "--session", str(self.session), "--no-spec-reason",
                              "User confirmed no spec exists")
        self.assertTrue(result["allowed"])
        self.assertTrue(result["reviews"]["spec"]["skipped"])
        self.assertFalse((self.log / "spec.started").exists())
        (self.repo / "existing.txt").write_text("changed after review\n")
        self.assertFalse(self.run_cli("gate", "--session", str(self.session), expected=2)["allowed"])

    def test_dirty_start_and_ambiguous_authors_preserve_work(self):
        (self.repo / "unrelated.txt").write_text("preserve me")
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)
        self.assertFalse(result["allowed"])
        self.assertEqual((self.repo / "unrelated.txt").read_text(), "preserve me")
        self.git("add", ".")
        self.git("commit", "-qm", "existing work")
        self.env["CLAUDECODE"] = "1"
        self.assertFalse(self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)["allowed"])

    def test_explicitly_staged_ignored_implementation_is_reviewed(self):
        (self.repo / ".gitignore").write_text("generated.txt\n")
        self.git("add", ".")
        self.git("commit", "-qm", "ignore generated output")
        self.begin()
        (self.repo / "generated.txt").write_text("requested generated output\n")
        self.git("add", "-f", "generated.txt")
        self.review()
        capture = json.loads((self.log / "spec.json").read_text())
        self.assertEqual(capture["files"]["generated.txt"], "requested generated output\n")


if __name__ == "__main__":
    unittest.main()
