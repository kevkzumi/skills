# External review handoff

This shared contract separates authoring from reviewer execution. Run the linked skill's `scripts/review.py` with Python 3.9+, or run this checkout's `scripts/cross-review.py` directly. Resolve symlinks before locating the checkout. It uses only the Python standard library and Git. Keep the complete fork checkout: shared code and the existing `code-review` criteria are deliberately maintained outside the beta skill folders.

## Begin before editing

```text
python3 <entrypoint> begin --repo <project> --session <new-external-directory> --author codex
```

Choose a dedicated, initially absent artifact directory outside the project. Record its path in the author session so subsequent invocations use the same baseline. The author argument is the verified coordinating tool, never a request to choose a reviewer. Conflicting environment markers block execution. This initial version supports clean starts and Codex authors only. An existing commit is required; unresolved merges and submodules are unsupported.

## Review after validation

```text
python3 <entrypoint> review --session <directory> --spec <resolved-spec-file> --standards <rules-file> --standards <other-rules-file>
```

Fetch external issues first and save their full requirements to a temporary spec file, including source references. Include applicable ancestor instructions and every documented standard, following their references. Standards sources are explicit because the reviewers' automatic instruction discovery is disabled. If the user confirms there is no spec, use `--no-spec-reason <confirmation-and-reason>` instead of `--spec`; the result records a skip. An empty spec is an error.

Pause author edits until review finishes. Both processes receive one frozen source export, the diff from the original baseline, and separate axis criteria. The export includes committed, staged, unstaged and untracked nonignored files. Git symlinks become their target text so review cannot follow links out of the export. Review artifacts remain outside the project. Files ignored by Git must be explicitly staged if they belong to the implementation.

Claude uses its configured default model. `--safe-mode` suppresses ordinary inherited customizations, `--settings` disables hooks, strict MCP configuration disables integrations, and the built-in tool list contains only Read, Glob and Grep. Permission prompts, command/skill invocation, and session persistence are disabled. Admin-managed policy still applies: this is a restriction on reviewer capabilities, not an OS sandbox for every host-side effect. If managed hooks cannot be disabled, stop until an isolated environment enforces the required read-only behavior. Never weaken restrictions to make a failed launch work.

Each process has a 600-second timeout; `--timeout` may set a positive value up to 3600 seconds. A timeout terminates its process group. Processes, schema, output capture, and scope validation are implemented by the helper, not inferred from prose. The JSON output contains `allowed`, separate `reviews`, a scope identity, and an artifact location. Raw stdout and stderr are retained there for diagnosis. `resolved_models` records CLI metadata when provided; an empty list means unknown. Do not infer a model from its requested selection.

The helper reads the existing review criteria at runtime, including all twelve Standards smells, documented-rule precedence, tooling exclusions, and the Spec review questions. It supplies these criteria to external processes without calling the native dispatcher. Every report must identify its axis, declare completeness, return a consistent clean/findings verdict, and attach evidence and references to each finding. Invalid results, nonzero exits, authentication/model failures, unavailable executables, or timeouts block completion.

## Gate completion

```text
python3 <entrypoint> gate --session <directory>
```

Exit 0 and `allowed: true` mean the current implementation still matches a completed review without unresolved findings. Exit 2 or `allowed: false` blocks completion. A later failed review revokes an earlier pass. The helper never commits, publishes, or closes issues: the author runs relevant validation and owns the final action. Stage only the implementation, recheck the gate, then commit without further content edits. Any edits after review require a fresh review. This initial version stops on findings; later slices add the bounded author-fix loop and reverse routing.

## Verification

Run `python3 -m unittest discover -s scripts/tests -v` from the fork checkout. The complete handoff tests use real temporary Git repositories and stand-in CLIs; process-overlap barriers prove concurrency. They check supplied scope, capabilities, source separation, valid results, skips, blocked completion, and work preservation. Stand-ins verify launch configuration; they do not prove real CLI enforcement or authentication compatibility. Report live smoke results separately.
