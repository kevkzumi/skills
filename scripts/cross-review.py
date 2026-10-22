#!/usr/bin/env python3
"""Shared external review handoff. Run with --help for the command contract."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import tempfile

sys.dont_write_bytecode = True
from cross_review import providers, runner, scope


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin", help="Capture the baseline before editing")
    begin.add_argument("--repo", required=True)
    begin.add_argument("--baseline", help="Ancestor commit covering the complete implementation")
    begin.add_argument("--path", dest="paths", action="append", help="Owned repository-relative file or directory; repeat as needed")
    begin.add_argument("--author", choices=["codex", "claude"])
    begin.add_argument("--reviewer-model", help="Requested model for the opposite reviewer tool")
    review = commands.add_parser("review", help="Run a frozen two-axis external review")
    sources = review.add_mutually_exclusive_group(required=True)
    sources.add_argument("--spec", help="File containing the resolved spec, including fetched issue text")
    sources.add_argument("--no-spec-reason", help="Reason the user confirmed no spec is available")
    review.add_argument("--standards", action="append", default=[], help="Repeat for every relevant standards source")
    review.add_argument("--timeout", type=float, default=600, help="Seconds per reviewer (default: 600)")
    gate = commands.add_parser("gate", help="Revalidate review before the author completes work")
    for subcommand in (begin, review, gate):
        subcommand.add_argument("--session", required=True, help="Dedicated artifact directory outside the project")
    args = parser.parse_args()
    directory = Path(args.session).resolve()
    state_file = directory / "session.json"
    if args.command == "begin":
        session = scope.begin(args.repo, baseline=args.baseline, paths=args.paths)
        session["author"] = providers.author_context(args.author)
        session["reviewer_model"] = args.reviewer_model
        if directory == Path(session["repo"]) or Path(session["repo"]) in directory.parents:
            raise ValueError("Session artifacts must be outside the project.")
        directory.mkdir(parents=True, exist_ok=False)
        write(state_file, session)
        return dict(session, allowed=False, reason="Implementation has not been reviewed.")
    session = json.loads(state_file.read_text())
    result_file = directory / "result.json"
    if args.command == "gate":
        result = json.loads(result_file.read_text())
        if not scope.unchanged(session, result["scope"]):
            result.update(allowed=False, error="Implementation changed after review; fresh review required.")
        return result
    if not 0 < args.timeout <= 3600:
        raise ValueError("Reviewer timeout must be positive and at most 3600 seconds.")
    # Revoke an earlier completion result before any fallible input preparation.
    write(result_file, {"allowed": False, "error": "Review is incomplete."})
    round_dir = Path(tempfile.mkdtemp(prefix="round-", dir=directory))
    reviewed_scope = scope.snapshot(session, round_dir)
    spec = Path(args.spec).read_text() if args.spec else None
    if args.spec and not spec.strip():
        raise ValueError("Spec source is empty.")
    standards = [{"source": str(Path(path).resolve()), "contents": Path(path).read_text()}
                 for path in args.standards]
    reviews = runner.run(session, reviewed_scope, round_dir, spec, standards, args.timeout, args.no_spec_reason)
    allowed = all(report.get("skipped") or (report.get("complete") and not report.get("findings"))
                  for report in reviews.values())
    result = {"allowed": bool(allowed), "scope": reviewed_scope, "reviews": reviews,
              "artifacts": str(round_dir)}
    if not scope.unchanged(session, reviewed_scope):
        result.update(allowed=False, error="Implementation changed during review; results are stale.")
    write(result_file, result)
    return result


if __name__ == "__main__":
    try:
        result = main()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("allowed") or "baseline" in result else 2)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(json.dumps({"allowed": False, "error": str(error)}))
        sys.exit(2)
