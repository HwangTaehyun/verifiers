"""Tests for FeedbackTracker — repeated violation detection.

Covers:
  - record: single and bulk finding recording
  - get_repeated_violations: threshold-based detection
  - get_session_summary: complete session statistics
  - format_feedback_message: human-readable output
  - save_session: JSONL persistence
  - reset: tracker state clearing
  - has_repeated_violations: property check
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hooks.validators.base import Finding
from lib.feedback_tracker import DEFAULT_REPEAT_THRESHOLD, FeedbackTracker, SessionFeedback


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_finding(
    rule: str = "V01-TEST",
    severity: str = "warning",
    file: str = "/project/test.py",
    message: str = "test finding",
) -> Finding:
    return Finding(
        severity=severity,
        file=file,
        rule=rule,
        message=message,
        fix="Fix it",
    )


@pytest.fixture
def tracker() -> FeedbackTracker:
    return FeedbackTracker(session_id="test-session-001")


# ============================================================================
# 1. record
# ============================================================================


class TestRecord:
    def test_record_single(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding())
        assert tracker.total_findings == 1

    def test_record_multiple(self, tracker: FeedbackTracker) -> None:
        for _ in range(5):
            tracker.record(_make_finding())
        assert tracker.total_findings == 5

    def test_record_all(self, tracker: FeedbackTracker) -> None:
        findings = [_make_finding(rule=f"V{i:02d}-TEST") for i in range(3)]
        tracker.record_all(findings)
        assert tracker.total_findings == 3

    def test_record_tracks_files(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding(file="/a.py"))
        tracker.record(_make_finding(file="/b.py"))
        tracker.record(_make_finding(file="/a.py"))
        summary = tracker.get_session_summary()
        assert summary.rule_counts["V01-TEST"] == 3

    def test_record_tracks_severity(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding(severity="warning"))
        tracker.record(_make_finding(severity="error"))
        repeated = tracker.get_repeated_violations()
        # Not yet at threshold, so empty
        assert len(repeated) == 0


# ============================================================================
# 2. get_repeated_violations
# ============================================================================


class TestRepeatedViolations:
    def test_below_threshold_empty(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding())
        tracker.record(_make_finding())
        assert tracker.get_repeated_violations() == []

    def test_at_threshold_detected(self, tracker: FeedbackTracker) -> None:
        for _ in range(DEFAULT_REPEAT_THRESHOLD):
            tracker.record(_make_finding())
        repeated = tracker.get_repeated_violations()
        assert len(repeated) == 1
        assert repeated[0]["rule"] == "V01-TEST"
        assert repeated[0]["count"] == DEFAULT_REPEAT_THRESHOLD

    def test_above_threshold_detected(self, tracker: FeedbackTracker) -> None:
        for _ in range(DEFAULT_REPEAT_THRESHOLD + 2):
            tracker.record(_make_finding())
        repeated = tracker.get_repeated_violations()
        assert len(repeated) == 1
        assert repeated[0]["count"] == DEFAULT_REPEAT_THRESHOLD + 2

    def test_multiple_rules_some_repeated(self, tracker: FeedbackTracker) -> None:
        # Rule A: 5 violations (above threshold)
        for _ in range(5):
            tracker.record(_make_finding(rule="V01-RULE-A"))
        # Rule B: 1 violation (below threshold)
        tracker.record(_make_finding(rule="V02-RULE-B"))
        # Rule C: 3 violations (at threshold)
        for _ in range(3):
            tracker.record(_make_finding(rule="V03-RULE-C"))

        repeated = tracker.get_repeated_violations()
        rules = {r["rule"] for r in repeated}
        assert "V01-RULE-A" in rules
        assert "V03-RULE-C" in rules
        assert "V02-RULE-B" not in rules

    def test_sorted_by_count(self, tracker: FeedbackTracker) -> None:
        for _ in range(3):
            tracker.record(_make_finding(rule="V01-LOW"))
        for _ in range(7):
            tracker.record(_make_finding(rule="V02-HIGH"))
        for _ in range(5):
            tracker.record(_make_finding(rule="V03-MID"))

        repeated = tracker.get_repeated_violations()
        counts = [r["count"] for r in repeated]
        assert counts == [7, 5, 3]

    def test_custom_threshold(self) -> None:
        tracker = FeedbackTracker(threshold=2, session_id="test")
        tracker.record(_make_finding())
        tracker.record(_make_finding())
        assert len(tracker.get_repeated_violations()) == 1

    def test_files_tracked_per_rule(self, tracker: FeedbackTracker) -> None:
        for i in range(3):
            tracker.record(_make_finding(file=f"/project/file{i}.py"))
        repeated = tracker.get_repeated_violations()
        assert len(repeated) == 1
        assert len(repeated[0]["files"]) == 3

    def test_severity_escalation(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding(severity="warning"))
        tracker.record(_make_finding(severity="error"))
        tracker.record(_make_finding(severity="warning"))
        repeated = tracker.get_repeated_violations()
        assert len(repeated) == 1
        assert repeated[0]["severity"] == "error"  # highest severity kept


# ============================================================================
# 3. get_session_summary
# ============================================================================


class TestSessionSummary:
    def test_empty_tracker(self, tracker: FeedbackTracker) -> None:
        summary = tracker.get_session_summary()
        assert summary.total_findings == 0
        assert summary.unique_rules == 0
        assert summary.repeated_rules == []

    def test_with_findings(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding(rule="V01-A"))
        tracker.record(_make_finding(rule="V01-A"))
        tracker.record(_make_finding(rule="V01-A"))
        tracker.record(_make_finding(rule="V02-B"))

        summary = tracker.get_session_summary()
        assert summary.total_findings == 4
        assert summary.unique_rules == 2
        assert summary.session_id == "test-session-001"
        assert isinstance(summary, SessionFeedback)

    def test_rule_counts_dict(self, tracker: FeedbackTracker) -> None:
        for _ in range(4):
            tracker.record(_make_finding(rule="V01-A"))
        tracker.record(_make_finding(rule="V02-B"))
        summary = tracker.get_session_summary()
        assert summary.rule_counts["V01-A"] == 4
        assert summary.rule_counts["V02-B"] == 1


# ============================================================================
# 4. format_feedback_message
# ============================================================================


class TestFormatMessage:
    def test_no_repeated_returns_none(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding())
        assert tracker.format_feedback_message() is None

    def test_repeated_returns_message(self, tracker: FeedbackTracker) -> None:
        for _ in range(3):
            tracker.record(_make_finding(rule="V01-TEST"))
        msg = tracker.format_feedback_message()
        assert msg is not None
        assert "REPEATED VIOLATION" in msg
        assert "V01-TEST" in msg
        assert "3 times" in msg

    def test_multiple_rules_in_message(self, tracker: FeedbackTracker) -> None:
        for _ in range(3):
            tracker.record(_make_finding(rule="V01-A"))
        for _ in range(4):
            tracker.record(_make_finding(rule="V02-B"))
        msg = tracker.format_feedback_message()
        assert msg is not None
        assert "V01-A" in msg
        assert "V02-B" in msg

    def test_file_count_in_message(self, tracker: FeedbackTracker) -> None:
        for i in range(3):
            tracker.record(_make_finding(rule="V01-A", file=f"/project/file{i}.py"))
        msg = tracker.format_feedback_message()
        assert msg is not None
        assert "3 file(s)" in msg

    def test_many_files_truncated(self, tracker: FeedbackTracker) -> None:
        for i in range(5):
            tracker.record(_make_finding(rule="V01-A", file=f"/project/file{i}.py"))
        msg = tracker.format_feedback_message()
        assert msg is not None
        assert "+2 more" in msg


# ============================================================================
# 5. save_session (JSONL persistence)
# ============================================================================


class TestSaveSession:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        tracker = FeedbackTracker(log_dir=tmp_path, session_id="test-save")
        for _ in range(3):
            tracker.record(_make_finding())
        tracker.save_session()

        log_file = tmp_path / "feedback.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        entry = json.loads(content.strip())
        assert entry["session_id"] == "test-save"
        assert entry["total_findings"] == 3
        assert entry["unique_rules"] == 1

    def test_save_appends(self, tmp_path: Path) -> None:
        for i in range(2):
            tracker = FeedbackTracker(log_dir=tmp_path, session_id=f"session-{i}")
            tracker.record(_make_finding())
            tracker.save_session()

        log_file = tmp_path / "feedback.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_save_includes_repeated_rules(self, tmp_path: Path) -> None:
        tracker = FeedbackTracker(log_dir=tmp_path, session_id="test-repeated")
        for _ in range(4):
            tracker.record(_make_finding(rule="V01-REPEAT"))
        tracker.save_session()

        log_file = tmp_path / "feedback.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert len(entry["repeated_rules"]) == 1
        assert entry["repeated_rules"][0]["rule"] == "V01-REPEAT"


# ============================================================================
# 6. Properties and reset
# ============================================================================


class TestPropertiesAndReset:
    def test_has_repeated_violations_false(self, tracker: FeedbackTracker) -> None:
        tracker.record(_make_finding())
        assert tracker.has_repeated_violations is False

    def test_has_repeated_violations_true(self, tracker: FeedbackTracker) -> None:
        for _ in range(3):
            tracker.record(_make_finding())
        assert tracker.has_repeated_violations is True

    def test_reset_clears_state(self, tracker: FeedbackTracker) -> None:
        for _ in range(5):
            tracker.record(_make_finding())
        tracker.reset()
        assert tracker.total_findings == 0
        assert tracker.get_repeated_violations() == []
        assert not tracker.has_repeated_violations

    def test_session_id_auto_generated(self) -> None:
        tracker = FeedbackTracker()
        assert tracker.session_id  # Not empty
        assert "-" in tracker.session_id  # Contains separator

    def test_default_threshold(self) -> None:
        assert DEFAULT_REPEAT_THRESHOLD == 3
