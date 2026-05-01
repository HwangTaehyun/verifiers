"""Parallel validator execution for Tier 3.

Phase36 audit fix-in (A2 + A4 + A7):

* **A2** ProcessPoolExecutor → ThreadPoolExecutor. Every Tier 3
  validator that costs real time shells out via ``subprocess.run``
  (ruff, golangci-lint, pytest, tsc, eslint, ...) which releases the
  GIL for the duration of the child process. Threads parallelize those
  subprocesses just as well as processes, without paying ~200 ms per
  Stop hook for spawn + ProjectContext pickling. The ``pickle.PicklingError``
  fallback path that Phase12 introduced for ProcessPoolExecutor is
  retired here — threads share the parent's address space, no pickling.

* **A7** ``DEFAULT_MAX_WORKERS = min(8, len(validators))``. Once V19
  (Phase28 split) and the rest of the long tail (golangci, eslint,
  ruff_all) can each take a slot, four workers became the bottleneck.
  Threads have no spawn cost so the bump is free.

* **A4** Sentinel findings (``V##-CRASHED`` / ``V##-TIMEOUT``) now set
  ``kind="sentinel"``. ``stop_validator._apply_exclude_filters`` skips
  filtering on sentinels, so a config like ``exclude.paths: ["**"]``
  can no longer silence a crashed worker — that would let a sentinel
  emit a silent approve, defeating the whole point of having one.

The ``transport-CRASHED`` outer branch from Phase12 is gone. With
threads, an exception inside the worker bubbles up via
``future.result()``; the inner sentinel in ``_run_one_validator``
already covers it. There is no separate transport layer to fail.

Opt-out: ``VERIFIERS_PARALLEL=0`` in the environment falls back to a
sequential loop (same in-process semantics, just no thread pool).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import TYPE_CHECKING

from hooks.validators.base import Finding, ValidationResult
from lib.json_logger import log_exception

if TYPE_CHECKING:
    from hooks.validators.base import BaseValidator
    from lib.project_context import ProjectContext


# Phase36 (A7): bumped from 4 to a project-size-aware default. Threads
# have negligible startup cost, so up to 8 concurrent slots simply
# shortens the wait when several heavy validators (ruff_all + tsc +
# eslint + pytest) all want to run.
DEFAULT_MAX_WORKERS = 8
DEFAULT_PER_VALIDATOR_TIMEOUT = 30  # seconds


def _is_parallel_enabled() -> bool:
    """Honor the VERIFIERS_PARALLEL=0 opt-out."""
    return os.environ.get("VERIFIERS_PARALLEL", "1") != "0"


def _vid_prefix(validator_id: str) -> str:
    """Extract V-NN prefix from a validator id (``V14-complexity-guard`` → ``V14``)."""
    return validator_id.split("-", 1)[0]


def _crash_finding(validator_id: str, project_root: str, exc: BaseException) -> Finding:
    """Build the ``V##-CRASHED`` sentinel for a worker exception."""
    return Finding(
        severity="warning",
        file=project_root,
        rule=f"{_vid_prefix(validator_id)}-CRASHED",
        message=f"Validator {validator_id} crashed: {type(exc).__name__}: {exc}",
        fix=(
            "Check logs/_errors.jsonl for the traceback. Set VERIFIERS_PARALLEL=0 to rule out a parallelization issue."
        ),
        kind="sentinel",
    )


def _timeout_finding(validator_id: str, project_root: str, timeout: int) -> Finding:
    """Build the ``V##-TIMEOUT`` sentinel for a per-validator timeout."""
    return Finding(
        severity="warning",
        file=project_root,
        rule=f"{_vid_prefix(validator_id)}-TIMEOUT",
        message=f"Validator {validator_id} exceeded the {timeout}s per-validator timeout.",
        fix=(
            "Investigate why this check is slow on the current project. "
            "Configure ``.verifiers/config.yaml`` validators.disabled to skip "
            f"if it's not relevant, or run ``just verify-one`` for {validator_id} "
            "in isolation to inspect."
        ),
        kind="sentinel",
    )


def _run_one_validator(
    validator: "BaseValidator",
    ctx: "ProjectContext",
    mode: str,
) -> ValidationResult:
    """Worker entry point. Catches every Exception and converts it to a
    ``V##-CRASHED`` sentinel so a single crashed validator never
    silences the whole Stop hook."""
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
            findings=[_crash_finding(validator.id, str(getattr(ctx, "project_root", "")), exc)],
        )


def _run_sequential(
    validators: "list[BaseValidator]",
    ctx: "ProjectContext",
    mode: str,
) -> list[Finding]:
    """Sequential fallback used when ``VERIFIERS_PARALLEL=0``."""
    findings: list[Finding] = []
    for v in validators:
        result = _run_one_validator(v, ctx, mode)
        findings.extend(result.findings)
    return findings


def _resolve_timeout(validator_id: str, ctx: "ProjectContext", default: int) -> int:
    """Phase62-N2: pick per-validator timeout from ctx.config.timeouts.

    Falls back to the runner default when no override is configured.
    Validator id `V19-py-quality` → prefix `V19` lookup. Bound to a
    minimum of 1 second to avoid 0-second timeouts that would always
    fire.
    """
    try:
        timeouts = ctx.config.timeouts
    except AttributeError:
        return default
    prefix = _vid_prefix(validator_id)
    return max(1, timeouts.per_validator.get(prefix, timeouts.default or default))


def run_all(
    validators: "list[BaseValidator]",
    ctx: "ProjectContext",
    mode: str,
    *,
    max_workers: int | None = None,
    per_validator_timeout: int = DEFAULT_PER_VALIDATOR_TIMEOUT,
) -> list[Finding]:
    """Run every validator in parallel and return all findings.

    Parallelism uses ``ThreadPoolExecutor``: every heavy validator
    blocks on ``subprocess.run`` (which releases the GIL), so threads
    parallelize as well as processes without spawn cost or pickling.

    A timed-out validator contributes a single ``V##-TIMEOUT`` sentinel
    (``kind="sentinel"``) instead of dropping silently — preserving the
    Stop hook's "if it approved, the checks ran" guarantee.

    Returns:
        Flat list of findings across all validators. Order is not
        stable across runs (futures complete out of order), but no
        downstream consumer (``format_output``, ``FeedbackTracker``,
        ``_dedup_findings``) depends on ordering.
    """
    if not validators:
        return []

    if not _is_parallel_enabled():
        return _run_sequential(validators, ctx, mode)

    if max_workers is None:
        max_workers = min(DEFAULT_MAX_WORKERS, len(validators))

    findings: list[Finding] = []
    project_root = str(getattr(ctx, "project_root", ""))

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="verifier") as pool:
        futures = {pool.submit(_run_one_validator, v, ctx, mode): v for v in validators}
        for future in as_completed(futures, timeout=None):
            validator = futures[future]
            # Phase62-N2: pick per-validator timeout from config.
            v_timeout = _resolve_timeout(validator.id, ctx, per_validator_timeout)
            try:
                result = future.result(timeout=v_timeout)
                findings.extend(result.findings)
            except TimeoutError:
                log_exception(
                    source=f"parallel_runner/{validator.id}",
                    error=TimeoutError(f"per-validator {v_timeout}s budget exceeded"),
                    context={"mode": mode},
                )
                findings.append(_timeout_finding(validator.id, project_root, v_timeout))
                future.cancel()
    return findings
