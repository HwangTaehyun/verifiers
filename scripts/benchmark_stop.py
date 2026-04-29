#!/usr/bin/env python3
"""Benchmark sequential vs parallel Tier 3 (Stop hook) validator execution.

The phase12 ``lib/parallel_runner.py`` farms each Stop-mode validator
into its own ``ProcessPoolExecutor`` worker. The README claims this
prevents one heavy validator from starving the rest under the 120s
hook budget. This script measures the actual speedup on a synthetic
workload that mirrors the real one (15 fast validators + 4 heavies
matching V06 / V07 / V19 / V14's external-tool costs).

Usage::

    uv run python scripts/benchmark_stop.py                 # default workload
    uv run python scripts/benchmark_stop.py --workers 8     # different pool size
    uv run python scripts/benchmark_stop.py --json          # machine-readable

Output goes to stdout; nothing is written to disk. Re-run before
landing parallel-runner changes to confirm the speedup didn't regress.
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
from dataclasses import dataclass, field
from pathlib import Path

# Make the repo's lib/ + hooks/ importable when run via uv.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks.validators.base import ValidationResult
from lib.config_loader import VerifiersConfig
from lib.parallel_runner import run_all


@dataclass
class _SlowValidator:
    """Test double — sleeps the configured time then returns no findings.

    Mirrors a real validator's interface enough that ``parallel_runner``
    can submit / pickle / await it.
    """

    id: str
    sleep_seconds: float

    def run(self, ctx, file_path=None, mode="stop") -> ValidationResult:  # noqa: ARG002
        time.sleep(self.sleep_seconds)
        return ValidationResult(validator_id=self.id, findings=[])


@dataclass
class _BenchmarkCtx:
    """Minimal stand-in for ``ProjectContext`` — picklable for spawn workers."""

    project_root: Path = field(default_factory=lambda: Path("/tmp"))
    config: VerifiersConfig = field(default_factory=VerifiersConfig)


def _default_workload() -> list[_SlowValidator]:
    """Synthetic mix matching the real Tier 3 load (19 validators)."""
    return [
        # Fifteen lightweight validators: regex-only, file-globbing
        # work that finishes quickly. Models V01/V02/V03/V04/V05/V08/
        # V09/V10/V11/V12/V13/V14/V15/V16/V18.
        *[_SlowValidator(id=f"V{i:02d}-light", sleep_seconds=0.05) for i in range(15)],
        # Four heavyweights that drive external tools and dominate
        # serial wall-clock time in real projects.
        _SlowValidator(id="V06-go-quality", sleep_seconds=2.0),  # golangci-lint
        _SlowValidator(id="V07-ts-quality", sleep_seconds=1.5),  # tsc + madge + knip
        _SlowValidator(id="V19-py-quality", sleep_seconds=1.0),  # ruff + pytest
        _SlowValidator(id="V14-complexity", sleep_seconds=0.3),  # AST scan
    ]


def _measure(workload: list[_SlowValidator], max_workers: int, parallel: bool) -> float:
    """Run ``workload`` once under the given mode; return wall-clock seconds."""
    os.environ["VERIFIERS_PARALLEL"] = "1" if parallel else "0"
    ctx = _BenchmarkCtx()
    start = time.monotonic()
    run_all(workload, ctx, mode="stop", max_workers=max_workers, per_validator_timeout=60)
    return time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4, help="parallel pool size (default: 4)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a single JSON object suitable for CI consumption",
    )
    args = parser.parse_args()

    workload = _default_workload()
    sequential_s = _measure(workload, max_workers=args.workers, parallel=False)
    parallel_s = _measure(workload, max_workers=args.workers, parallel=True)
    speedup = sequential_s / parallel_s if parallel_s else 0.0

    if args.json:
        print(
            _json.dumps(
                {
                    "validators": len(workload),
                    "workers": args.workers,
                    "sequential_seconds": round(sequential_s, 3),
                    "parallel_seconds": round(parallel_s, 3),
                    "speedup": round(speedup, 2),
                }
            )
        )
        return 0

    sum_sleep = sum(v.sleep_seconds for v in workload)
    max_sleep = max(v.sleep_seconds for v in workload)
    print(f"Validators: {len(workload)}  workers: {args.workers}")
    print(f"  Σ sleep:   {sum_sleep:.2f}s  (sequential lower bound)")
    print(f"  max sleep: {max_sleep:.2f}s  (parallel lower bound, infinite workers)")
    print(f"Sequential: {sequential_s:6.2f}s")
    print(f"Parallel:   {parallel_s:6.2f}s")
    print(f"Speedup:    {speedup:6.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
