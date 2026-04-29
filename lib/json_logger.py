"""JSONL structured logging for validator results.

Logs each validation run as a single JSON line in two locations:

  - **Per-project metrics** (Phase33b+): the BaseValidator runner passes
    ``ctx.metrics_log_dir`` (= ``<project_root>/.verifiers/state/metrics/``)
    so each project owns its own ``V##.jsonl`` files. This avoids the
    pre-Phase33b problem where every project hooking into the same
    verifiers install wrote to a single shared ``logs/`` and the metrics
    CLI couldn't tell projects apart.

  - **Sentinel error log** (legacy): ``log_exception()`` still appends to
    the verifiers-source-tree ``logs/_errors.jsonl`` because it has no
    ``ctx`` available — the call sites are inside ``except Exception:``
    branches in router/stop_validator/parallel_runner where context may
    not be constructed. This is fine because ``_errors.jsonl`` is
    write-only diagnostics and isn't read by ``aggregate_metrics``.

Per-validator JSONL files are size-rotated: when a file exceeds
``MAX_LOG_BYTES`` (default 10 MB), it is renamed to ``<file>.1`` (single
backup, FIFO eviction) and a fresh file is started. Rotation failure is
non-fatal — logging never blocks validation.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default log directory relative to verifiers repo root.
# Used by log_exception() and as a back-compat fallback for JsonLogger
# instances that were not given an explicit log_dir. Phase33b moved
# per-project metric logs to ctx.metrics_log_dir; the constant stays
# here for the error-log path which has no ctx.
LOG_DIR = Path(__file__).parent.parent / "logs"

# Sentinel error log — captures every silent exception across hooks/lib
ERROR_LOG_FILE = LOG_DIR / "_errors.jsonl"

# Per-validator log size cap before rotation (10 MB). On overflow we
# rename to "<file>.1" (single FIFO backup) — total cost per validator
# is bounded at ~20 MB. The cap is intentionally larger than typical
# turn-of-life data (a few MB / month) so well-behaved projects never
# rotate, while pathological repos (Phase33 audit found a 421 MB V14
# file) recover automatically.
MAX_LOG_BYTES = 10_000_000


def log_exception(
    source: str,
    error: BaseException,
    context: dict[str, Any] | None = None,
) -> None:
    """Append a structured exception record to logs/_errors.jsonl.

    Replaces ``except Exception: pass`` so that crashes are recoverable
    via post-mortem analysis without ever blocking the user's turn.
    Failure of the logger itself is silently swallowed (the goal is to
    never propagate logging errors back into the hook pipeline).

    Args:
        source: Identifier of the call site (validator id, hook name, or
            ``"router"`` / ``"stop_validator"``).
        error: The caught exception.
        context: Optional extra fields (file, mode, cwd, ...).
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        }
        if context:
            record["context"] = context
        with open(ERROR_LOG_FILE, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass

    if os.environ.get("VERIFIERS_DEBUG") == "1":
        try:
            print(
                f"[verifiers-debug] {source}: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
        except OSError:
            pass


def _maybe_rotate(log_file: Path, max_bytes: int = MAX_LOG_BYTES) -> None:
    """Rotate ``log_file`` to ``<file>.1`` if it exceeds ``max_bytes``.

    Single-backup FIFO: any prior ``.1`` is overwritten. Failure to
    rotate is silently swallowed so logging never blocks validation.
    """
    try:
        if log_file.exists() and log_file.stat().st_size > max_bytes:
            backup = log_file.with_suffix(log_file.suffix + ".1")
            if backup.exists():
                backup.unlink()
            log_file.rename(backup)
    except OSError:
        pass


class JsonLogger:
    """Append-only JSONL logger for validator results.

    Phase33b+: BaseValidator.run() now passes ``ctx.metrics_log_dir``
    so each project owns its own ``V##.jsonl`` files. The ``log_dir``
    parameter still defaults to ``LOG_DIR`` (verifiers-source ``logs/``)
    for back-compat with the standalone CLI (``run_single``) and tests
    that don't construct a ProjectContext.

    Logging failure (full disk, missing perms) is silently swallowed —
    the goal is to never propagate logger errors back into the hook
    pipeline. Rotation runs on every ``log()`` call so writers don't
    pile up unbounded. ``mkdir`` is lazy in ``log()`` rather than at
    construction time so creating a logger for a path that does not
    yet exist (e.g., a brand-new project's ``.verifiers/state/``) is
    side-effect-free until something actually needs to be written.
    """

    def __init__(self, validator_id: str, log_dir: Path | None = None):
        self.validator_id = validator_id
        self.log_dir = log_dir or LOG_DIR
        self.log_file = self.log_dir / f"{validator_id}.jsonl"
        self._start_time: float | None = None

    def start(self) -> None:
        """Mark the start of a validation run."""
        self._start_time = time.monotonic()

    def log(self, project_name: str, findings: list[dict[str, Any]], mode: str = "post_tool_use") -> None:
        """Log a validation result as a single JSONL line.

        Creates the log directory on first write (so a project that
        hasn't run any validator yet doesn't accumulate empty
        ``.verifiers/state/metrics/`` directories) and rotates the
        target file if it grew past ``MAX_LOG_BYTES``.
        """
        duration_ms = 0
        if self._start_time is not None:
            duration_ms = int((time.monotonic() - self._start_time) * 1000)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "validator": self.validator_id,
            "project": project_name,
            "mode": mode,
            "duration_ms": duration_ms,
            "findings_count": len(findings),
            "error_count": sum(1 for f in findings if f.get("severity") == "error"),
            "warning_count": sum(1 for f in findings if f.get("severity") == "warning"),
        }

        # Only include findings summary to keep logs compact
        if findings:
            entry["findings"] = [
                {"rule": f.get("rule", ""), "severity": f.get("severity", ""), "file": f.get("file", "")}
                for f in findings
            ]

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            _maybe_rotate(self.log_file)
            with open(self.log_file, "a") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Logging failure should never block validation
