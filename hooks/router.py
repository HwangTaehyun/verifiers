#!/usr/bin/env python3
"""Tier 2 full activation: Routes to matching validators by file pattern.

Used by /verify skill (full activation) — runs all validators that match
the modified file's pattern.

stdin: {"tool_name": "Edit", "tool_input": {"file_path": "/path"}, "cwd": "/project"}
stdout: {"additionalContext": "..."} or {}
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks.validators import get_all_validators
from hooks.validators.base import Finding, format_output, read_hook_input, write_hook_output
from lib.project_context import ProjectContext


def main() -> None:
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    # Create project context
    ctx = ProjectContext(cwd)

    # Collect findings from all matching validators
    all_findings: list[Finding] = []

    for validator in get_all_validators():
        if validator.should_run(file_path):
            try:
                result = validator.run(ctx, file_path, mode="post_tool_use")
                all_findings.extend(result.findings)
            except Exception:
                pass  # Individual validator failure shouldn't block others

    output = format_output(all_findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
