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
from lib.exclusion import filter_disabled_validators, is_excluded
from lib.json_logger import log_exception
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

    # Create project context (loads .verifiers/config.yaml if present)
    ctx = ProjectContext(cwd)

    # P1-4: respect ctx.config.exclude.paths — files matching the project's
    # exclusion globs are skipped before any validator runs.
    if is_excluded(file_path, ctx.project_root, ctx.config.exclude.paths):
        write_hook_output({})
        return

    # P1-3: drop validators the project explicitly disabled.
    active = filter_disabled_validators(get_all_validators(), ctx.config.validators.disabled)

    # Collect findings from all matching validators
    all_findings: list[Finding] = []

    for validator in active:
        if validator.should_run(file_path):
            try:
                result = validator.run(ctx, file_path, mode="post_tool_use")
                all_findings.extend(result.findings)
            except Exception as exc:
                # Individual validator failure shouldn't block others — but
                # we record it so debugging is possible (P0-4).
                log_exception(
                    source=f"router/{validator.id}",
                    error=exc,
                    context={"file_path": file_path, "cwd": cwd, "mode": "post_tool_use"},
                )

    output = format_output(all_findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
