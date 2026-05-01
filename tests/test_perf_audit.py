"""Tests for scripts/perf_audit.py — Phase64.5 perf recommendations."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Make scripts/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.metrics import ValidatorMetric
from scripts.perf_audit import (
    QUIET_USE_THRESHOLD,
    SLOW_THRESHOLD_MS,
    VERY_SLOW_THRESHOLD_MS,
    _is_cacheable,
    build_recommendations,
    build_report,
)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 1, tzinfo=timezone.utc)


def _metric(
    vid: str,
    *,
    use_count: int = 100,
    findings_emitted: int = 0,
    mean_ms: float = 50.0,
    last_used_days_ago: int = 1,
    last_finding_days_ago: int | None = None,
    now: datetime,
) -> ValidatorMetric:
    """Build a synthetic ValidatorMetric for testing recommendation logic."""
    last_used = now - timedelta(days=last_used_days_ago)
    last_finding = now - timedelta(days=last_finding_days_ago) if last_finding_days_ago is not None else None
    return ValidatorMetric(
        validator_id=vid,
        first_seen=last_used - timedelta(days=30),
        last_used=last_used,
        last_finding_at=last_finding,
        use_count=use_count,
        findings_emitted=findings_emitted,
        error_findings=findings_emitted,
        warning_findings=0,
        total_duration_ms=int(use_count * mean_ms),
    )


# ── _is_cacheable ────────────────────────────────────────────────────────


def test_is_cacheable_safe_validators() -> None:
    assert _is_cacheable("V07-ts-quality") is True
    assert _is_cacheable("V14-complexity") is True


def test_is_cacheable_test_runners_excluded() -> None:
    for vid in ("V06-go", "V09-go-test", "V10-ts-test", "V11-py-test", "V12-commit", "V21-pytest", "V37-race"):
        assert _is_cacheable(vid) is False


# ── build_recommendations ────────────────────────────────────────────────


def test_no_recommendations_for_fast_active_validators(now: datetime) -> None:
    """Fast (50ms) + active (recent findings) → no recommendations."""
    metrics = [_metric("V07-ts", use_count=200, findings_emitted=20, mean_ms=50, last_finding_days_ago=1, now=now)]
    recs = build_recommendations(metrics, now)
    assert recs == []


def test_disable_recommendation_for_slow_quiet_validator(now: datetime) -> None:
    """Slow (1.5s) + quiet (uses=100, finds=0) → disable candidate."""
    metrics = [
        _metric(
            "V20-hasura",
            use_count=100,
            findings_emitted=0,
            mean_ms=1500,
            last_used_days_ago=1,
            last_finding_days_ago=None,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    assert len(recs) == 1
    assert recs[0].category == "disable"
    assert recs[0].severity == "strong"
    assert "V20" in recs[0].suggested_yaml
    assert "validators:\n  disabled:" in recs[0].suggested_yaml


def test_no_disable_recommendation_below_use_threshold(now: datetime) -> None:
    """Quiet but only 10 invocations → not enough sample, no disable."""
    metrics = [
        _metric(
            "V20-hasura",
            use_count=10,
            findings_emitted=0,
            mean_ms=1500,
            last_finding_days_ago=None,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    # No disable rec — sample size too small.
    assert not any(r.category == "disable" for r in recs)


def test_cache_ttl_recommendation_for_very_slow_cacheable(now: datetime) -> None:
    """Very slow (6s) + cacheable + active → tier_cache TTL bump."""
    metrics = [
        _metric(
            "V07-ts-quality",
            use_count=200,
            findings_emitted=10,
            mean_ms=6000,
            last_finding_days_ago=1,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    cache_recs = [r for r in recs if r.category == "cache_ttl"]
    assert len(cache_recs) == 1
    assert "tier_cache:" in cache_recs[0].suggested_yaml
    assert "max_age_seconds: 1800" in cache_recs[0].suggested_yaml


def test_no_cache_ttl_for_uncacheable_validator(now: datetime) -> None:
    """Very slow but uncacheable (V21 pytest) → no tier_cache rec."""
    metrics = [
        _metric(
            "V21-pytest",
            use_count=200,
            findings_emitted=5,
            mean_ms=15000,  # 15s pytest
            last_finding_days_ago=1,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    assert not any(r.category == "cache_ttl" for r in recs)


def test_timeout_recommendation_for_slow_validator(now: datetime) -> None:
    """Mean ≥50% of default 30s timeout → timeout bump."""
    metrics = [
        _metric(
            "V21-pytest",
            use_count=200,
            findings_emitted=5,
            mean_ms=18000,  # 18s — past 50% of 30s
            last_finding_days_ago=1,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    timeout_recs = [r for r in recs if r.category == "timeout"]
    assert len(timeout_recs) == 1
    assert "timeouts:" in timeout_recs[0].suggested_yaml
    assert "V21:" in timeout_recs[0].suggested_yaml


def test_timeout_recommendation_floor_60s(now: datetime) -> None:
    """Even a tiny mean above the threshold gets at least a 60s suggestion."""
    metrics = [
        _metric(
            "V19-py-quality",
            use_count=200,
            findings_emitted=5,
            mean_ms=15000,  # 15s — exactly 50%
            last_finding_days_ago=1,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    timeout_recs = [r for r in recs if r.category == "timeout"]
    assert any("60" in r.suggested_yaml or "120" in r.suggested_yaml for r in timeout_recs)


def test_review_recommendation_for_quiet_uncacheable(now: datetime) -> None:
    """V09 (test runner, uncacheable) firing 100 times w/ no findings → review."""
    metrics = [
        _metric(
            "V09-go-test",
            use_count=100,
            findings_emitted=0,
            mean_ms=200,
            last_finding_days_ago=None,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    review_recs = [r for r in recs if r.category == "review"]
    assert len(review_recs) == 1
    assert review_recs[0].severity == "info"
    assert review_recs[0].suggested_yaml == ""  # no actionable yaml


def test_disable_takes_priority_over_timeout(now: datetime) -> None:
    """A slow + quiet validator gets disable, not also timeout — `continue`."""
    metrics = [
        _metric(
            "V20-hasura",
            use_count=200,
            findings_emitted=0,
            mean_ms=18000,  # very slow
            last_used_days_ago=1,
            last_finding_days_ago=None,
            now=now,
        )
    ]
    recs = build_recommendations(metrics, now)
    categories = [r.category for r in recs]
    assert "disable" in categories
    assert "timeout" not in categories  # blocked by `continue`


def test_thresholds_are_consistent() -> None:
    assert SLOW_THRESHOLD_MS == 1000
    assert VERY_SLOW_THRESHOLD_MS == 5000
    assert QUIET_USE_THRESHOLD == 50


# ── build_report ─────────────────────────────────────────────────────────


def test_build_report_excludes_zero_use_validators(now: datetime) -> None:
    """Validators with use_count=0 don't appear in slowest list (no signal)."""
    metrics = [
        _metric("V01-env", use_count=0, mean_ms=0, now=now),
        _metric("V07-ts", use_count=100, findings_emitted=10, mean_ms=200, last_finding_days_ago=1, now=now),
    ]
    report = build_report(metrics, now, days=30)
    ids = [s["validator_id"] for s in report.slowest]
    assert "V01-env" not in ids
    assert "V07-ts" in ids


def test_build_report_marks_cacheability(now: datetime) -> None:
    metrics = [
        _metric("V07-ts", use_count=10, mean_ms=100, last_finding_days_ago=1, now=now),
        _metric("V21-pytest", use_count=10, mean_ms=100, last_finding_days_ago=1, now=now),
    ]
    report = build_report(metrics, now, days=30)
    by_id = {s["validator_id"]: s for s in report.slowest}
    assert by_id["V07-ts"]["cacheable"] == "yes"
    assert by_id["V21-pytest"]["cacheable"] == "no"


def test_build_report_sorted_by_mean_desc(now: datetime) -> None:
    metrics = [
        _metric("V01-fast", use_count=100, mean_ms=50, last_finding_days_ago=1, now=now),
        _metric("V02-mid", use_count=100, mean_ms=500, last_finding_days_ago=1, now=now),
        _metric("V03-slow", use_count=100, mean_ms=5000, last_finding_days_ago=1, now=now),
    ]
    report = build_report(metrics, now, days=30)
    ids = [s["validator_id"] for s in report.slowest]
    assert ids == ["V03-slow", "V02-mid", "V01-fast"]
