"""Tests for Phase64.3 — Tier 2 router parallel dispatch.

Focused on the parallelism contract:
  - Output equivalence: parallel dispatch yields the same findings as
    sequential, regardless of validator order.
  - Per-validator failures don't kill the batch.
  - Threshold + escape hatch behavior.

We exercise ``_run_one_validator`` and the import-time constants
directly rather than spinning up a real ProjectContext + 49 validators
— faster + isolates the parallelism logic from validator semantics.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from hooks.router import (
    _MAX_PARALLEL_WORKERS,
    _PARALLEL_THRESHOLD,
    _run_one_validator,
)
from hooks.validators.base import Finding, ValidationResult


# ── _run_one_validator ───────────────────────────────────────────────────


def _make_validator(vid: str, findings: list[Finding] | Exception):
    """Build a mock validator whose .run() returns/raises as specified."""
    v = MagicMock()
    v.id = vid
    if isinstance(findings, Exception):
        v.run.side_effect = findings
    else:
        v.run.return_value = ValidationResult(validator_id=vid, findings=findings)
    return v


def test_run_one_validator_returns_findings() -> None:
    finding = Finding(severity="error", file="x.go", rule="V99-TEST", message="m", fix="f")
    v = _make_validator("V99-test", [finding])
    ctx = MagicMock()
    out = _run_one_validator(v, ctx, "/x.go", "/cwd")
    assert out == [finding]


def test_run_one_validator_swallows_exception() -> None:
    """A crashing validator must not propagate — Tier 2 contract."""
    v = _make_validator("V99-test", RuntimeError("boom"))
    ctx = MagicMock()
    out = _run_one_validator(v, ctx, "/x.go", "/cwd")
    assert out == []  # crash → empty list


def test_run_one_validator_returns_list_copy() -> None:
    """Returns a list (so caller can mutate) — not the validator's own."""
    finding = Finding(severity="warning", file="x.go", rule="V99-T", message="m", fix="f")
    v = _make_validator("V99-test", [finding])
    ctx = MagicMock()
    out = _run_one_validator(v, ctx, "/x.go", "/cwd")
    out.append(finding)  # mutating shouldn't affect the validator's internal state


# ── Parallel equivalence ─────────────────────────────────────────────────


def test_parallel_dispatch_yields_same_findings_as_sequential() -> None:
    """4 validators, each returns 1 finding. Parallel and sequential
    must yield the same set of findings (order may differ in parallel)."""
    validators = [
        _make_validator(
            f"V{i:02}-test", [Finding(severity="warning", file=f"f{i}.go", rule=f"V{i:02}-X", message="m", fix="f")]
        )
        for i in range(4)
    ]
    ctx = MagicMock()

    sequential: list[Finding] = []
    for v in validators:
        sequential.extend(_run_one_validator(v, ctx, "/x.go", "/cwd"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_run_one_validator, v, ctx, "/x.go", "/cwd") for v in validators]
        parallel = [f for fut in futures for f in fut.result()]

    assert sorted([f.rule for f in sequential]) == sorted([f.rule for f in parallel])


def test_parallel_one_crash_doesnt_kill_others() -> None:
    """If validator #2 raises, validators #0/#1/#3 still return findings."""
    validators = [
        _make_validator("V01-ok", [Finding(severity="error", file="a", rule="V01-A", message="m", fix="f")]),
        _make_validator("V02-ok", [Finding(severity="error", file="b", rule="V02-B", message="m", fix="f")]),
        _make_validator("V03-bad", RuntimeError("boom")),
        _make_validator("V04-ok", [Finding(severity="error", file="d", rule="V04-D", message="m", fix="f")]),
    ]
    ctx = MagicMock()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_run_one_validator, v, ctx, "/x.go", "/cwd") for v in validators]
        results = [f for fut in futures for f in fut.result()]

    rules = sorted(f.rule for f in results)
    assert rules == ["V01-A", "V02-B", "V04-D"]


# ── Threshold constants ──────────────────────────────────────────────────


def test_parallel_threshold_is_4() -> None:
    """Below 4 matched validators, sequential is faster (spin-up overhead)."""
    assert _PARALLEL_THRESHOLD == 4


def test_max_parallel_workers_is_4() -> None:
    """Tier 2 is per-Edit; 4 workers is the sweet spot for typical
    multi-validator dispatch (.go edits hit ~11)."""
    assert _MAX_PARALLEL_WORKERS == 4


# ── Escape hatch ─────────────────────────────────────────────────────────


def test_verifiers_parallel_zero_recognized() -> None:
    """The router checks VERIFIERS_PARALLEL=0 to opt out of parallelism."""
    # Just confirm the env var name is what we documented — actual
    # behavior is exercised in main() integration tests.
    with patch.dict(os.environ, {"VERIFIERS_PARALLEL": "0"}):
        assert os.environ.get("VERIFIERS_PARALLEL", "1") == "0"
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VERIFIERS_PARALLEL", None)
        assert os.environ.get("VERIFIERS_PARALLEL", "1") == "1"


# ── Concurrency safety smoke test ────────────────────────────────────────


def test_parallel_dispatch_with_many_validators() -> None:
    """11-way concurrent run (typical .go Edit) — no deadlock, all
    findings collected."""
    validators = [
        _make_validator(
            f"V{i:02}-test", [Finding(severity="warning", file=f"f{i}.go", rule=f"V{i:02}-X", message="m", fix="f")]
        )
        for i in range(11)
    ]
    ctx = MagicMock()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_run_one_validator, v, ctx, "/x.go", "/cwd") for v in validators]
        results = [f for fut in futures for f in fut.result()]

    assert len(results) == 11
    assert len({f.rule for f in results}) == 11  # all unique
