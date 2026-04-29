"""Tests for lib/parallel_runner.py — Tier 3 parallelization (P1-5).

The runner has two important contracts:

1. The Stop hook never silently approves a project that wasn't actually
   checked. A crashed or timed-out validator MUST contribute a sentinel
   ``Finding`` so the user sees something rather than nothing.

2. ``VERIFIERS_PARALLEL=0`` falls back to a faithful sequential loop —
   used by ``tests/test_stop_validator.py`` and also as a production
   escape hatch.

Tests run with ``VERIFIERS_PARALLEL=0`` by default to keep the suite
fast and avoid spinning up subprocess pools per case; one explicit
parallel-mode case exercises the full ProcessPoolExecutor path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hooks.validators.base import Finding, ValidationResult
from lib.config_loader import VerifiersConfig
from lib.parallel_runner import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_PER_VALIDATOR_TIMEOUT,
    _run_one_validator,
    _vid_prefix,
    run_all,
)


# ---------------------------------------------------------------------------
# Test doubles — picklable validators + minimal ctx
# ---------------------------------------------------------------------------


@dataclass
class _StubCtx:
    """Pickled into worker; must not contain unpicklable handles."""

    project_root: Path
    config: VerifiersConfig = field(default_factory=VerifiersConfig)


@dataclass
class _PassValidator:
    id: str = "V99-pass"

    def run(self, ctx, file_path=None, mode="stop"):
        return ValidationResult(
            validator_id=self.id,
            findings=[
                Finding(
                    severity="info",
                    file=str(ctx.project_root),
                    rule="V99-OK",
                    message="ok",
                    fix="n/a",
                )
            ],
        )


@dataclass
class _CrashValidator:
    id: str = "V98-crash"

    def run(self, ctx, file_path=None, mode="stop"):
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# 1. _vid_prefix
# ---------------------------------------------------------------------------


class TestVidPrefix:
    def test_extracts_prefix(self) -> None:
        assert _vid_prefix("V14-complexity-guard") == "V14"

    def test_no_dash(self) -> None:
        assert _vid_prefix("V99") == "V99"


# ---------------------------------------------------------------------------
# 2. _run_one_validator — happy path + crash mapping
# ---------------------------------------------------------------------------


class TestRunOneValidator:
    def test_happy_path_returns_validation_result(self, tmp_path: Path) -> None:
        ctx = _StubCtx(project_root=tmp_path)
        result = _run_one_validator(_PassValidator(), ctx, mode="stop")
        assert isinstance(result, ValidationResult)
        assert result.validator_id == "V99-pass"
        assert any(f.rule == "V99-OK" for f in result.findings)

    def test_crash_emits_sentinel_finding(self, tmp_path: Path) -> None:
        ctx = _StubCtx(project_root=tmp_path)
        result = _run_one_validator(_CrashValidator(), ctx, mode="stop")
        assert result.validator_id == "V98-crash"
        # Sentinel finding so the Stop hook can never silent-approve.
        assert any(f.rule == "V98-CRASHED" for f in result.findings)
        crash = next(f for f in result.findings if f.rule == "V98-CRASHED")
        assert crash.severity == "warning"
        assert "boom" in crash.message


# ---------------------------------------------------------------------------
# 3. run_all — sequential fallback (VERIFIERS_PARALLEL=0)
# ---------------------------------------------------------------------------


class TestRunAllSequential:
    @pytest.fixture(autouse=True)
    def force_sequential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERIFIERS_PARALLEL", "0")

    def test_empty_validators_returns_empty(self, tmp_path: Path) -> None:
        ctx = _StubCtx(project_root=tmp_path)
        assert run_all([], ctx, mode="stop") == []

    def test_aggregates_findings(self, tmp_path: Path) -> None:
        ctx = _StubCtx(project_root=tmp_path)
        validators = [_PassValidator(id=f"V0{i}-pass") for i in range(3)]
        findings = run_all(validators, ctx, mode="stop")
        assert len(findings) == 3
        ids = {f.rule for f in findings}
        assert ids == {"V99-OK"}

    def test_crash_does_not_starve_siblings(self, tmp_path: Path) -> None:
        ctx = _StubCtx(project_root=tmp_path)
        validators = [_PassValidator(), _CrashValidator(), _PassValidator(id="V97-pass")]
        findings = run_all(validators, ctx, mode="stop")
        rules = {f.rule for f in findings}
        # Both pass validators delivered and the crash sentinel is present.
        assert "V99-OK" in rules
        assert "V98-CRASHED" in rules


# ---------------------------------------------------------------------------
# 4. Defaults — public API surface
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_max_workers_is_sane(self) -> None:
        assert 2 <= DEFAULT_MAX_WORKERS <= 16

    def test_per_validator_timeout_under_hook_budget(self) -> None:
        # Tier 3 hook timeout is 120s; per-validator must leave room.
        assert DEFAULT_PER_VALIDATOR_TIMEOUT <= 60


# ---------------------------------------------------------------------------
# 5. Parallel mode — single end-to-end smoke test
# ---------------------------------------------------------------------------


class TestRunAllParallelSmoke:
    """Real ProcessPoolExecutor path, one case to prove it doesn't deadlock.

    Does NOT rely on mocks — both validator types are simple module-level
    dataclasses that pickle/unpickle cleanly under ``spawn``.
    """

    def test_parallel_aggregates_findings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERIFIERS_PARALLEL", "1")
        ctx = _StubCtx(project_root=tmp_path)
        validators = [_PassValidator(id=f"V{i:02d}-pass") for i in (1, 2, 3, 4)]
        findings = run_all(validators, ctx, mode="stop", max_workers=2, per_validator_timeout=5)
        # Each pass validator produces one V99-OK finding; we expect 4 total.
        assert len(findings) == 4
