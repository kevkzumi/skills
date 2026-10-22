#!/usr/bin/env python3
"""Shared external review handoff. Run with --help for the command contract."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import os
from contextlib import contextmanager

sys.dont_write_bytecode = True
from cross_review import providers, rounds, scope


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


@contextmanager
def session_lock(directory):
    lock = directory / 'workflow.lock'
    descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin", help="Capture the baseline before editing")
    begin.add_argument("--repo", required=True)
    begin.add_argument("--baseline", help="Ancestor commit covering the complete implementation")
    begin.add_argument("--path", dest="paths", action="append", help="Owned repository-relative file or directory; repeat as needed")
    begin.add_argument("--author", choices=["codex", "claude"])
    begin.add_argument("--reviewer-model", help="Requested model for the opposite reviewer tool")
    begin.add_argument("--require-spec", action="store_true", help="Require the parent spec for a full-spec workflow")
    assess = commands.add_parser("assess", help="Record the author's resolution of every finding")
    assess.add_argument("--assessments", required=True, help="JSON list of axis/index/fixed-or-disagree/explanation objects")
    review = commands.add_parser("review", help="Run a frozen two-axis external review")
    sources = review.add_mutually_exclusive_group(required=True)
    sources.add_argument("--spec", help="File containing the resolved spec, including fetched issue text")
    sources.add_argument("--no-spec-reason", help="Reason the user confirmed no spec is available")
    review.add_argument("--standards", action="append", default=[], help="Repeat for every relevant standards source")
    review.add_argument("--timeout", type=float, default=600, help="Seconds per reviewer (default: 600)")
    gate = commands.add_parser("gate", help="Revalidate review before the author completes work")
    for subcommand in (begin, review, gate, assess):
        subcommand.add_argument("--session", required=True, help="Dedicated artifact directory outside the project")
    args = parser.parse_args()
    directory = Path(args.session).resolve()
    state_file = directory / "session.json"
    if args.command == "begin":
        session = scope.begin(args.repo, baseline=args.baseline, paths=args.paths)
        session["author"] = providers.author_context(args.author)
        session["reviewer_model"] = args.reviewer_model
        session["require_spec"] = args.require_spec
        if directory == Path(session["repo"]) or Path(session["repo"]) in directory.parents:
            raise ValueError("Session artifacts must be outside the project.")
        directory.mkdir(parents=True, exist_ok=False)
        write(state_file, session)
        return dict(session, allowed=False, reason="Implementation has not been reviewed.")
    session = json.loads(state_file.read_text())
    with session_lock(directory):
        if args.command == "gate":
            return rounds.gate(session, directory)
        if args.command == "assess":
            return rounds.assess(directory, json.loads(Path(args.assessments).read_text()))
        # Even invalid follow-up input must revoke a previous pass.
        prior = json.loads((directory / 'result.json').read_text()) if (directory / 'result.json').exists() else {}
        write(directory / 'result.json', dict(prior, allowed=False, error='Review is incomplete.'))
        if session.get("require_spec") and not args.spec:
            raise ValueError("Full-spec coordination requires its parent spec; Spec cannot be skipped.")
        if not 0 < args.timeout <= 3600:
            raise ValueError("Reviewer timeout must be positive and at most 3600 seconds.")
        spec = Path(args.spec).read_text() if args.spec else None
        if (args.spec and not spec.strip()) or (args.no_spec_reason is not None and not args.no_spec_reason.strip()):
            raise ValueError("Spec source or confirmed skip reason is empty.")
        standards = [{"source": str(Path(path).resolve()), "contents": Path(path).read_text()}
                     for path in args.standards]
        return rounds.review(session, directory, spec, standards, args.timeout, args.no_spec_reason,
                             spec_source=str(Path(args.spec).resolve()) if args.spec else None)


if __name__ == "__main__":
    try:
        result = main()
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("allowed") or "baseline" in result else 2)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(json.dumps({"allowed": False, "error": str(error)}))
        sys.exit(2)
