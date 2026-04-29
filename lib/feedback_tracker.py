"""Feedback Tracker — detect repeated violations within a session.

When the same rule is violated N+ times in a session, the agent is likely
making the same mistake repeatedly. The tracker escalates the warning to
help the agent recognize the pattern.

Design:
  - In-memory tracking during a session (via stop hook)
  - Persistent session log to logs/feedback.jsonl for cross-session analysis
  - Configurable repetition threshold (default: 3)
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hooks.validators.base import Finding

# Default log directory relative to verifiers repo root
LOG_DIR = Path(__file__).parent.parent / "logs"

# Default repetition threshold
DEFAULT_REPEAT_THRESHOLD = 3


@dataclass
class SessionFeedback:
    """Summary of a session's violation patterns."""

    session_id: str
    total_findings: int
    unique_rules: int
    repeated_rules: list[dict[str, Any]]  # [{rule, count, severity, files}]
    rule_counts: dict[str, int]


class FeedbackTracker:
    """Track findings within a session and detect repeated violations.

    Usage:
        tracker = FeedbackTracker()
        tracker.record(finding1)
        tracker.record(finding2)
        ...
        repeated = tracker.get_repeated_violations()
        summary = tracker.get_session_summary()
        tracker.save_session()  # persist to logs/feedback.jsonl
    """

    def __init__(
        self,
        threshold: int = DEFAULT_REPEAT_THRESHOLD,
        log_dir: Path | None = None,
        session_id: str | None = None,
    ):
        self.threshold = threshold
        self.log_dir = log_dir or LOG_DIR
        self._findings: list[Finding] = []
        self._rule_counter: Counter[str] = Counter()
        self._rule_files: dict[str, set[str]] = {}
        self._rule_severities: dict[str, str] = {}
        self.session_id = session_id or self._generate_session_id()

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a session ID from timestamp + PID."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{ts}-{os.getpid()}"

    def record(self, finding: Finding) -> None:
        """Record a finding from any validator."""
        self._findings.append(finding)
        self._rule_counter[finding.rule] += 1

        # Track files per rule
        if finding.rule not in self._rule_files:
            self._rule_files[finding.rule] = set()
        self._rule_files[finding.rule].add(finding.file)

        # Track highest severity per rule (error > warning > info)
        severity_rank = {"error": 3, "warning": 2, "info": 1}
        existing = self._rule_severities.get(finding.rule, "info")
        if severity_rank.get(finding.severity, 0) > severity_rank.get(existing, 0):
            self._rule_severities[finding.rule] = finding.severity

    def record_all(self, findings: list[Finding]) -> None:
        """Record multiple findings at once."""
        for finding in findings:
            self.record(finding)

    def get_repeated_violations(self) -> list[dict[str, Any]]:
        """Get rules that have been violated >= threshold times.

        Returns list of dicts with: rule, count, severity, files.
        Sorted by count (descending).
        """
        repeated = []
        for rule, count in self._rule_counter.most_common():
            if count >= self.threshold:
                repeated.append(
                    {
                        "rule": rule,
                        "count": count,
                        "severity": self._rule_severities.get(rule, "warning"),
                        "files": sorted(self._rule_files.get(rule, set())),
                    }
                )
        return repeated

    def get_session_summary(self) -> SessionFeedback:
        """Generate a complete session summary."""
        return SessionFeedback(
            session_id=self.session_id,
            total_findings=len(self._findings),
            unique_rules=len(self._rule_counter),
            repeated_rules=self.get_repeated_violations(),
            rule_counts=dict(self._rule_counter.most_common()),
        )

    def format_feedback_message(self) -> str | None:
        """Format repeated violations as a human-readable message.

        Returns None if no repeated violations found.
        """
        repeated = self.get_repeated_violations()
        if not repeated:
            return None

        lines = [
            "⚠️ REPEATED VIOLATION PATTERNS DETECTED:",
            "",
        ]
        for item in repeated:
            rule = item["rule"]
            count = item["count"]
            files = item["files"]
            file_list = ", ".join(Path(f).name for f in files[:3])
            if len(files) > 3:
                file_list += f" (+{len(files) - 3} more)"
            lines.append(f"  • {rule}: violated {count} times across {len(files)} file(s) [{file_list}]")

        lines.append("")
        lines.append(
            "These rules keep being violated. Consider reviewing your approach "
            "or fixing the root cause rather than individual files."
        )
        return "\n".join(lines)

    def save_session(self) -> None:
        """Persist session feedback to logs/feedback.jsonl."""
        summary = self.get_session_summary()
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": summary.session_id,
            "total_findings": summary.total_findings,
            "unique_rules": summary.unique_rules,
            "rule_counts": summary.rule_counts,
            "repeated_rules": summary.repeated_rules,
        }

        # Phase37 (A6 audit): 0o700 keeps the feedback log private on
        # shared hosts — it carries rule/finding history that may
        # include file paths.
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.log_dir.chmod(0o700)
        except OSError:
            pass
        log_file = self.log_dir / "feedback.jsonl"
        try:
            with open(log_file, "a") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Logging failure should never block validation

    @property
    def total_findings(self) -> int:
        return len(self._findings)

    @property
    def has_repeated_violations(self) -> bool:
        return any(count >= self.threshold for count in self._rule_counter.values())

    def reset(self) -> None:
        """Clear all tracked findings (start fresh)."""
        self._findings.clear()
        self._rule_counter.clear()
        self._rule_files.clear()
        self._rule_severities.clear()
