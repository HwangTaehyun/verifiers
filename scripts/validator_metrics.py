#!/usr/bin/env python3
"""Validator usage metrics CLI — show which validators are pulling their weight.

Usage:
    uv run --script scripts/validator_metrics.py
    uv run --script scripts/validator_metrics.py --days 90
    uv run --script scripts/validator_metrics.py --json
    uv run --script scripts/validator_metrics.py --log-dir /path/to/logs

Reads logs/V##-{name}.jsonl emitted by JsonLogger.run() and aggregates
per-validator stats over the requested window. Renders either a human
table (default) or a JSON document for downstream pipelines.

Lifecycle states:
  active   — invoked + emitting findings recently
  quiet    — invoked recently but emitting no findings (cost / value gap)
  dormant  — not invoked at all in the window (likely no matching files
             — e.g. Go validators in a Python-only project)

The "consider disabling" hint at the bottom flags quiet+dormant
validators outside the pinned set. Pinning is a Phase33b follow-up;
for now every validator is treated as unpinned.

Inspired by NousResearch/hermes-agent#7816 — long-lived self-improving
agents need usage records before they can prune skills, and validators
are subject to the same pressure (bigger windows than skills, but the
same effectiveness-vs-cost trade-off).
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add parent so ``lib.metrics`` resolves when executed directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.metrics import aggregate_metrics  # noqa: E402


def _format_table(metrics, now: datetime, days: int) -> str:
    lines: list[str] = []
    lines.append(f"Validator metrics — last {days} days")
    lines.append("")
    header = f"{'ID':<26} {'state':<8} {'uses':>6} {'finds':>6} {'errs':>5} {'warns':>5} {'mean(ms)':>10} {'effect':>7}"
    lines.append(header)
    lines.append("-" * len(header))

    for m in metrics:
        state = m.state(now)
        lines.append(
            f"{m.validator_id:<26} {state:<8} {m.use_count:>6} {m.findings_emitted:>6} "
            f"{m.error_findings:>5} {m.warning_findings:>5} "
            f"{m.mean_duration_ms:>10.1f} {m.effectiveness:>7.2f}"
        )

    if not metrics:
        lines.append("(no validator activity in the window)")
        return "\n".join(lines)

    lines.append("")
    dormant = [m.validator_id for m in metrics if m.state(now) == "dormant"]
    quiet = [m.validator_id for m in metrics if m.state(now) == "quiet"]
    if dormant:
        lines.append(f"Dormant ({len(dormant)}): {', '.join(dormant)}")
    if quiet:
        lines.append(f"Quiet   ({len(quiet)}): {', '.join(quiet)}")
    if dormant or quiet:
        lines.append(
            "  → quiet validators fired but emitted no findings — review for false-positive rules or perf/value gaps."
        )
        lines.append("  → dormant validators never fired — likely benign (file_patterns didn't match in this project).")

    return "\n".join(lines)


def _format_json(metrics, now: datetime) -> str:
    payload = []
    for m in metrics:
        payload.append(
            {
                "validator_id": m.validator_id,
                "state": m.state(now),
                "use_count": m.use_count,
                "findings_emitted": m.findings_emitted,
                "error_findings": m.error_findings,
                "warning_findings": m.warning_findings,
                "total_duration_ms": m.total_duration_ms,
                "mean_duration_ms": round(m.mean_duration_ms, 2),
                "effectiveness": round(m.effectiveness, 4),
                "first_seen": m.first_seen.isoformat() if m.first_seen else None,
                "last_used": m.last_used.isoformat() if m.last_used else None,
                "last_finding_at": m.last_finding_at.isoformat() if m.last_finding_at else None,
            }
        )
    return json.dumps(payload, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate validator usage from logs/V##.jsonl",
    )
    parser.add_argument("--days", type=int, default=30, help="Window in days (default: 30).")
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Path to logs/ (default: ./logs/ relative to verifiers root).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    if args.log_dir:
        log_dir = Path(args.log_dir).expanduser().resolve()
    else:
        # Phase33b: per-project metrics live under ctx.metrics_log_dir.
        # Default to the cwd's .verifiers/state/metrics so the CLI
        # always reports the project the user is sitting in. Fall back
        # to the legacy verifiers-source ``logs/`` for back-compat
        # when the new path doesn't exist yet.
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

    if args.json:
        print(_format_json(metrics, now))
    else:
        print(_format_table(metrics, now, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
