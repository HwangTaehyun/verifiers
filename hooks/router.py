#!/usr/bin/env python3
"""Tier 2 router (P2-1): runs file-pattern-matched validators after Edit/Write.

Registered on ``PostToolUse`` alongside the Tier 1 ``security_hook.py``
when ``scripts/merge_settings.py`` runs. Two cost-saving prefilters
keep this cheap on every Edit:

  1. **Extension prefilter** — if no registered validator's
     ``should_run(file_path)`` returns True (e.g. a Markdown edit when
     no validator cares about ``.md``), short-circuit before doing any
     work.
  2. **Content-hash cache** — if the file's current bytes match the
     hash recorded by the previous router run for the same path, skip.
     Lives at ``<cwd>/.verifiers/state/router-cache.json``.

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
from lib.exclusion import (
    filter_disabled_validators,
    filter_enabled_validators,
    is_excluded,
    is_excluded_for_validator,
)
from lib.json_logger import log_exception
from lib.project_context import ProjectContext
from lib.router_cache import file_content_hash, load_cache, record_hit, save_cache, should_skip


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

    # ── P1-4: project-configured exclusions ───────────────────────────
    if is_excluded(file_path, ctx.project_root, ctx.config.exclude.paths):
        write_hook_output({})
        return

    # ── P1-3: enabled allowlist + disabled deny-list ────────────────
    # ``enabled`` is applied first as a strict allowlist (empty = no
    # filter), then ``disabled`` subtracts from whatever remains so it
    # always wins on conflict (matches the README's documented order).
    active = filter_enabled_validators(get_all_validators(), ctx.config.validators.enabled)
    active = filter_disabled_validators(active, ctx.config.validators.disabled)

    # ── Phase15: per-validator file exclusion ────────────────────────
    # Drop validators that the user told to skip *this specific file*
    # (e.g. ``exclude.per_validator.V14: ["legacy/**"]``). Other
    # validators still see the file normally.
    per_v = ctx.config.exclude.per_validator
    active = [v for v in active if not is_excluded_for_validator(file_path, ctx.project_root, per_v, v.id)]

    # ── P2-1 prefilter 1: extension matching ─────────────────────────
    # If no active validator declares interest in this file, exit
    # immediately — saves the cost of opening + hashing the file
    # for every Markdown / lockfile / yaml edit Claude makes.
    matching = [v for v in active if v.should_run(file_path)]
    if not matching:
        write_hook_output({})
        return

    # ── P2-1 prefilter 2: content-hash cache ─────────────────────────
    # If the file's bytes exactly match what the router last saw
    # (e.g. an Edit whose new_string equalled the existing content),
    # skip the validator suite entirely.
    cache = load_cache(ctx.project_root)
    current_hash = file_content_hash(file_path)
    if should_skip(cache, file_path, current_hash):
        write_hook_output({})
        return

    # ── Run matching validators ──────────────────────────────────────
    all_findings: list[Finding] = []

    for validator in matching:
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

    # Update the cache so an immediately-following Edit on the same
    # file with identical content takes the fast path next time.
    record_hit(cache, file_path, current_hash)
    save_cache(ctx.project_root, cache)

    output = format_output(all_findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
