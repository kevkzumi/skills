"""Reviewer selection, read-only launch capabilities, and structured results."""
import json
import os
import subprocess


REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "axis": {"type": "string", "enum": ["standards", "spec"]},
        "complete": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["clean", "findings"]},
        "findings": {"type": "array", "items": {"type": "object",
            "additionalProperties": False,
            "properties": {key: {"type": "string"} for key in
                           ("severity", "description", "evidence", "reference")},
            "required": ["severity", "description", "evidence", "reference"]}},
        "summary": {"type": "string"}},
    "required": ["axis", "complete", "verdict", "findings", "summary"]}


def author_context(explicit=None):
    codex = bool(os.environ.get("CODEX_THREAD_ID"))
    claude = bool(os.environ.get("CLAUDECODE"))
    if codex and claude:
        raise ValueError("Ambiguous author context: both Codex and Claude markers are present.")
    detected = "codex" if codex else "claude" if claude else None
    if explicit and detected and explicit != detected:
        raise ValueError("Author argument conflicts with execution context.")
    author = explicit or detected
    if author not in ("codex", "claude"):
        raise ValueError("Unknown author context; provide --author only from a verified Codex or Claude session.")
    return author


CODEX_DISABLED_FEATURES = ("hooks", "plugins", "apps", "multi_agent", "browser_use",
                           "computer_use", "image_generation")


def command(author, directory=None, axis=None, model=None, timeout=30):
    if author == "claude":
        flags = [argument for feature in CODEX_DISABLED_FEATURES for argument in ("--disable", feature)]
        # Discover configuration without printing transport values, which can contain secrets.
        discovery = subprocess.run(["codex", *flags, "mcp", "list", "--json"], cwd=directory,
                                   capture_output=True, text=True, timeout=min(timeout, 30))
        if discovery.returncode:
            raise ValueError("Could not discover Codex MCP configuration; review is blocked.")
        try:
            servers = json.loads(discovery.stdout)
            if not isinstance(servers, list) or any(
                    not isinstance(server, dict) or not isinstance(server.get("name"), str)
                    or not server["name"] for server in servers):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("Invalid Codex MCP configuration response; review is blocked.") from None
        for server in servers:
            flags.extend(["-c", "mcp_servers." + json.dumps(server["name"]) + ".enabled=false"])
        schema = directory / (axis + "-schema.json")
        schema.write_text(json.dumps(REPORT_SCHEMA))
        result = ["codex", "-a", "never", *flags, "exec", "-C", str(directory), "-s", "read-only",
                  "--ephemeral", "--skip-git-repo-check", "--json", "--output-schema", str(schema),
                  "--output-last-message", str(directory / (axis + "-last-message.json"))]
        if model is not None:
            result.extend(["-m", model])
        return result + ["-"]
    if author != "codex":
        raise ValueError("Unsupported author; no same-tool fallback is permitted.")
    result = ["claude", "--print", "--safe-mode", "--no-session-persistence",
              "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
              "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
              "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
              "--permission-mode", "dontAsk", "--permission-prompts", "none",
              "--output-format", "json", "--json-schema", json.dumps(REPORT_SCHEMA)]
    if model is not None:
        result.extend(["--model", model])
    return result


def parse(stdout, axis, author="codex", directory=None, model=None):
    resolved_models = []
    if author == "claude":
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if (not events or not all(isinstance(event, dict) for event in events)
                or not any(event.get("type") == "turn.completed" for event in events)
                or any(event.get("type") in ("error", "turn.failed") for event in events)):
            raise ValueError("Codex did not return a successful completed turn.")
        report = json.loads((directory / (axis + "-last-message.json")).read_text())
        # Codex JSON events currently do not expose a verified resolved model identity.
        tool = "codex"
    else:
        envelope = json.loads(stdout)
        if (not isinstance(envelope, dict) or envelope.get("type") != "result"
                or envelope.get("subtype") != "success" or envelope.get("is_error") is not False):
            raise ValueError("Reviewer did not return a successful result envelope.")
        report = envelope.get("structured_output")
        usage = envelope.get("modelUsage", {})
        if isinstance(usage, dict):
            resolved_models = [name for name in usage if isinstance(name, str) and name.strip()]
        tool = "claude"
    if (not isinstance(report, dict) or report.get("axis") != axis
            or report.get("complete") is not True
            or not isinstance(report.get("summary"), str) or not report["summary"].strip()
            or not isinstance(report.get("findings"), list)):
        raise ValueError("Missing or incomplete structured review report.")
    for finding in report["findings"]:
        if not isinstance(finding, dict) or any(
                not isinstance(finding.get(key), str) or not finding[key].strip()
                for key in ("severity", "description", "evidence", "reference")):
            raise ValueError("A finding is missing its severity, evidence, or reference.")
    expected_verdict = "findings" if report["findings"] else "clean"
    if report.get("verdict") != expected_verdict:
        raise ValueError("Review verdict is missing or contradicts its findings.")
    report.update(tool=tool, requested_model=model if model is not None else "configured default",
                  resolved_models=resolved_models, model_identity="verified" if resolved_models else "unknown")
    return report
