"""Exercise opposite-tool routing through the complete public review handoff."""
import json
import os
from pathlib import Path
import unittest

import test_cross_review as handoff


CODEX_STAND_IN = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
if "mcp" in args:
    if os.environ.get("REVIEW_MODE") == "mcp-discovery-failure":
        print("private transport token", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("REVIEW_MODE") == "invalid-mcp":
        print('{"credential":"private transport token"}')
        sys.exit(0)
    print(json.dumps([{"name": 'external.server"name', "enabled": True}]))
    sys.exit(0)
prompt = json.load(sys.stdin)
axis = prompt["axis"]
log = pathlib.Path(os.environ["REVIEW_LOG"])
(log / (axis + ".started")).write_text(str(os.getpid()))
deadline = time.monotonic() + 5
while len(list(log.glob("*.started"))) < 2:
    if time.monotonic() > deadline:
        sys.exit(12)
    time.sleep(.02)
(log / (axis + ".json")).write_text(json.dumps({
    "args": args, "prompt": prompt,
    "files": {p.name: p.read_text() for p in pathlib.Path("source").glob("*.txt")},
}))
report = {"axis": axis, "complete": True, "verdict": "clean", "findings": [], "summary": "Reviewed source."}
mode = os.environ.get("REVIEW_MODE")
if mode in ("authentication", "model-unavailable"):
    print(mode, file=sys.stderr)
    sys.exit(1)
if mode == "missing-result":
    print(json.dumps({"type": "turn.completed"}))
    sys.exit(0)
if mode == "incomplete":
    report["complete"] = False
if mode == "wrong-axis":
    report["axis"] = "other"
if mode == "contradiction":
    report["verdict"] = "findings"
if mode == "timeout":
    time.sleep(5)
if mode == "turn-failed":
    print(json.dumps({"type": "turn.failed", "error": {"message": "incomplete"}}))
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(report))
print(json.dumps({"type": "thread.started", "thread_id": "test-thread"}))
print(json.dumps({"type": "turn.completed", "usage": {}}))
'''


class ProviderHandoffTests(unittest.TestCase):
    setUp = handoff.HandoffTests.setUp
    git = handoff.HandoffTests.git
    run_cli = handoff.HandoffTests.run_cli
    begin = handoff.HandoffTests.begin
    review = handoff.HandoffTests.review

    def use_claude_author(self):
        self.env.pop("CODEX_THREAD_ID", None)
        self.env["CLAUDECODE"] = "1"
        cli = self.bin / "codex"
        cli.write_text(CODEX_STAND_IN)
        cli.chmod(0o755)

    def test_claude_author_receives_two_independent_codex_reviews_with_read_only_capabilities(self):
        self.use_claude_author()
        self.begin()
        (self.repo / "existing.txt").write_text("implementation\n")
        result = self.review()
        self.assertTrue(result["allowed"])
        for axis in ("standards", "spec"):
            report = result["reviews"][axis]
            self.assertEqual(report["tool"], "codex")
            self.assertEqual(report["requested_model"], "configured default")
            self.assertEqual(report["resolved_models"], [])
            self.assertEqual(report["model_identity"], "unknown")
            capture = json.loads((self.log / (axis + ".json")).read_text())
            self.assertEqual(capture["files"], {"existing.txt": "implementation\n"})
            args = capture["args"]
            self.assertEqual(args[args.index("-s") + 1], "read-only")
            self.assertEqual(args[args.index("-a") + 1], "never")
            self.assertIn("--ephemeral", args)
            self.assertIn("--skip-git-repo-check", args)
            self.assertNotIn("-m", args)
            disabled = [args[i + 1] for i, value in enumerate(args) if value == "--disable"]
            for integration in ("hooks", "plugins", "apps", "multi_agent", "browser_use", "computer_use", "image_generation"):
                self.assertIn(integration, disabled)
            self.assertIn('mcp_servers."external.server\\"name".enabled=false', args)
        self.assertNotEqual((self.log / "standards.started").read_text(), (self.log / "spec.started").read_text())
        self.assertTrue(self.run_cli("gate", "--session", str(self.session))["allowed"])
        self.assertEqual(self.git("log", "-1", "--format=%s"), "baseline")

    def test_model_overrides_are_literal_arguments_in_both_directions(self):
        model = 'requested-model;$(touch injected)'
        for author, model_flag, tool in (("codex", "--model", "claude"), ("claude", "-m", "codex")):
            with self.subTest(author=author):
                if author == "claude":
                    self.use_claude_author()
                self.session = self.root / ("model-review-" + author)
                self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session),
                             "--reviewer-model", model)
                (self.repo / "existing.txt").write_text("implementation\n")
                result = self.review()
                for axis in ("standards", "spec"):
                    report = result["reviews"][axis]
                    self.assertEqual(report["tool"], tool)
                    self.assertEqual(report["requested_model"], model)
                    capture = json.loads((self.log / (axis + ".json")).read_text())
                    args = capture["args"]
                    self.assertEqual(args[args.index(model_flag) + 1], model)
                self.assertFalse((Path(result["artifacts"]) / "injected").exists())
                self.assertFalse((self.repo / "injected").exists())
                (self.repo / "existing.txt").write_text("before\n")

    def test_unknown_and_conflicting_author_contexts_do_not_launch_reviewers(self):
        self.env.pop("CODEX_THREAD_ID", None)
        self.env.pop("CLAUDECODE", None)
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)
        self.assertIn("Unknown author", result["error"])
        self.env["CODEX_THREAD_ID"] = "codex-author"
        self.env["CLAUDECODE"] = "1"
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session), expected=2)
        self.assertIn("Ambiguous", result["error"])
        self.env.pop("CLAUDECODE")
        result = self.run_cli("begin", "--repo", str(self.repo), "--session", str(self.session),
                              "--author", "claude", expected=2)
        self.assertIn("conflicts", result["error"])
        self.assertEqual(list(self.log.iterdir()), [])

    def test_codex_failed_or_incomplete_handoffs_block_completion_without_leaking_mcp_details(self):
        self.use_claude_author()
        (self.repo / "existing.txt").write_text("before\n")
        for mode in ("authentication", "model-unavailable", "missing-result", "incomplete", "wrong-axis",
                     "contradiction", "turn-failed", "timeout", "mcp-discovery-failure", "invalid-mcp"):
            with self.subTest(mode=mode):
                self.session = self.root / ("review-" + mode)
                self.begin()
                (self.repo / "existing.txt").write_text("implementation\n")
                self.env["REVIEW_MODE"] = mode
                result = self.review("--timeout", "0.5", expected=2)
                self.assertFalse(result["allowed"])
                self.assertNotIn("private transport token", json.dumps(result))
                self.assertFalse(self.run_cli("gate", "--session", str(self.session), expected=2)["allowed"])
                self.assertEqual(self.git("log", "-1", "--format=%s"), "baseline")
                (self.repo / "existing.txt").write_text("before\n")


if __name__ == "__main__":
    unittest.main()
