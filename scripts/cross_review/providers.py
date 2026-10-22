"""Reviewer selection, read-only launch capabilities, and structured results."""
import json
import os


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
    if author != "codex":
        raise ValueError("This slice supports a known Codex author only; provide --author codex from a verified session.")
    return author


def command(author):
    if author != "codex":
        raise ValueError("Unsupported author; no same-tool fallback is permitted.")
    return ["claude", "--print", "--safe-mode", "--no-session-persistence",
            "--tools", "Read,Glob,Grep", "--allowedTools", "Read,Glob,Grep",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--output-format", "json", "--json-schema", json.dumps(REPORT_SCHEMA)]


def parse(stdout, axis):
    envelope = json.loads(stdout)
    if (not isinstance(envelope, dict) or envelope.get("type") != "result"
            or envelope.get("subtype") != "success" or envelope.get("is_error") is not False):
        raise ValueError("Reviewer did not return a successful result envelope.")
    report = envelope.get("structured_output")
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
    report.update(tool="claude", requested_model="configured default",
                  resolved_models=list(envelope.get("modelUsage", {})))
    return report
