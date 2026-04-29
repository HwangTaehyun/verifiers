"""Tests for lib/metrics.py + per-project metrics path (Phase33 / Phase33b).

Covers:
  - aggregate_metrics: empty dir, single validator, multi-entry sums,
    since-filter, _errors.jsonl exclusion, malformed-line resilience
  - ValidatorMetric.state: dormant / quiet / active classification
  - ValidatorMetric.effectiveness, mean_duration_ms derived properties
  - Phase33b: ProjectContext.metrics_log_dir + JsonLogger rotation +
    BaseValidator.run() writing into the per-project metrics directory
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.metrics import ValidatorMetric, aggregate_metrics


# ── Helpers ─────────────────────────────────────────────────────────────


def _write_log(log_dir: Path, filename: str, entries: list[dict]) -> None:
    """Write JSONL lines into log_dir/<filename>.jsonl."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / filename
    with path.open("w") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


def _iso(offset_days: int = 0) -> str:
    """ISO timestamp ``offset_days`` ago in UTC."""
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def _entry(
    validator: str,
    *,
    offset_days: int = 0,
    duration_ms: int = 10,
    findings_count: int = 0,
    error_count: int = 0,
    warning_count: int = 0,
    mode: str = "post_tool_use",
) -> dict:
    return {
        "timestamp": _iso(offset_days),
        "validator": validator,
        "mode": mode,
        "duration_ms": duration_ms,
        "findings_count": findings_count,
        "error_count": error_count,
        "warning_count": warning_count,
    }


# ── 1. aggregate_metrics — happy path ──────────────────────────────────


class TestAggregateMetrics:
    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        result = aggregate_metrics(tmp_path)
        assert result == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        # aggregate_metrics on a non-existent directory must not raise.
        result = aggregate_metrics(tmp_path / "nope")
        assert result == []

    def test_single_validator_single_entry(self, tmp_path: Path) -> None:
        _write_log(
            tmp_path,
            "V08-security.jsonl",
            [_entry("V08-security", duration_ms=50, findings_count=1, error_count=1)],
        )
        result = aggregate_metrics(tmp_path)
        assert len(result) == 1
        m = result[0]
        assert m.validator_id == "V08-security"
        assert m.use_count == 1
        assert m.findings_emitted == 1
        assert m.error_findings == 1
        assert m.warning_findings == 0
        assert m.total_duration_ms == 50

    def test_multiple_entries_aggregate(self, tmp_path: Path) -> None:
        _write_log(
            tmp_path,
            "V14-complexity.jsonl",
            [
                _entry("V14-complexity", duration_ms=100, findings_count=2, error_count=1, warning_count=1),
                _entry("V14-complexity", duration_ms=150, findings_count=0),
                _entry("V14-complexity", duration_ms=200, findings_count=3, warning_count=3),
            ],
        )
        result = aggregate_metrics(tmp_path)
        assert len(result) == 1
        m = result[0]
        assert m.use_count == 3
        assert m.findings_emitted == 5
        assert m.error_findings == 1
        assert m.warning_findings == 4
        assert m.total_duration_ms == 450
        assert m.mean_duration_ms == 150.0

    def test_multiple_validators_separate(self, tmp_path: Path) -> None:
        _write_log(tmp_path, "V08-security.jsonl", [_entry("V08-security", findings_count=1)])
        _write_log(tmp_path, "V14-complexity.jsonl", [_entry("V14-complexity", findings_count=2)])
        result = aggregate_metrics(tmp_path)
        assert len(result) == 2
        ids = [m.validator_id for m in result]
        # Sorted by id alphabetically.
        assert ids == ["V08-security", "V14-complexity"]


# ── 2. since-filter — restrict to recent window ────────────────────────


class TestSinceFilter:
    def test_excludes_old_entries(self, tmp_path: Path) -> None:
        _write_log(
            tmp_path,
            "V01-env.jsonl",
            [
                _entry("V01-env", offset_days=60, duration_ms=10),  # outside window
                _entry("V01-env", offset_days=5, duration_ms=20, findings_count=1, warning_count=1),  # in
            ],
        )
        since = datetime.now(timezone.utc) - timedelta(days=30)
        result = aggregate_metrics(tmp_path, since=since)
        assert len(result) == 1
        m = result[0]
        assert m.use_count == 1
        assert m.total_duration_ms == 20
        assert m.findings_emitted == 1

    def test_no_since_includes_all(self, tmp_path: Path) -> None:
        _write_log(
            tmp_path,
            "V01-env.jsonl",
            [
                _entry("V01-env", offset_days=60, duration_ms=10),
                _entry("V01-env", offset_days=5, duration_ms=20),
            ],
        )
        result = aggregate_metrics(tmp_path)
        assert len(result) == 1
        assert result[0].use_count == 2

    def test_validator_with_no_recent_entries_dropped(self, tmp_path: Path) -> None:
        # If every entry is outside the since window, the validator
        # should not appear in the result at all.
        _write_log(tmp_path, "V01-env.jsonl", [_entry("V01-env", offset_days=60)])
        since = datetime.now(timezone.utc) - timedelta(days=30)
        assert aggregate_metrics(tmp_path, since=since) == []


# ── 3. resilience — bad input never crashes ────────────────────────────


class TestResilience:
    def test_excludes_errors_jsonl(self, tmp_path: Path) -> None:
        # logs/_errors.jsonl exists in production but its schema is
        # different (source/error/context, not validator/timestamp).
        # The V*.jsonl glob must not pick it up.
        (tmp_path / "_errors.jsonl").write_text(
            json.dumps({"timestamp": _iso(0), "source": "router", "error": "ImportError"}) + "\n"
        )
        assert aggregate_metrics(tmp_path) == []

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "V08-security.jsonl"
        path.write_text(
            "not json at all\n"
            + json.dumps(_entry("V08-security", findings_count=1, error_count=1))
            + "\n"
            + "{\n"  # truncated JSON
            + "{}\n"  # valid JSON but missing required fields
        )
        result = aggregate_metrics(tmp_path)
        assert len(result) == 1
        assert result[0].use_count == 1

    def test_missing_timestamp_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "V08-security.jsonl"
        # No timestamp → entry skipped (we can't bucket it by date).
        path.write_text(json.dumps({"validator": "V08-security", "duration_ms": 10}) + "\n")
        assert aggregate_metrics(tmp_path) == []

    def test_missing_validator_id_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "V08-security.jsonl"
        path.write_text(json.dumps({"timestamp": _iso(0), "duration_ms": 10}) + "\n")
        assert aggregate_metrics(tmp_path) == []

    def test_non_int_duration_treated_as_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "V08-security.jsonl"
        path.write_text(
            json.dumps(
                {
                    "timestamp": _iso(0),
                    "validator": "V08-security",
                    "duration_ms": "not a number",
                    "findings_count": 1,
                    "error_count": 0,
                    "warning_count": 0,
                }
            )
            + "\n"
        )
        result = aggregate_metrics(tmp_path)
        assert len(result) == 1
        assert result[0].total_duration_ms == 0
        assert result[0].findings_emitted == 1


# ── 4. derived properties ──────────────────────────────────────────────


class TestEffectiveness:
    def test_zero_use_returns_zero(self) -> None:
        m = ValidatorMetric(validator_id="V99")
        assert m.effectiveness == 0.0
        assert m.mean_duration_ms == 0.0

    def test_perfect_effectiveness(self) -> None:
        m = ValidatorMetric(validator_id="V99", use_count=10, findings_emitted=10)
        assert m.effectiveness == 1.0

    def test_partial_effectiveness(self) -> None:
        m = ValidatorMetric(validator_id="V99", use_count=4, findings_emitted=1)
        assert m.effectiveness == 0.25

    def test_super_effective_can_exceed_one(self) -> None:
        # A validator can emit multiple findings per invocation, so the
        # ratio is not capped at 1.0. This is intentional — a high ratio
        # (e.g. 10 findings per call) is a strong signal of value.
        m = ValidatorMetric(validator_id="V14", use_count=2, findings_emitted=20)
        assert m.effectiveness == 10.0

    def test_mean_duration(self) -> None:
        m = ValidatorMetric(validator_id="V19", use_count=4, total_duration_ms=2000)
        assert m.mean_duration_ms == 500.0


# ── 5. lifecycle state classification ──────────────────────────────────


class TestState:
    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def test_dormant_when_never_used(self) -> None:
        m = ValidatorMetric(validator_id="V03")
        assert m.state(self._now()) == "dormant"

    def test_dormant_when_old(self) -> None:
        old = self._now() - timedelta(days=20)
        m = ValidatorMetric(validator_id="V03", use_count=1, last_used=old)
        assert m.state(self._now(), dormant_days=14) == "dormant"

    def test_quiet_when_used_no_findings(self) -> None:
        recent = self._now() - timedelta(days=2)
        m = ValidatorMetric(validator_id="V09", use_count=10, last_used=recent)
        assert m.state(self._now()) == "quiet"

    def test_quiet_when_findings_too_old(self) -> None:
        recent = self._now() - timedelta(days=2)
        long_ago = self._now() - timedelta(days=100)
        m = ValidatorMetric(
            validator_id="V09",
            use_count=10,
            last_used=recent,
            findings_emitted=1,
            last_finding_at=long_ago,
        )
        assert m.state(self._now(), quiet_days=30) == "quiet"

    def test_active_when_used_with_findings(self) -> None:
        recent = self._now() - timedelta(days=2)
        m = ValidatorMetric(
            validator_id="V08",
            use_count=50,
            last_used=recent,
            findings_emitted=12,
            last_finding_at=recent,
        )
        assert m.state(self._now()) == "active"


# ── 6. Phase33b — JsonLogger rotation ──────────────────────────────────


class TestJsonLoggerRotation:
    def test_rotates_when_over_cap(self, tmp_path: Path) -> None:
        from lib.json_logger import _maybe_rotate

        log_file = tmp_path / "V08-security.jsonl"
        log_file.write_bytes(b"x" * 1000)

        _maybe_rotate(log_file, max_bytes=500)

        assert not log_file.exists()
        backup = tmp_path / "V08-security.jsonl.1"
        assert backup.exists()
        assert backup.stat().st_size == 1000

    def test_does_not_rotate_under_cap(self, tmp_path: Path) -> None:
        from lib.json_logger import _maybe_rotate

        log_file = tmp_path / "V14.jsonl"
        log_file.write_bytes(b"y" * 100)

        _maybe_rotate(log_file, max_bytes=500)

        assert log_file.exists()
        assert log_file.stat().st_size == 100
        assert not (tmp_path / "V14.jsonl.1").exists()

    def test_rotate_overwrites_old_backup(self, tmp_path: Path) -> None:
        from lib.json_logger import _maybe_rotate

        log_file = tmp_path / "V19.jsonl"
        log_file.write_bytes(b"new" * 200)
        backup = tmp_path / "V19.jsonl.1"
        backup.write_bytes(b"old" * 200)

        _maybe_rotate(log_file, max_bytes=300)

        # The "new" file should now be the backup; the old backup is gone.
        assert backup.exists()
        assert backup.read_bytes() == b"new" * 200
        assert not log_file.exists()

    def test_rotation_failure_swallowed(self, tmp_path: Path, monkeypatch) -> None:
        # If rename raises, _maybe_rotate must not propagate.
        from lib.json_logger import _maybe_rotate

        log_file = tmp_path / "V08.jsonl"
        log_file.write_bytes(b"x" * 1000)

        def boom(self, target):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        monkeypatch.setattr(Path, "rename", boom)
        _maybe_rotate(log_file, max_bytes=500)  # must not raise


# ── 7. Phase33b — per-project metrics path via BaseValidator.run() ─────


class TestPerProjectMetricsPath:
    def test_run_writes_to_ctx_metrics_log_dir(self, tmp_path: Path) -> None:
        from hooks.validators.base import BaseValidator
        from lib.project_context import ProjectContext

        # Set up a fake project root with a .git so ProjectContext picks it up.
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)

        class _DummyValidator(BaseValidator):
            id = "V99-test-only"

        validator = _DummyValidator()
        validator.run(ctx, file_path=None, mode="stop")

        expected_dir = tmp_path / ".verifiers" / "state" / "metrics"
        expected_file = expected_dir / "V99-test-only.jsonl"

        assert expected_dir.is_dir()
        assert expected_file.is_file()
        # Body is one JSON line.
        line = expected_file.read_text().strip()
        record = json.loads(line)
        assert record["validator"] == "V99-test-only"
        assert record["mode"] == "stop"
        assert record["findings_count"] == 0

    def test_metrics_log_dir_property(self, tmp_path: Path) -> None:
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert ctx.metrics_log_dir == tmp_path / ".verifiers" / "state" / "metrics"

    def test_two_projects_have_separate_dirs(self, tmp_path: Path) -> None:
        from hooks.validators.base import BaseValidator
        from lib.project_context import ProjectContext

        # Two sibling projects, both running the same validator.
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        for p in (proj_a, proj_b):
            p.mkdir()
            (p / ".git").mkdir()

        class _DummyValidator(BaseValidator):
            id = "V99-test-only"

        v = _DummyValidator()
        v.run(ProjectContext(proj_a), file_path=None, mode="stop")
        v.run(ProjectContext(proj_b), file_path=None, mode="stop")

        a_log = proj_a / ".verifiers" / "state" / "metrics" / "V99-test-only.jsonl"
        b_log = proj_b / ".verifiers" / "state" / "metrics" / "V99-test-only.jsonl"
        assert a_log.is_file()
        assert b_log.is_file()
        # Cross-pollination check: proj_a's log dir does NOT contain
        # proj_b's writes.
        a_lines = a_log.read_text().strip().splitlines()
        b_lines = b_log.read_text().strip().splitlines()
        assert len(a_lines) == 1
        assert len(b_lines) == 1
