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
# Phase38 (A3 audit): regex set + exclusion primitives moved to
# ``lib/secret_regexes.py`` so Tier 1 and V08 share one source of
# truth. Re-exported as the legacy module-level names for back-compat
# with any consumer that imported from this file.
from lib.secret_regexes import (
    EXCLUDE_DIRS as _EXCLUDE_DIRS,  # noqa: F401  (re-exported for back-compat)
    EXCLUDE_EXACT_NAMES as _EXCLUDE_EXACT_NAMES,  # noqa: F401
    EXCLUDE_FILENAME_PREFIXES as _EXCLUDE_FILENAME_PREFIXES,  # noqa: F401
    EXCLUDE_FILENAME_SUFFIXES as _EXCLUDE_FILENAME_SUFFIXES,  # noqa: F401
    SECRET_REGEXES,
    is_excluded_path as _is_excluded_path,
)


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
    # Phase38 (A5 audit): cap stdin at 1 MiB. Claude Code hook payloads
    # are far smaller than this; the cap exists to neutralize the
    # documented standalone CLI surface (``echo '{...}' | hook``) so a
    # misbehaving wrapper can't pipe gigabytes and pin the process.
    try:
        input_data = json.loads(sys.stdin.read(1_048_576))
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
