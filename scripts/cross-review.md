# External review handoff

This shared contract separates authoring from reviewer execution. Run the linked skill's `scripts/review.py` with Python 3.9+, or this checkout's `scripts/cross-review.py` directly. Resolve symlinks before locating the checkout. Keep the complete fork checkout: shared code and existing `code-review` criteria live outside the beta skill folders. Dependencies are Python's standard library, Git, and the authenticated opposite tool's CLI.

## Begin before editing

```text
python3 <entrypoint> begin --repo <project> --session <new-external-directory> --author codex
```

Choose a dedicated, initially absent directory outside the project. Record its path and keep using that session for the whole workflow, including fixes. The verified authoring tool determines the reviewer: Codex uses Claude Code; Claude Code uses Codex. Environment markers can detect the author; `--author` is an explicit statement of the verified coordinating tool when markers are absent. Conflicting markers or unsupported contexts block execution. It never selects a same-tool fallback.

The reviewer uses its configured default model unless the user requests an override; then add `--reviewer-model <model>` to `begin`. The preference persists across rounds. Requested and resolved model identities are reported separately; unavailable requested models block review.

An existing commit is required. With a clean start, ownership defaults to all implementation changes. For narrower ownership, or any dirty start, repeat `--path <repo-relative-file-or-directory>` for the owned scope. Paths overlapping pre-existing dirty work are rejected. Preserve that work; a later change to an initially dirty path is ambiguous and blocks review. Unrelated paths outside explicit ownership stay outside review and commits. An optional `--baseline <ancestor-commit>` pins the complete integration range. Unresolved merges, submodules, and hidden assume-unchanged or skip-worktree entries are unsupported.

## Review after validation

```text
python3 <entrypoint> review --session <directory> --spec <resolved-spec-file> --standards <rules-file> --standards <other-rules-file>
```

Fetch external issues first and save their full requirements with references to a temporary spec file. Include applicable ancestor instructions and every documented standard, following their references. Standards sources are explicit because automatic instruction discovery is disabled. For single-work implementation only, if the user confirms there is no spec, use `--no-spec-reason <confirmation-and-reason>`; the result records a skip. Spec coordination requires its parent spec. Empty specs or skip reasons fail. The requirements, standards, and skip policy stay fixed throughout this workflow. Keep their source files available: changes to their contents invalidate completion, including changes after review.

Pause author edits until review finishes. Both fresh processes receive the same frozen scoped source export and full diff from the original baseline, with separate axis criteria. The export covers committed, staged, unstaged, and nonignored untracked implementation files. Stage ignored files explicitly when they belong to the implementation. Symlinks become their target text, never links outside the export. Author edits during review invalidate its completion result.

Claude disables ordinary inherited customizations, hooks, integrations, permission prompts, skill invocation, and session persistence; its built-in tools are limited to Read, Glob, and Grep. Codex uses a read-only sandbox, no approval prompts, ephemeral execution, and disabled integrations, hooks, plugins, and automatic instruction discovery. Admin-managed policy still applies. If inherited or managed behavior cannot be constrained, stop until an isolated environment enforces read-only review. Never weaken restrictions to make a launch succeed.

Each process defaults to a 600-second timeout; `--timeout` accepts positive seconds up to 3600. A timeout terminates its process group. Raw inputs, stdout, stderr, and results remain in the session directory. `resolved_models` contains verified CLI metadata when available; an empty list means unknown. Do not infer the resolved model from the requested selection.

The helper reads the existing review criteria at runtime: documented-standard precedence, all twelve Standards smells, tooling exclusions, and the Spec questions. Every report must identify its axis, completeness, consistent verdict, and evidence and references for each finding. Keep Standards and Spec distinct when presenting results. Invalid output, nonzero exits, missing CLIs, authentication/model errors, or timeouts block this workflow and preserve available reports.

## Assess findings and repeat

The author performs all fixes and validation. Reviewers stay read-only. After reviewing every finding, write a JSON list containing one assessment for each current finding, identified by its `axis` and zero-based `index` within that axis's findings list:

```json
[
  {"axis": "spec", "index": 0, "resolution": "fixed", "explanation": "Implemented the missing behavior and ran its regression check."}
]
```

```text
python3 <entrypoint> assess --session <directory> --assessments <assessment-file>
```

Use `fixed` only after the author makes and validates the accepted fix. Use `disagree` with an evidence-based explanation for a finding the author disputes. Every finding must be assessed exactly once. An unresolved disagreement stops this workflow; present it to the user. An assessment always leaves completion blocked until external review verifies the updated work.

When every finding is fixed, repeat `review` with the same spec and standards. Each round exports fresh inputs covering the entire implementation and gives each axis its own previous report and author assessments. The helper permits at most three rounds total, including the initial review. It counts launched attempts, so failed or stale attempts cannot create unlimited retries. An execution failure stops the workflow; the author must report it instead of retrying silently or substituting reviewers. Preserve the existing session and counter; never begin a replacement session merely to bypass a blocker or the round limit.

Results expose rounds started and completed, resolved and remaining findings, disagreements, separate reports, and blockers. Use these in the end-of-run report. A failed axis cannot clear its previous findings. After three rounds, unresolved findings block completion and must be presented to the user. The helper serializes commands for a session; after an interrupted process, inspect the session and confirm no process is running before removing a stale lock. Preserve incomplete review as blocked.

## Gate completion

```text
python3 <entrypoint> gate --session <directory>
```

Exit 0 and `allowed: true` mean the current implementation still matches a completed clean review, with any explicit Spec skip recorded. Exit 2 or `allowed: false` blocks completion. A later failed review revokes an earlier pass. The helper never commits, publishes, or closes issues. Validation and the final action remain the author's responsibility.

For a single-work final commit, use the returned `scope.commit_paths` as exact arguments to `git --literal-pathspecs add -- <paths>`, recheck `gate`, then `git --literal-pathspecs commit --only -m <message> -- <paths>` without further content edits. This excludes unrelated staged work. An empty `commit_paths` means no final content commit is needed. Do not interpolate raw paths into shell code or use broad staging commands. Any subsequent edits require fresh review within the same round budget.

For spec coordination, intermediate implementation and merge commits are allowed. The shared outcome gates PR readiness, tracker resolution, and reporting the spec complete. A blocker preserves the draft PR, open work, integration branch, and recoverable unfinished work. The coordinator supplies the complete parent spec and issue requirements and reviews the full original integration range after all issues are merged.

## Verification

Run `python3 -m unittest discover -s scripts/tests -v` from the fork checkout. Tests exercise complete handoffs with temporary Git repositories and stand-in CLIs, covering concurrent processes, capabilities, routing, scope, fix loops, skips, and blocked completion. Stand-ins validate launch configuration; they do not prove real CLI enforcement or authentication compatibility. Report live smoke results separately.
