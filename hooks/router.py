#!/usr/bin/env python3
"""Tier 2 router (P2-1): runs file-pattern-matched validators after Edit/Write.

Registered on ``PostToolUse`` alongside the Tier 1 ``security_hook.py``
when ``scripts/merge_settings.py`` runs. Three cost-saving prefilters
keep this cheap on every Edit:

  1. **Extension-bucketed dispatch** (Phase64.2) — ``get_matching_validators``
     consults a pre-built ``ext → [validators]`` index so a typical
     ``.go`` Edit only does regex matching against ~12 candidates, not
     all 49.
  2. **Content-hash cache** — if the file's current bytes match the
     hash recorded by the previous router run for the same path, skip.
     Lives at ``<cwd>/.verifiers/state/router-cache.json``.
  3. **Parallel dispatch** (Phase64.3) — when 4+ validators match a
     file (e.g. a ``.go`` Edit hitting V06+V09+V14+V15+V25+V27+V34+V35+V36+V38+V39),
     the per-validator subprocess work runs concurrently via
     ThreadPoolExecutor. Below that threshold the sequential path runs
     so the parallelism overhead doesn't outweigh the savings.

stdin: {"tool_name": "Edit", "tool_input": {"file_path": "/path"}, "cwd": "/project"}
stdout: {"additionalContext": "..."} or {}
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Add parent directory to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks.validators import get_matching_validators
from hooks.validators.base import Finding, format_output, read_hook_input, write_hook_output
from lib.exclusion import (
    is_excluded,
    is_excluded_for_validator,
)
from lib.validator_registry import resolve_active_validators
from lib.json_logger import log_exception
from lib.project_context import ProjectContext
from lib.router_cache import file_content_hash, load_cache, record_hit, save_cache, should_skip


# Phase64.3: parallelize router dispatch when many validators match.
# Below this threshold the ThreadPool spin-up cost (~1-3ms) outweighs
# any benefit from parallel subprocess execution.
_PARALLEL_THRESHOLD = 4
_MAX_PARALLEL_WORKERS = 4


def _run_one_validator(validator, ctx: ProjectContext, file_path: str, cwd: str) -> list[Finding]:
    """Worker entry point for parallel router dispatch.

    Mirrors the sequential try/except pattern: a single validator
    crashing must not abort the rest. Phase36's sentinel approach for
    Tier 3 isn't strictly needed here — Tier 2 already ignores per-
    validator failures and just logs them — so we keep that contract.
    """
    try:
        result = validator.run(ctx, file_path, mode="post_tool_use")
        return list(result.findings)
    except Exception as exc:
        log_exception(
            source=f"router/{validator.id}",
            error=exc,
            context={"file_path": file_path, "cwd": cwd, "mode": "post_tool_use"},
        )
        return []


def main() -> None:
    input_data = read_hook_input()
    # Phase38b (A5 audit): if read_hook_input hit the stdin cap, surface
    # a warning instead of silent-passing on truncated input.
    truncated = input_data.get("_verifiers_stdin_truncated")
    if truncated:
        from hooks.validators.base import stdin_truncation_finding

        write_hook_output(format_output([stdin_truncation_finding(truncated)], mode="post_tool_use"))
        return
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
    # Phase35 (A1 audit): the four-step allowlist + ValueError-handle +
    # denylist pipeline lives in ``lib/validator_registry`` so router
    # and stop_validator share one implementation.
    active, config_error = resolve_active_validators(ctx, source="router/resolve_active_validators")
    if config_error is not None:
        output = format_output([config_error], mode="post_tool_use")
        write_hook_output(output)
        return

    # ── Phase15: per-validator file exclusion ────────────────────────
    # Drop validators that the user told to skip *this specific file*
    # (e.g. ``exclude.per_validator.V14: ["legacy/**"]``). Other
    # validators still see the file normally.
    per_v = ctx.config.exclude.per_validator
    active = [v for v in active if not is_excluded_for_validator(file_path, ctx.project_root, per_v, v.id)]

    # ── P2-1 prefilter 1: extension matching (Phase64.2 fast path) ────
    # If no active validator declares interest in this file, exit
    # immediately — saves the cost of opening + hashing the file
    # for every Markdown / lockfile / yaml edit Claude makes.
    #
    # Phase64.2: ``get_matching_validators`` consults a pre-built
    # ext → [validators] index (cached at registry import time) and
    # narrows to the bucket for this file's suffix BEFORE running
    # ``should_run``. For a typical ``.go`` Edit this drops the regex
    # match count from 49 (all validators) to ~12 (Go-relevant + filename
    # patterns + wildcard). Identical end-result; lower per-Edit cost.
    matching = get_matching_validators(file_path, active)
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
    # Phase64.3: parallelize when 4+ validators matched. A typical .go
    # edit hits ~11 validators (V06+V09+V14+V15+V25+V27+V34+V35+V36+V38+V39);
    # going from sequential to ThreadPool(4w) saves ~200-400ms because
    # most of the work is subprocess.run (govet/gofmt/build/ruff/etc)
    # which releases the GIL.
    all_findings: list[Finding] = []
    parallel_disabled = os.environ.get("VERIFIERS_PARALLEL", "1") == "0"

    if not parallel_disabled and len(matching) >= _PARALLEL_THRESHOLD:
        workers = min(_MAX_PARALLEL_WORKERS, len(matching))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="router") as pool:
            futures = [pool.submit(_run_one_validator, v, ctx, file_path, cwd) for v in matching]
            for fut in futures:
                # No per-validator timeout here — Tier 2 already runs under the
                # PostToolUse 60s budget and individual checks are short.
                # Phase36's parallel_runner sentinel pattern is Tier 3-specific:
                # Tier 2 historically swallows failures (the pre-Phase64.3 loop
                # did try/except + log_exception), so we preserve that contract.
                all_findings.extend(fut.result())
    else:
        for validator in matching:
            all_findings.extend(_run_one_validator(validator, ctx, file_path, cwd))

    # Update the cache so an immediately-following Edit on the same
    # file with identical content takes the fast path next time.
    record_hit(cache, file_path, current_hash)
    save_cache(ctx.project_root, cache)

    output = format_output(all_findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
