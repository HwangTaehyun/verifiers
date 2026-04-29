#!/usr/bin/env python3
"""Tier 1: Global PostToolUse hook — security checks only.

This is the lightest hook, running on EVERY Edit/Write via settings.json.
Target: <100ms execution time. Uses only regex, no external processes.

stdin: {"tool_name": "Edit", "tool_input": {"file_path": "/path"}, ...}
stdout: {"additionalContext": "..."} or {}
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Secret patterns to detect
SECRET_REGEXES: list[tuple[str, str]] = [
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API Key"),
    (r"sk_live_[a-zA-Z0-9]{20,}", "Stripe Live Key"),
    (r"xoxb-[a-zA-Z0-9\-]+", "Slack Bot Token"),
    (r'password\s*[:=]\s*["\'][^"\'$\{]{8,}["\']', "Hardcoded password"),
]

# Paths to exclude (false positives)
# .env files (except .env.example) are allowed to contain secrets
EXCLUDE_PATHS = [
    ".env",
    ".env.production",
    ".env.development",
    "_test.go",
    "test_",
    "fixtures/",
    "testdata/",
    "mock",
    "__tests__",
]


def check_secrets(file_path: str) -> list[dict]:
    """Check a file for hardcoded secrets using regex patterns."""
    if any(exc in file_path for exc in EXCLUDE_PATHS):
        return []

    try:
        content = Path(file_path).read_text(errors="replace")
    except OSError:
        return []

    findings = []
    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith(("//", "#", "*", "/*", "<!--")):
            continue
        for pattern, desc in SECRET_REGEXES:
            if re.search(pattern, line):
                findings.append(
                    {
                        "severity": "error",
                        "file": file_path,
                        "line": i,
                        "rule": "V08-HARDCODED-SECRET",
                        "message": f"Possible {desc} detected",
                        "fix": (
                            f"Remove the hardcoded secret at {file_path}:{i}. "
                            f"Move to .env and reference via os.Getenv() or ${{VAR}}"
                        ),
                    }
                )
                break  # One finding per line is enough
    return findings


def main() -> None:
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        print("{}")
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        print("{}")
        return

    # Extract file path from tool input
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path:
        print("{}")
        return

    findings = check_secrets(file_path)

    if not findings:
        print("{}")
        return

    # Build reason (concise, Claude sees this directly) and additionalContext (full detail)
    reason_lines = [
        f"Security error: {len(findings)} secret(s) detected in {file_path}.",
        "Fix these NOW before continuing:\n",
    ]
    context_lines = []
    for f in findings:
        loc = f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
        reason_lines.append(f"  [{f['rule']}] {loc} — {f['fix']}")
        context_lines.append(f"\U0001f6ab VERIFICATION FAILED [{f['rule']}]")
        context_lines.append(f"File: {f['file']}")
        if f.get("line"):
            context_lines.append(f"Line: {f['line']}")
        context_lines.append(f"Issue: {f['message']}")
        context_lines.append("")
        context_lines.append(f"FIX: {f['fix']}")
        context_lines.append("")
        context_lines.append("---")
        context_lines.append("")

    output = {
        "decision": "block",
        "reason": "\n".join(reason_lines),
        "additionalContext": "\n".join(context_lines),
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
