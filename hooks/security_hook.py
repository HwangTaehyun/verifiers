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

# Secret patterns to detect.
# The hardcoded-password regex excludes ${...} (shell/yaml interpolation)
# and {{...}} (Go/Jinja/Helm template placeholders) so that, e.g.,
#   password = "{{ env.PASSWORD }}"
# does not produce a V08-HARDCODED-SECRET false positive (P2-2).
SECRET_REGEXES: list[tuple[str, str]] = [
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API Key"),
    (r"sk_live_[a-zA-Z0-9]{20,}", "Stripe Live Key"),
    (r"xoxb-[a-zA-Z0-9\-]+", "Slack Bot Token"),
    (r"""password\s*[:=]\s*["'][^"'${}]{8,}["']""", "Hardcoded password"),
]


# Path classification primitives (P2-3): the previous implementation used
# ``any(exc in file_path for exc in EXCLUDE_PATHS)`` which is a substring
# match — e.g. "mock" excluded "mockingbird/Real.go" by accident. Each
# rule below is now anchored to a path component, suffix, or exact name
# so genuine source files are never falsely skipped.
_EXCLUDE_DIRS = frozenset(
    {"fixtures", "testdata", "mock", "mocks", "__tests__", "vendor", "node_modules", "generated", "gen"}
)
_EXCLUDE_FILENAME_PREFIXES = ("test_",)
_EXCLUDE_FILENAME_SUFFIXES = ("_test.go",)
# Exact .env names: .env / .env.development / .env.production are allowed
# to contain secrets (developer-managed). .env.example must still be checked.
_EXCLUDE_EXACT_NAMES = frozenset({".env", ".env.development", ".env.production"})


def _is_excluded_path(file_path: str) -> bool:
    """Return True iff this path falls into a security-exempt category."""
    p = Path(file_path)
    name = p.name
    if name in _EXCLUDE_EXACT_NAMES:
        return True
    if name.startswith(_EXCLUDE_FILENAME_PREFIXES) or name.endswith(_EXCLUDE_FILENAME_SUFFIXES):
        return True
    # Path components: directory match must be on a full segment, not a substring.
    return any(part in _EXCLUDE_DIRS for part in p.parts)


def check_secrets(file_path: str) -> list[dict]:
    """Check a file for hardcoded secrets using regex patterns."""
    if _is_excluded_path(file_path):
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
