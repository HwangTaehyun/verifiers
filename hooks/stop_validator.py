#!/usr/bin/env python3
"""Tier 3: Stop hook — full validation at end-of-turn.

Runs ALL validators (V01-V08) when Claude is about to end its turn.
If errors are found, blocks the turn (decision: "block") forcing
Claude to fix issues before responding to the user.

stdin: {"stop_reason": "end_turn", "cwd": "/project"}
stdout: {"decision": "approve"} or {"decision": "block", "additionalContext": "..."}
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

from hooks.validators.base import Finding, format_output, read_hook_input, write_hook_output
from lib.exclusion import (
    is_excluded,
    is_excluded_for_validator,
)
from lib.feedback_tracker import FeedbackTracker
from lib.json_logger import log_exception
from lib.parallel_runner import run_all
from lib.validator_registry import resolve_active_validators
from lib.project_context import ProjectContext


_MAX_CONSECUTIVE_BLOCKS = 3  # Approve after N consecutive blocks to prevent infinite loop


def _apply_exclude_filters(findings: list[Finding], ctx: ProjectContext) -> list[Finding]:
    """Drop findings whose file path is in ``exclude.paths`` (global) or
    in ``exclude.per_validator[<v-id>]`` for the rule's owning validator.

    Validator-id resolution: each Finding carries ``rule`` like
    ``V20-RAW-SQL-FORBIDDEN``; the V-ID prefix (``V20``) is what
    ``per_validator`` config keys match against. Both forms work in
    config — ``V20`` (prefix) and ``V20-hasura-graphql`` (full id) —
    because ``is_excluded_for_validator`` handles both internally.

    Findings without a file path (e.g., project-level proto warnings)
    can't be path-filtered and pass through unchanged.
    """
    paths = ctx.config.exclude.paths
    per_validator = ctx.config.exclude.per_validator
    if not paths and not per_validator:
        return findings

    out: list[Finding] = []
    for f in findings:
        # Phase36 (A4 audit): sentinel findings (V##-CRASHED, V##-TIMEOUT)
        # must never be silenced by exclude.paths. The whole point of
        # the sentinel is to surface a worker death; filtering it would
        # turn a crashed validator back into a silent approve.
        if f.kind == "sentinel":
            out.append(f)
            continue
        # Some findings (e.g., schema-level proto warnings) have file=""
        # or a non-file marker. Skip filtering for those.
        if not f.file:
            out.append(f)
            continue
        if paths and is_excluded(f.file, ctx.project_root, paths):
            continue
        if per_validator:
            vid_prefix = f.rule.split("-", 1)[0] if f.rule else ""
            if vid_prefix and is_excluded_for_validator(f.file, ctx.project_root, per_validator, vid_prefix):
                continue
        out.append(f)
    return out


def main() -> None:
    input_data = read_hook_input()
    # Phase38b (A5 audit): truncation warning before any other processing.
    truncated = input_data.get("_verifiers_stdin_truncated")
    if truncated:
        from hooks.validators.base import stdin_truncation_finding

        write_hook_output(format_output([stdin_truncation_finding(truncated)], mode="stop"))
        return
    if not input_data:
        write_hook_output({"decision": "approve"})
        return

    cwd = input_data.get("cwd", ".")

    # ── Circuit breaker: prevent infinite Stop hook loops ──
    # stop_hook_active=True means Claude is already continuing from a previous block.
    # We track consecutive blocks and approve after _MAX_CONSECUTIVE_BLOCKS to
    # prevent the agent from being stuck in an unbreakable loop.
    stop_hook_active = input_data.get("stop_hook_active", False)

    # Create project context
    ctx = ProjectContext(cwd)

    # P1-3: enabled allowlist + disabled deny-list (disabled wins).
    # Phase35 (A1 audit): the four-step filter pipeline is shared with
    # ``hooks/router.py`` via ``lib/validator_registry``. The hard-fail
    # on a non-empty allowlist matching zero validators stays — the
    # ``VERIFIERS-CONFIG-EMPTY-ALLOWLIST`` finding bubbles up so the
    # Stop hook never silent-approves on a typo.
    active, config_error = resolve_active_validators(ctx, source="stop_validator/resolve_active_validators")
    if config_error is not None:
        output = format_output([config_error], mode="stop")
        write_hook_output(output)
        return

    # Parallel by default (4 workers, 30s per-validator timeout). Set
    # VERIFIERS_PARALLEL=0 to fall back to the legacy sequential loop.
    # Crashed/timed-out validators contribute V##-CRASHED / V##-TIMEOUT
    # sentinel findings so the user can never get a silent false-approve.
    all_findings = run_all(active, ctx, mode="stop")

    # Post-filter findings by config.exclude — Tier 3 parity with Tier 2 router.
    # Without this filter the Stop hook would report violations that the
    # router (PostToolUse) already silenced, defeating the purpose of
    # `exclude.paths` / `exclude.per_validator` in stop mode.
    #
    # Reasons we filter findings (not files-pre-scan):
    #   1. validators here scan the project themselves; we don't have a
    #      single per-file dispatch point like router.py does.
    #   2. Findings carry the absolute file path that matched, so
    #      filtering them is straightforward and keeps validator
    #      internals unchanged.
    all_findings = _apply_exclude_filters(all_findings, ctx)

    # Track findings for repeated violation detection
    tracker = FeedbackTracker()
    tracker.record_all(all_findings)

    # If repeated violations detected, append feedback to output
    feedback_msg = tracker.format_feedback_message()
    output = format_output(all_findings, mode="stop")

    if feedback_msg and "additionalContext" in output:
        output["additionalContext"] += f"\n\n{feedback_msg}"
    elif feedback_msg:
        output["additionalContext"] = feedback_msg
        output["decision"] = "block"

    # ── Circuit breaker: if already in a stop-hook loop, limit retries ──
    # State lives at <cwd>/.verifiers/state/verifier-block-count to keep
    # everything verifier-owned inside its own ``.verifiers/`` namespace
    # (already used by V15 ``dependency_guard`` for ``.verifiers/layers.yaml``).
    # The legacy <cwd>/.verifier-block-count is read once for back-compat
    # then unlinked so users don't see a stale dotfile in the project root.
    # Per-worktree scope is intentional: the circuit breaker tracks
    # "this conversation keeps hitting the same block", and Claude
    # sessions are typically scoped to a single worktree.
    state_dir = Path(cwd) / ".verifiers" / "state"
    block_marker = state_dir / "verifier-block-count"
    legacy_marker = Path(cwd) / ".verifier-block-count"

    def _read_block_count() -> int:
        for marker in (block_marker, legacy_marker):
            try:
                if marker.exists():
                    return int(marker.read_text().strip())
            except (ValueError, OSError):
                continue
        return 0

    def _drop_legacy_marker() -> None:
        try:
            legacy_marker.unlink(missing_ok=True)
        except OSError:
            pass

    if stop_hook_active and output.get("decision") == "block":
        block_count = _read_block_count() + 1

        if block_count >= _MAX_CONSECUTIVE_BLOCKS:
            # Safety valve: let the agent through with warnings
            output["decision"] = "approve"
            circuit_msg = (
                f"\n\n⚠️ CIRCUIT BREAKER: {block_count} consecutive stop-hook blocks. "
                f"Approving to prevent infinite loop. "
                f"{len([f for f in all_findings if f.severity == 'error'])} unresolved error(s) remain. "
                f'Run `echo \'{{"cwd": "{cwd}"}}\' | uv run --script stop_validator.py` '
                f"to see full details."
            )
            output.setdefault("additionalContext", "")
            output["additionalContext"] += circuit_msg
            # Remove reason since we're approving
            output.pop("reason", None)
            # Reset counter
            try:
                block_marker.unlink(missing_ok=True)
            except OSError:
                pass
            _drop_legacy_marker()
        else:
            # Persist block count
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                block_marker.write_text(str(block_count))
            except OSError:
                pass
            _drop_legacy_marker()
    else:
        # Not in a loop or approved — reset counter
        try:
            block_marker.unlink(missing_ok=True)
        except OSError:
            pass
        _drop_legacy_marker()

    # Persist session feedback for cross-session analysis
    try:
        tracker.save_session()
    except Exception as exc:
        # Persistence failure should never block, but record so cross-session
        # feedback drops are diagnosable (P0-4).
        log_exception(
            source="stop_validator/FeedbackTracker.save_session",
            error=exc,
            context={"cwd": cwd},
        )

    write_hook_output(output)


if __name__ == "__main__":
    main()
