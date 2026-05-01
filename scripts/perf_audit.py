#!/usr/bin/env python3
"""Performance audit CLI — turn metric history into actionable config.

Usage:
    uv run --script scripts/perf_audit.py
    uv run --script scripts/perf_audit.py --days 90
    uv run --script scripts/perf_audit.py --json
    uv run --script scripts/perf_audit.py --log-dir /path/to/metrics

Companion to ``validator_metrics.py``. Where that script reports raw
state (active / quiet / dormant), this one reads the same JSONL stream
and emits **recommendations** the user can paste straight into
``.verifiers/config.yaml``:

  - **Slow validators** → ``timeouts.per_validator[V##]`` bump so a
    legitimately slow check (pytest, golangci-lint) doesn't get killed
    by the default 30s budget.
  - **Slow + quiet** → ``validators.disabled[V##]`` candidate. A
    validator that's both expensive and finds nothing in 30+ days is
    paying its full cost for no benefit. Hermes Curator's pruning
    discipline applied at the validator layer.
  - **Slow + cacheable** → ``tier_cache.max_age_seconds`` bump (or a
    note that the cache is already protecting the user). Phase 63's
    default 5-min TTL is conservative; long-running validators on
    rarely-changing inputs benefit from longer windows.
  - **Quiet but uncacheable** → flag for review (V09/V10/V11/V21/V37
    are the test runners; if they fire and never find anything, the
    test suite either needs more aggressive failure injection in CI or
    the validator's tracker thresholds want tuning).

Phase 64.5 of the optimization sweep — observability that closes the
loop on Phase 61–63 caching work. Without it, knowing whether the
cache landed correctly required eyeballing per-Stop wall-clock numbers.

Read-only — never writes config. The user copy-pastes recommended
diffs at their own discretion.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent so ``lib.*`` resolves when executed directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.metrics import aggregate_metrics  # noqa: E402
from lib.tier_cache import TIER_CACHE_INELIGIBLE  # noqa: E402


# Slow / cost thresholds (milliseconds, p50/mean).
SLOW_THRESHOLD_MS = 1000  # ≥ 1s mean → "slow"
VERY_SLOW_THRESHOLD_MS = 5000  # ≥ 5s mean → "consider longer cache TTL"

# Default Phase 62 timeout (must stay in sync with parallel_runner.DEFAULT_PER_VALIDATOR_TIMEOUT).
DEFAULT_TIMEOUT_S = 30

# Default Phase 63 TTL (must stay in sync with TierCacheConfig.max_age_seconds).
DEFAULT_TIER_CACHE_TTL_S = 300

# A validator that's been quiet (uses > N, finds == 0) AND slow is a
# strong candidate to disable. We're conservative on the use threshold
# so we don't recommend disabling new validators.
QUIET_USE_THRESHOLD = 50


@dataclass
class Recommendation:
    """A single config-actionable suggestion."""

    validator_id: str
    severity: str  # "info" | "suggestion" | "strong"
    category: str  # "timeout" | "cache_ttl" | "disable" | "review"
    rationale: str
    suggested_yaml: str = ""

    def to_dict(self) -> dict:
        return {
            "validator_id": self.validator_id,
            "severity": self.severity,
            "category": self.category,
            "rationale": self.rationale,
            "suggested_yaml": self.suggested_yaml,
        }


@dataclass
class PerfReport:
    days: int
    generated_at: str
    slowest: list[dict] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)


def _vid_prefix(validator_id: str) -> str:
    """Extract V-NN prefix from a validator id."""
    return validator_id.split("-", 1)[0]


def _is_cacheable(validator_id: str) -> bool:
    """True if Phase 63 PASS-state cache covers this validator."""
    return _vid_prefix(validator_id) not in TIER_CACHE_INELIGIBLE


def _suggest_timeout_yaml(prefix: str, suggested_seconds: int) -> str:
    """Render a copy-pasteable timeouts.per_validator entry."""
    return f"timeouts:\n  per_validator:\n    {prefix}: {suggested_seconds}"


def _suggest_disable_yaml(prefix: str) -> str:
    return f"validators:\n  disabled:\n    - {prefix}"


def _suggest_tier_cache_yaml(seconds: int) -> str:
    return f"tier_cache:\n  max_age_seconds: {seconds}"


def build_recommendations(metrics, now: datetime) -> list[Recommendation]:
    """Translate aggregated metrics into a list of recommendations.

    Order: most actionable first (disable candidates → cache TTL bumps
    → timeout bumps → review notes).
    """
    out: list[Recommendation] = []

    for m in metrics:
        prefix = _vid_prefix(m.validator_id)
        state = m.state(now)
        slow = m.mean_duration_ms >= SLOW_THRESHOLD_MS
        very_slow = m.mean_duration_ms >= VERY_SLOW_THRESHOLD_MS

        # 1. Quiet + slow + sufficient sample size → disable candidate.
        if state == "quiet" and slow and m.use_count >= QUIET_USE_THRESHOLD:
            out.append(
                Recommendation(
                    validator_id=m.validator_id,
                    severity="strong",
                    category="disable",
                    rationale=(
                        f"{m.use_count} invocations over the window, mean {m.mean_duration_ms:.0f} ms, "
                        f"zero findings. Each Stop hook is paying ~{m.mean_duration_ms / 1000:.1f}s "
                        f"for a validator that has emitted no signal in {m.use_count} runs. "
                        "If this stays empty in your project, disable it."
                    ),
                    suggested_yaml=_suggest_disable_yaml(prefix),
                )
            )
            continue  # don't double-recommend timeout/cache bumps for a disable candidate

        # 2. Very slow + cacheable → bump tier_cache TTL recommendation.
        if very_slow and _is_cacheable(m.validator_id):
            # Aim for 30 minutes of cache horizon — long enough that
            # repeated Stop hooks within a focused work session benefit,
            # short enough to catch system-state drift.
            suggested_ttl = 1800
            out.append(
                Recommendation(
                    validator_id=m.validator_id,
                    severity="suggestion",
                    category="cache_ttl",
                    rationale=(
                        f"Mean {m.mean_duration_ms / 1000:.1f}s — Phase 63 PASS-state cache will save "
                        f"the most time on this validator. Default TTL {DEFAULT_TIER_CACHE_TTL_S}s "
                        f"({DEFAULT_TIER_CACHE_TTL_S // 60}min) is conservative; bumping to "
                        f"{suggested_ttl}s ({suggested_ttl // 60}min) extends the win across "
                        "back-to-back Stop hooks in a focused session."
                    ),
                    suggested_yaml=_suggest_tier_cache_yaml(suggested_ttl),
                )
            )

        # 3. Approaching timeout → bump per-validator budget.
        if m.mean_duration_ms >= DEFAULT_TIMEOUT_S * 1000 * 0.5:
            # If mean is >= 50% of the default 30s timeout, p99 will hit
            # the timeout often enough to surface V##-TIMEOUT sentinels.
            suggested_seconds = max(60, int(m.mean_duration_ms / 1000 * 3))  # 3x mean, min 60s
            out.append(
                Recommendation(
                    validator_id=m.validator_id,
                    severity="suggestion",
                    category="timeout",
                    rationale=(
                        f"Mean {m.mean_duration_ms / 1000:.1f}s vs default {DEFAULT_TIMEOUT_S}s budget. "
                        "p99 will trip V##-TIMEOUT under load. "
                        f"Recommended per-validator timeout: {suggested_seconds}s (3× mean, floor 60s)."
                    ),
                    suggested_yaml=_suggest_timeout_yaml(prefix, suggested_seconds),
                )
            )

        # 4. Quiet but uncacheable (test runners / git-state-aware) — review note.
        if state == "quiet" and not _is_cacheable(m.validator_id) and m.use_count >= QUIET_USE_THRESHOLD:
            out.append(
                Recommendation(
                    validator_id=m.validator_id,
                    severity="info",
                    category="review",
                    rationale=(
                        f"{m.use_count} invocations, zero findings, but in TIER_CACHE_INELIGIBLE "
                        "(test runner or git-state aware). Either the test suite has no failures "
                        "and the runner is doing its job (good — keep), or its failure-tracker "
                        "thresholds need tuning. No config change needed; flagged for awareness."
                    ),
                )
            )

    return out


def _format_table(report: PerfReport) -> str:
    lines: list[str] = []
    lines.append(f"Performance audit — last {report.days} days (generated {report.generated_at})")
    lines.append("")

    if report.slowest:
        lines.append("Top slowest validators (by mean ms):")
        header = f"  {'V-ID':<26} {'mean(ms)':>10} {'uses':>7} {'findings':>9} {'cacheable':>10}"
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for row in report.slowest[:10]:
            lines.append(
                f"  {row['validator_id']:<26} {row['mean_ms']:>10.1f} {row['uses']:>7} "
                f"{row['findings']:>9} {row['cacheable']:>10}"
            )
        lines.append("")

    if not report.recommendations:
        lines.append("✅ No tuning recommendations — your validators are fast / effective / well-cached.")
        return "\n".join(lines)

    by_category: dict[str, list[Recommendation]] = {}
    for rec in report.recommendations:
        by_category.setdefault(rec.category, []).append(rec)

    severity_marker = {"strong": "🔴", "suggestion": "🟡", "info": "🔵"}

    for category, recs in by_category.items():
        title = {
            "disable": "Disable candidates (slow + zero findings)",
            "cache_ttl": "tier_cache TTL bumps (very slow + cacheable)",
            "timeout": "Per-validator timeout bumps",
            "review": "Review (quiet + uncacheable)",
        }.get(category, category)
        lines.append(f"## {title}")
        lines.append("")
        for rec in recs:
            marker = severity_marker.get(rec.severity, "•")
            lines.append(f"{marker} {rec.validator_id} — {rec.rationale}")
            if rec.suggested_yaml:
                lines.append("")
                for yml_line in rec.suggested_yaml.split("\n"):
                    lines.append(f"   {yml_line}")
            lines.append("")
    return "\n".join(lines)


def build_report(metrics, now: datetime, days: int) -> PerfReport:
    sorted_by_speed = sorted(metrics, key=lambda m: m.mean_duration_ms, reverse=True)
    slowest = [
        {
            "validator_id": m.validator_id,
            "mean_ms": round(m.mean_duration_ms, 2),
            "uses": m.use_count,
            "findings": m.findings_emitted,
            "cacheable": "yes" if _is_cacheable(m.validator_id) else "no",
        }
        for m in sorted_by_speed
        if m.use_count > 0
    ]
    recommendations = build_recommendations(metrics, now)
    return PerfReport(
        days=days,
        generated_at=now.isoformat(),
        slowest=slowest,
        recommendations=recommendations,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validator perf audit — slow / quiet / cache-eligible analysis with config recommendations.",
    )
    parser.add_argument("--days", type=int, default=30, help="Window in days (default: 30).")
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Path to .verifiers/state/metrics/ (default: cwd's project metrics, then ./logs/).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    if args.log_dir:
        log_dir = Path(args.log_dir).expanduser().resolve()
    else:
        cwd_metrics = Path.cwd() / ".verifiers" / "state" / "metrics"
        legacy = Path(__file__).parent.parent / "logs"
        log_dir = cwd_metrics if cwd_metrics.exists() else legacy

    if not log_dir.exists():
        print(f"log directory not found: {log_dir}", file=sys.stderr)
        print(
            "  hint: per-project metrics live in <project>/.verifiers/state/metrics/. "
            "Run a verifier first or pass --log-dir explicitly.",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=args.days)
    metrics = aggregate_metrics(log_dir, since=since)
    report = build_report(metrics, now, args.days)

    if args.json:
        payload = {
            "days": report.days,
            "generated_at": report.generated_at,
            "slowest": report.slowest,
            "recommendations": [r.to_dict() for r in report.recommendations],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_format_table(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
