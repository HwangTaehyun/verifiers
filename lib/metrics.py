"""Validator usage metrics — aggregate logs/V##.jsonl into per-validator stats.

Inspired by the "skill lifecycle" debate around self-improving agents
(see Hermes Curator, NousResearch/hermes-agent#7816). Long-lived
self-modifying agents accumulate skills; without usage data we can't
tell which ones are still pulling their weight. The same pressure exists
for validators in the verifiers system, but the failure modes are
different:

  - Validator count is bounded (~20) and registered in code, not
    auto-generated, so raw "bloat" is small.
  - The interesting question is **effectiveness**: a validator that
    fires on every Edit but never emits a finding is paying CPU for
    zero value; a validator that emits the same finding repeatedly
    without the user fixing it is either a false positive or a rule
    the user has decided to ignore.
  - Dormancy mostly resolves itself via ``file_patterns`` — a Go
    validator never fires in a Python project, so cost is 0. Still,
    seeing dormancy spelled out makes it easier to decide which
    validators to disable in ``.verifiers/config.yaml``.

Architecture:
- ``aggregate_metrics(log_dir, since=...)`` walks logs/V##.jsonl,
  parses each JSON line, groups by validator id, and returns a sorted
  list of ``ValidatorMetric``.
- ``ValidatorMetric.state(now)`` classifies the validator into
  ``active`` / ``quiet`` / ``dormant`` using the windows passed in.
- The CLI front (``scripts/validator_metrics.py``) renders a table or
  emits JSON for further pipelines.

Pinned validators (security/regulatory) are exempted from the "consider
disabling" suggestion regardless of dormancy. Phase33 only models the
read side; the pin metadata + archive workflow lives in a follow-up.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ValidatorMetric:
    """Aggregated stats for a single validator over a time window."""

    validator_id: str
    first_seen: datetime | None = None
    last_used: datetime | None = None
    last_finding_at: datetime | None = None
    use_count: int = 0
    findings_emitted: int = 0
    error_findings: int = 0
    warning_findings: int = 0
    total_duration_ms: int = 0

    @property
    def mean_duration_ms(self) -> float:
        if self.use_count == 0:
            return 0.0
        return self.total_duration_ms / self.use_count

    @property
    def effectiveness(self) -> float:
        """Findings emitted per invocation.

        - 1.0 means the validator finds something every time it runs
          (high signal — pulling its weight).
        - 0.0 means it fires but emits nothing (cost without value).

        Intermediate values are common; the metric mostly serves to
        spot validators stuck near 0.0 over many invocations.
        """
        if self.use_count == 0:
            return 0.0
        return self.findings_emitted / self.use_count

    def state(self, now: datetime, dormant_days: int = 14, quiet_days: int = 30) -> str:
        """Lifecycle bucket for this validator.

        Args:
            now: Reference time (usually ``datetime.now(timezone.utc)``).
            dormant_days: A validator unused for this many days is dormant.
            quiet_days: A validator used recently but emitting no findings
                in this window is quiet (cost without value signal).

        Returns one of ``"active"``, ``"quiet"``, ``"dormant"``.
        """
        if self.last_used is None:
            return "dormant"
        if (now - self.last_used).days > dormant_days:
            return "dormant"
        if self.last_finding_at is None or (now - self.last_finding_at).days > quiet_days:
            return "quiet"
        return "active"


def _iter_log_lines(log_file: Path) -> Iterator[dict]:
    """Yield each parsed JSON object from a JSONL file, skipping malformed lines."""
    try:
        fh = log_file.open()
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def aggregate_metrics(log_dir: Path, since: datetime | None = None) -> list[ValidatorMetric]:
    """Walk ``log_dir/V*.jsonl`` and return one ValidatorMetric per validator.

    The ``V*.jsonl`` glob deliberately excludes ``_errors.jsonl`` (no V
    prefix) and any future non-validator log files.

    Args:
        log_dir: Directory containing ``{validator-id}.jsonl`` files.
        since: If provided, log entries with ``timestamp < since`` are
            ignored. Use this to restrict aggregation to a recent window.

    Returns:
        A list of ``ValidatorMetric``, sorted by validator_id.
    """
    metrics: dict[str, ValidatorMetric] = {}

    if not log_dir.exists():
        return []

    for log_file in log_dir.glob("V*.jsonl"):
        for entry in _iter_log_lines(log_file):
            validator_id = entry.get("validator")
            if not isinstance(validator_id, str) or not validator_id:
                continue

            ts = _parse_timestamp(entry.get("timestamp"))
            if ts is None:
                continue

            if since is not None and ts < since:
                continue

            m = metrics.setdefault(validator_id, ValidatorMetric(validator_id=validator_id))
            m.use_count += 1

            duration = entry.get("duration_ms", 0)
            if isinstance(duration, int) and not isinstance(duration, bool):
                m.total_duration_ms += duration

            findings_count = entry.get("findings_count", 0)
            if isinstance(findings_count, int) and not isinstance(findings_count, bool):
                m.findings_emitted += findings_count

            error_count = entry.get("error_count", 0)
            if isinstance(error_count, int) and not isinstance(error_count, bool):
                m.error_findings += error_count

            warning_count = entry.get("warning_count", 0)
            if isinstance(warning_count, int) and not isinstance(warning_count, bool):
                m.warning_findings += warning_count

            if m.first_seen is None or ts < m.first_seen:
                m.first_seen = ts
            if m.last_used is None or ts > m.last_used:
                m.last_used = ts
            if isinstance(findings_count, int) and findings_count > 0:
                if m.last_finding_at is None or ts > m.last_finding_at:
                    m.last_finding_at = ts

    return sorted(metrics.values(), key=lambda v: v.validator_id)
