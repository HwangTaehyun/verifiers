"""JSONL structured logging for validator results.

Logs each validation run as a single JSON line to logs/<validator-id>.jsonl
with timestamp, project name, duration, and findings.

Also exposes ``log_exception(...)`` for non-blocking error recording —
used in place of ``except Exception: pass`` so that silent crashes leave
a JSONL trace at logs/_errors.jsonl. When the ``VERIFIERS_DEBUG=1``
environment variable is set, errors are also printed to stderr for
interactive debugging.
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

# Default log directory relative to verifiers repo root
LOG_DIR = Path(__file__).parent.parent / "logs"

# Sentinel error log — captures every silent exception across hooks/lib
ERROR_LOG_FILE = LOG_DIR / "_errors.jsonl"


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


class JsonLogger:
    """Append-only JSONL logger for validator results."""

    def __init__(self, validator_id: str, log_dir: Path | None = None):
        self.validator_id = validator_id
        self.log_dir = log_dir or LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"{validator_id}.jsonl"
        self._start_time: float | None = None

    def start(self) -> None:
        """Mark the start of a validation run."""
        self._start_time = time.monotonic()

    def log(self, project_name: str, findings: list[dict[str, Any]], mode: str = "post_tool_use") -> None:
        """Log a validation result as a single JSONL line."""
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
            with open(self.log_file, "a") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Logging failure should never block validation
