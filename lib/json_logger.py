"""JSONL structured logging for validator results.

Logs each validation run as a single JSON line to logs/<validator-id>.jsonl
with timestamp, project name, duration, and findings.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Default log directory relative to verifiers repo root
LOG_DIR = Path(__file__).parent.parent / "logs"


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
