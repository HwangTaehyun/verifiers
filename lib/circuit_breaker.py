"""Stop-hook circuit breaker — Phase 71 L4 extraction.

Until Phase 71 this lived inline in ``hooks/stop_validator.py:236-301``.
Pulled out into its own module per the architecture review (M2/L4):
keep ``stop_validator.main()`` focused on the dispatch happy path so
the circuit-breaker semantics aren't drowned out by 60 lines of state-
file housekeeping.

## Why circuit breaker

Claude Code's Stop hook can re-fire if it returned ``decision: "block"``
(``stop_hook_active=True``). If the agent gets stuck on the same
finding it can't fix, the hook loops indefinitely and the user sees
nothing happen. The circuit breaker counts consecutive ``block``
returns; on the Nth (default 3) it converts to ``approve`` so the
session always makes forward progress, even at the cost of an
unresolved finding the user has to address manually.

## State

The block counter lives at::

    <cwd>/.verifiers/state/verifier-block-count

A legacy marker (``<cwd>/.verifier-block-count``, pre-Phase-35) is
read once for back-compat then unlinked so a stale dotfile doesn't
linger in the project root. Per-worktree scope is intentional —
Claude sessions track conversation context tied to a single worktree.

## Usage

::

    output = format_output(findings, mode="stop")
    output = apply_circuit_breaker(
        cwd=cwd,
        output=output,
        findings=findings,
        stop_hook_active=stop_hook_active,
    )
    write_hook_output(output)

The function mutates ``output`` in place AND returns it for ergonomics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.validators_core import Finding

# Phase 35 (A1 audit): the block counter triggers the safety-valve
# approve at this many consecutive blocks. 3 is empirically the right
# tradeoff — high enough that legitimate fix iterations succeed, low
# enough that genuinely stuck loops bail out before the user gets
# bored.
DEFAULT_MAX_CONSECUTIVE_BLOCKS = 3


def apply_circuit_breaker(
    *,
    cwd: str,
    output: dict[str, Any],
    findings: list[Finding],
    stop_hook_active: bool,
    max_consecutive_blocks: int = DEFAULT_MAX_CONSECUTIVE_BLOCKS,
) -> dict[str, Any]:
    """Apply the consecutive-block circuit breaker to ``output``.

    Reads the persisted block counter at
    ``<cwd>/.verifiers/state/verifier-block-count`` (legacy
    ``<cwd>/.verifier-block-count`` is migrated transparently). When
    ``stop_hook_active`` is True and the new count would meet or exceed
    ``max_consecutive_blocks``, flips ``output["decision"]`` to
    ``approve`` with an ``additionalContext`` explaining the trip and
    drops ``reason`` (since we're no longer blocking).

    On successful approve OR when ``stop_hook_active`` is False, the
    counter resets to 0 (file deleted).

    Mutates ``output`` in place and returns it.
    """
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

        if block_count >= max_consecutive_blocks:
            # Safety valve: let the agent through with warnings
            output["decision"] = "approve"
            circuit_msg = (
                f"\n\n⚠️ CIRCUIT BREAKER: {block_count} consecutive stop-hook blocks. "
                f"Approving to prevent infinite loop. "
                f"{len([f for f in findings if f.severity == 'error'])} unresolved error(s) remain. "
                f'Run `echo \'{{"cwd": "{cwd}"}}\' | uv run --script stop_validator.py` '
                f"to see full details."
            )
            output.setdefault("additionalContext", "")
            output["additionalContext"] += circuit_msg
            output.pop("reason", None)
            try:
                block_marker.unlink(missing_ok=True)
            except OSError:
                pass
            _drop_legacy_marker()
        else:
            try:
                state_dir.mkdir(parents=True, exist_ok=True)
                block_marker.write_text(str(block_count))
            except OSError:
                pass
            _drop_legacy_marker()
    else:
        try:
            block_marker.unlink(missing_ok=True)
        except OSError:
            pass
        _drop_legacy_marker()

    return output
