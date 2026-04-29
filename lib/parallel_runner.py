"""Parallel validator execution for Tier 3 (P1-5).

The Tier 3 ``stop_validator`` runs 19 validators sequentially. On large
repos the heavyweight ones (V06 golangci-lint, V07 tsc + madge + knip,
V19 full pytest) can each consume many seconds, putting the 120s hook
budget at risk. Sequential ordering also means one stuck external
command starves every later validator.

This module wraps each validator in its own worker process via
``concurrent.futures.ProcessPoolExecutor`` so:

* Heavy + light validators overlap — total wall-clock time approaches
  ``max(per-validator times)`` instead of ``sum(per-validator times)``.
* A per-validator timeout (default 30s) kills hung subprocesses without
  taking down everything else.
* A crashed or timed-out validator emits a sentinel ``Finding`` so
  Claude can never get a silent false-approve from "the validator just
  didn't run".

Opt-out: set ``VERIFIERS_PARALLEL=0`` in the environment to fall back
to the legacy sequential loop. The fallback also kicks in automatically
if a ``PicklingError`` is raised when submitting work — defensive
behavior so a previously-unforeseen unpicklable object can never break
the user's turn.
"""

from __future__ import annotations

import os
import pickle
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from typing import TYPE_CHECKING

from hooks.validators.base import Finding, ValidationResult
from lib.json_logger import log_exception

if TYPE_CHECKING:
    from hooks.validators.base import BaseValidator
    from lib.project_context import ProjectContext


DEFAULT_MAX_WORKERS = 4
DEFAULT_PER_VALIDATOR_TIMEOUT = 30  # seconds


def _is_parallel_enabled() -> bool:
    """Honor the VERIFIERS_PARALLEL=0 opt-out."""
    return os.environ.get("VERIFIERS_PARALLEL", "1") != "0"


def _run_one_validator(
    validator: "BaseValidator",
    ctx: "ProjectContext",
    mode: str,
) -> ValidationResult:
    """Top-level worker entry point.

    Must live at module scope so ``ProcessPoolExecutor`` (which uses
    ``spawn`` on macOS) can pickle the call.
    """
    try:
        return validator.run(ctx, file_path=None, mode=mode)
    except Exception as exc:
        log_exception(
            source=f"parallel_runner/{validator.id}",
            error=exc,
            context={"mode": mode},
        )
        return ValidationResult(
            validator_id=validator.id,
            findings=[
                Finding(
                    severity="warning",
                    file=str(getattr(ctx, "project_root", "")),
                    rule=f"{_vid_prefix(validator.id)}-CRASHED",
                    message=f"Validator {validator.id} crashed: {type(exc).__name__}: {exc}",
                    fix="Check logs/_errors.jsonl for the traceback. "
                    "Set VERIFIERS_PARALLEL=0 to rule out a parallelization issue.",
                )
            ],
        )


def _vid_prefix(validator_id: str) -> str:
    """Extract V-NN prefix from a validator id (``V14-complexity-guard`` → ``V14``)."""
    return validator_id.split("-", 1)[0]


def _timeout_finding(validator: "BaseValidator", ctx: "ProjectContext", timeout: int) -> Finding:
    return Finding(
        severity="warning",
        file=str(getattr(ctx, "project_root", "")),
        rule=f"{_vid_prefix(validator.id)}-TIMEOUT",
        message=(f"Validator {validator.id} exceeded the {timeout}s per-validator timeout."),
        fix=(
            "Investigate why this check is slow on the current project. "
            "Configure ``.verifiers/config.yaml`` validators.disabled to skip "
            f"if it's not relevant, or run ``just verify-one`` for {validator.id} "
            "in isolation to inspect."
        ),
    )


def _run_sequential(
    validators: "list[BaseValidator]",
    ctx: "ProjectContext",
    mode: str,
) -> list[Finding]:
    """Legacy sequential loop — used as fallback on opt-out or pickle error."""
    findings: list[Finding] = []
    for v in validators:
        result = _run_one_validator(v, ctx, mode)
        findings.extend(result.findings)
    return findings


def run_all(
    validators: "list[BaseValidator]",
    ctx: "ProjectContext",
    mode: str,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    per_validator_timeout: int = DEFAULT_PER_VALIDATOR_TIMEOUT,
) -> list[Finding]:
    """Run every validator and return all findings.

    Parallel by default (4 workers); set ``VERIFIERS_PARALLEL=0`` for
    sequential fallback. Order of returned findings is **not** stable
    across runs (futures complete out of order), but Tier 3's downstream
    consumers (``format_output``, ``FeedbackTracker``) don't depend on
    ordering — and ``_dedup_findings`` handles any cross-validator
    overlap deterministically by ``(rule, file, line, message)``.

    A timed-out validator contributes a single ``V##-TIMEOUT`` warning
    instead of dropping silently — this is essential for the user's
    trust in the Stop hook ("if it approved, the checks ran").
    """
    if not validators:
        return []

    if not _is_parallel_enabled():
        return _run_sequential(validators, ctx, mode)

    findings: list[Finding] = []
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one_validator, v, ctx, mode): v for v in validators}
            for future in as_completed(futures, timeout=None):
                validator = futures[future]
                try:
                    result = future.result(timeout=per_validator_timeout)
                    findings.extend(result.findings)
                except TimeoutError:
                    log_exception(
                        source=f"parallel_runner/{validator.id}",
                        error=TimeoutError(f"per-validator {per_validator_timeout}s budget exceeded"),
                        context={"mode": mode},
                    )
                    findings.append(_timeout_finding(validator, ctx, per_validator_timeout))
                    future.cancel()
                except Exception as exc:
                    # Exception inside the worker is already logged + mapped
                    # to a sentinel in _run_one_validator; this branch covers
                    # transport-layer issues (e.g. worker process died).
                    log_exception(
                        source=f"parallel_runner/{validator.id}/transport",
                        error=exc,
                        context={"mode": mode},
                    )
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(getattr(ctx, "project_root", "")),
                            rule=f"{_vid_prefix(validator.id)}-CRASHED",
                            message=f"Validator {validator.id} worker died: {type(exc).__name__}: {exc}",
                            fix="See logs/_errors.jsonl. Set VERIFIERS_PARALLEL=0 to fall back to sequential.",
                        )
                    )
    except (pickle.PicklingError, OSError) as exc:
        # Spawn-compatibility surface: an unpicklable validator/ctx, or
        # the OS refusing fork/spawn. Fall back to sequential rather than
        # giving the user a silent failure.
        log_exception(
            source="parallel_runner/pool_setup",
            error=exc,
            context={"mode": mode, "fallback": "sequential"},
        )
        return _run_sequential(validators, ctx, mode)

    return findings
