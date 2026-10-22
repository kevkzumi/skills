---
name: implement-cross-review
description: "Implement a piece of work with independent reviews through the other coding tool."
disable-model-invocation: true
---

Implement the supplied spec or issues, then obtain separate Standards and Spec reviews through the other coding tool before the final implementation commit. The current session owns all edits and completion actions.

This beta currently supports a Codex author with Claude Code reviewers and a clean starting repository. It requires Python 3.9+, Git, an authenticated Claude Code CLI, and a repo-backed installation. Resolve this skill's real directory through any symlink, then read the shared [handoff contract](../../../scripts/cross-review.md). Its sibling executable and the existing review criteria are required; copying this skill folder alone is insufficient.

1. Fetch supplied issue references through the project's issue tracker and state their titles. Resolve the spec from those issues, an explicit source, or matching project specs. If none is found, ask the user for it; only their confirmation permits a recorded Spec skip. Search for all applicable standards and instructions, including `CODING_STANDARDS.md` and `CONTRIBUTING.md` when present. Follow their references and supply their full contents to review.
2. Before editing, run the handoff's `begin` command with a dedicated artifact directory outside the project. Identify the author from the actual coordinating session; use `--author codex` only in a verified Codex session. Stop on ambiguous context, unsupported tools, or pre-existing changes. Preserve existing work.
3. Call the Skill tool with "tdd" where appropriate, at pre-agreed seams. Implement the requested scope. Run relevant focused checks during development and the full relevant suite at completion. Intermediate commits are included in review from the original baseline.
4. Pause edits and run `review` with the resolved spec and every relevant standards source. The shared handoff exports the complete implementation, including staged, unstaged, and untracked changes, then runs two fresh, concurrent, read-only Claude Code reviewers. It reads the existing review contract as criteria without invoking its native-agent dispatcher. Do not run the normal `code-review` skill as a substitute.
5. Present Standards and Spec separately, with evidence, references, reviewer tool, and known model identity. A skipped Spec is explicitly skipped, never passed. Any findings or incomplete review block completion in this initial beta. Preserve the work and reports; do not silently dismiss findings or substitute a same-tool reviewer.
6. Once review allows completion and validation has passed, run `gate` immediately before the final implementation commit. A changed working state requires fresh review. Commit only the reviewed implementation on the current branch. Report the commit, checks, each review's outcome, and any live-integration limitations.
