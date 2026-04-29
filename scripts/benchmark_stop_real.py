#!/usr/bin/env python3
"""Real-world Tier 3 wall-clock measurement against the verifiers repo (Phase 24, A3).

The companion ``scripts/benchmark_stop.py`` measures sequential vs
parallel execution against a *synthetic* workload (sleep-only validators).
This script complements it by running the **real** registered validators
(V01–V20) against the verifiers repo itself — V14 walks the AST, V19 invokes
ruff + pytest, V12 shells out to ``git status``, and so on.

Two reasons to keep both benchmarks:

* The synthetic one isolates parallel-pool overhead from validator cost.
  Pure speedup measurement.
* The real one tells a user "on a ~70-file Python repo, the actual Stop
  hook takes <X> seconds" — what they'll feel when they install verifiers
  on their own project.

Usage::

    uv run python scripts/benchmark_stop_real.py              # human readable
    uv run python scripts/benchmark_stop_real.py --json       # CI / monitoring
    uv run python scripts/benchmark_stop_real.py --skip V07   # exclude noisy validators

The script writes nothing to disk; output goes to stdout. It uses the
``filter_disabled_validators`` helper so ``--skip`` matches the same
allowlist semantics as ``.verifiers/config.yaml``.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import argparse
import json as _json
import os
import sys
import time
from pathlib import Path

# Make the repo's lib/ + hooks/ importable when run via uv.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from hooks.validators import get_all_validators  # noqa: E402
from lib.exclusion import filter_disabled_validators  # noqa: E402
from lib.parallel_runner import run_all  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402


def _measure(validators, ctx, *, parallel: bool, max_workers: int, per_timeout: int) -> tuple[float, int]:
    """Run ``validators`` once; return (wall_clock_seconds, finding_count)."""
    os.environ["VERIFIERS_PARALLEL"] = "1" if parallel else "0"
    start = time.monotonic()
    findings = run_all(
        validators,
        ctx,
        mode="stop",
        max_workers=max_workers,
        per_validator_timeout=per_timeout,
    )
    return time.monotonic() - start, len(findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel pool size (default: 4 = production default).",
    )
    parser.add_argument(
        "--per-timeout",
        type=int,
        default=60,
        help="Per-validator timeout in seconds (default: 60 — generous for first-run).",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="V-ID",
        help="V-ID prefix or full id to skip. Repeat to skip multiple. "
        "Useful to exclude noisy validators on first run (e.g. --skip V07).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a single JSON object to stdout (for CI / monitoring).",
    )
    args = parser.parse_args()

    ctx = ProjectContext(_REPO_ROOT)
    validators = filter_disabled_validators(get_all_validators(), args.skip)

    seq_s, seq_findings = _measure(
        validators,
        ctx,
        parallel=False,
        max_workers=args.workers,
        per_timeout=args.per_timeout,
    )
    par_s, par_findings = _measure(
        validators,
        ctx,
        parallel=True,
        max_workers=args.workers,
        per_timeout=args.per_timeout,
    )
    speedup = seq_s / par_s if par_s else 0.0

    if args.json:
        print(
            _json.dumps(
                {
                    "repo": str(_REPO_ROOT),
                    "validators_run": [v.id for v in validators],
                    "validators_skipped": args.skip,
                    "workers": args.workers,
                    "per_validator_timeout": args.per_timeout,
                    "sequential_seconds": round(seq_s, 3),
                    "sequential_findings": seq_findings,
                    "parallel_seconds": round(par_s, 3),
                    "parallel_findings": par_findings,
                    "speedup": round(speedup, 2),
                }
            )
        )
        return 0

    print(f"Repo:      {_REPO_ROOT}")
    print(f"Validators run:     {len(validators)} ({', '.join(v.id.split('-', 1)[0] for v in validators)})")
    if args.skip:
        print(f"Validators skipped: {', '.join(args.skip)}")
    print(f"Workers:   {args.workers}  per-validator timeout: {args.per_timeout}s")
    print()
    print(f"Sequential: {seq_s:6.2f}s  ({seq_findings} findings)")
    print(f"Parallel:   {par_s:6.2f}s  ({par_findings} findings)")
    print(f"Speedup:    {speedup:6.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
