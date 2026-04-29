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

from hooks.validators import get_all_validators
from hooks.validators.base import format_output, read_hook_input, write_hook_output
from lib.exclusion import filter_disabled_validators
from lib.feedback_tracker import FeedbackTracker
from lib.json_logger import log_exception
from lib.parallel_runner import run_all
from lib.project_context import ProjectContext


_MAX_CONSECUTIVE_BLOCKS = 3  # Approve after N consecutive blocks to prevent infinite loop


def main() -> None:
    input_data = read_hook_input()
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

    # Run ALL validators in stop mode (comprehensive check), minus any that
    # the project disabled in .verifiers/config.yaml (P1-3).
    active = filter_disabled_validators(get_all_validators(), ctx.config.validators.disabled)

    # Parallel by default (4 workers, 30s per-validator timeout). Set
    # VERIFIERS_PARALLEL=0 to fall back to the legacy sequential loop.
    # Crashed/timed-out validators contribute V##-CRASHED / V##-TIMEOUT
    # sentinel findings so the user can never get a silent false-approve.
    all_findings = run_all(active, ctx, mode="stop")

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
