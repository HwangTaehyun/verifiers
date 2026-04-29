"""Base validator interface and data structures.

All validators inherit from BaseValidator and produce Finding objects.
The format_output function converts findings into Claude Code hook JSON.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatch
from typing import Any

from lib.json_logger import JsonLogger
from lib.project_context import ProjectContext


@dataclass
class Finding:
    """A single validation finding.

    ``kind`` distinguishes ordinary findings (the validator detected a
    rule violation) from sentinels (the validator itself crashed or
    timed out). Sentinels must NEVER be silenced by ``exclude.paths``
    — that would let a worker death pass as a clean approve, defeating
    the whole point of having a sentinel. The Phase36 (A4) audit fix
    is that ``stop_validator._apply_exclude_filters`` checks
    ``f.kind == "sentinel"`` and short-circuits before the glob match.
    """

    severity: str  # "error" | "warning" | "info"
    file: str  # Absolute file path
    rule: str  # "V01-ENV-MISSING" format
    message: str  # Human-readable description
    fix: str  # Specific fix instruction for the agent
    line: int | None = None  # Line number (if applicable)
    kind: str = "finding"  # "finding" (default) | "sentinel" (V##-CRASHED, V##-TIMEOUT)


@dataclass
class ValidationResult:
    """Result of a validation run."""

    validator_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)


class BaseValidator:
    """Base class for all validators.

    Subclasses override ``validate_file`` (Tier 2, single file just
    edited) or ``validate_project`` (Tier 3, full-project sweep), or
    both. The ``run()`` entry point dispatches based on the (file_path,
    mode) pair and adds JSON logging around the call.

    Dispatch matrix used by ``run()``:
      (post_tool_use, file_path)    → validate_file
      (post_tool_use, None)         → validate_project (legacy "run all")
      (stop, _)                     → validate_project

    The (post_tool_use, None) fallback handles the legacy
    ``validator.run(ctx)`` shape used by tests and the run_single CLI;
    production hooks always pass either a file_path (router) or
    mode="stop" (parallel_runner).

    Migration history (S4 audit, Phase29-32):
      29 — added validate_file / validate_project + back-compat dispatch.
      30 — migrated V08/V14/V15/V19/V20/V21 to the new API.
      31a — migrated V01/V02/V03/V04/V12/V13/V16.
      31b — migrated V05/V06/V07/V09/V10/V11/V18, plus base dispatch
            handles (post_tool_use, None).
      32 — removed the legacy ``validate(ctx, file_path, mode)`` method
            entirely; ``run()`` now dispatches directly. ABC inheritance
            dropped because there is no abstract method left.
    """

    id: str = ""
    name: str = ""
    file_patterns: list[str] = []

    def __init__(self) -> None:
        # Logger is constructed lazily in run() with ctx.metrics_log_dir
        # so each project's metrics land under its own .verifiers/state/.
        # The instance attribute exists for back-compat with code that
        # may have monkeypatched ``validator.logger`` for testing.
        self.logger = JsonLogger(self.id)

    def should_run(self, file_path: str) -> bool:
        """Check if this validator should run for the given file."""
        if not self.file_patterns:
            return True
        return any(fnmatch(file_path, pattern) for pattern in self.file_patterns)

    # ── Per-tier entry points — subclasses override one or both ──────────

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse) entry point. Single file just edited.

        Default no-op. Override for per-file checks.
        """
        return []

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop) entry point. Full-project sweep.

        Default no-op. Override for project-wide checks.
        """
        return []

    def run(self, ctx: ProjectContext, file_path: str | None = None, mode: str = "post_tool_use") -> ValidationResult:
        """Dispatch to validate_file / validate_project + emit a JSON log line.

        Phase33b: the logger is rebuilt per ``run()`` invocation against
        ``ctx.metrics_log_dir`` so each project's metric history lands
        in its own ``.verifiers/state/metrics/V##.jsonl``. Tests that
        patched ``self.logger`` directly continue to work — the
        rebuilt instance overrides the construction-time default.
        """
        logger = JsonLogger(self.id, log_dir=ctx.metrics_log_dir)
        self.logger = logger
        logger.start()
        findings: list[Finding] = []
        if mode == "stop":
            findings.extend(self.validate_project(ctx))
        elif file_path:
            findings.extend(self.validate_file(ctx, file_path))
        else:
            findings.extend(self.validate_project(ctx))
        result = ValidationResult(validator_id=self.id, findings=findings)
        logger.log(
            project_name=ctx.project_name,
            findings=[asdict(f) for f in result.findings],
            mode=mode,
        )
        return result


def _build_reason(findings: list[Finding], *, mode: str) -> str:
    """Build a concise, actionable reason string for hook blocking.

    The ``reason`` field is what Claude actually sees when blocked.
    It must be short and tell Claude exactly what to fix.

    Args:
        findings: All findings (errors, warnings, info).
        mode: "stop" or "post_tool_use" — controls messaging tone.
    """
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    parts: list[str] = []

    if mode == "post_tool_use":
        parts.append(f"Verification error: {len(errors)} error(s) in the file you just edited.")
        parts.append("Fix these errors NOW before continuing:\n")
        max_errors = 5  # PostToolUse is per-file, keep shorter
        max_warnings = 3
    else:
        parts.append(f"Verification failed: {len(errors)} error(s), {len(warnings)} warning(s).")
        parts.append("Fix ALL errors before stopping:\n")
        max_errors = 10
        max_warnings = 5

    # Show errors first (truncate to max_errors)
    shown = 0
    for f in errors:
        if shown >= max_errors:
            remaining = len(errors) - max_errors
            parts.append(f"  ... and {remaining} more error(s). See additionalContext.")
            break
        loc = f"{f.file}:{f.line}" if f.line else f.file
        parts.append(f"  [{f.rule}] {loc} — {f.fix}")
        shown += 1

    # Show first N warnings as info
    if warnings:
        parts.append(f"\nAlso {len(warnings)} warning(s):")
        for f in warnings[:max_warnings]:
            loc = f"{f.file}:{f.line}" if f.line else f.file
            parts.append(f"  [{f.rule}] {loc} — {f.message}")
        if len(warnings) > max_warnings:
            parts.append(f"  ... and {len(warnings) - max_warnings} more warning(s).")

    return "\n".join(parts)


def _dedup_findings(findings: list[Finding]) -> list[Finding]:
    """Drop duplicate findings keyed on (rule, file, line, message).

    Tier 1 (security_hook) and Tier 3 V08 can both detect the same secret on
    the same line, in which case Claude would see two identical entries —
    extra tokens for nothing, plus the risk of treating an already-fixed
    issue as still-open. We keep the first occurrence to preserve any
    deterministic ordering an upstream caller relied on (P1-7).
    """
    seen: set[tuple[str, str, int | None, str]] = set()
    unique: list[Finding] = []
    for f in findings:
        key = (f.rule, f.file, f.line, f.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique


def format_output(findings: list[Finding], mode: str) -> dict[str, Any]:
    """Convert findings list to Claude Code hook output JSON.

    Both PostToolUse and Stop modes use ``decision: "block"`` + ``reason``
    when errors are found, so Claude can see exactly what to fix.

    - PostToolUse with errors: block + reason (forces Claude to fix immediately)
    - PostToolUse warnings only: additionalContext only (non-blocking)
    - Stop with errors: block + reason (prevents turn from ending)
    - Stop warnings only: approve + additionalContext (informational)

    Findings are de-duplicated on (rule, file, line, message) before
    rendering — see ``_dedup_findings`` (P1-7).
    """
    findings = _dedup_findings(findings)
    if not findings:
        if mode == "stop":
            return {"decision": "approve"}
        return {}

    has_errors = any(f.severity == "error" for f in findings)

    # Build additionalContext string (full detail)
    lines: list[str] = []
    for f in findings:
        icon = "\U0001f6ab" if f.severity == "error" else "\u26a0\ufe0f" if f.severity == "warning" else "\u2139\ufe0f"
        lines.append(f"{icon} VERIFICATION FAILED [{f.rule}]")
        lines.append(f"File: {f.file}")
        if f.line:
            lines.append(f"Line: {f.line}")
        lines.append(f"Issue: {f.message}")
        lines.append("")
        lines.append(f"FIX: {f.fix}")
        lines.append("")
        lines.append("---")
        lines.append("")

    context = "\n".join(lines)

    if mode == "stop":
        if has_errors:
            return {
                "decision": "block",
                "reason": _build_reason(findings, mode="stop"),
                "additionalContext": context,
            }
        return {"decision": "approve", "additionalContext": context}
    else:
        # PostToolUse: block + reason for errors, additionalContext only for warnings
        if has_errors:
            return {
                "decision": "block",
                "reason": _build_reason(findings, mode="post_tool_use"),
                "additionalContext": context,
            }
        return {"additionalContext": context}


# Phase38 / Phase38b (A5 audit): cap stdin reads so a misuse of the
# documented standalone CLI surface (``echo '{...}' | uv run --script
# stop_validator.py``) can't OOM the worker. 10 MiB covers every
# realistic Claude Code payload — measured transcript max line was
# ~528 KiB, lockfile / generated-asset writes top out a few MiB —
# while still cutting off ``yes ... | head -c 10G`` early.
_MAX_STDIN_BYTES = 10 * 1_048_576

# Sentinel key set by ``read_hook_input`` when the cap was hit. The
# Tier 2 / Tier 3 hook entry points check for it and emit a
# ``VERIFIERS-STDIN-TRUNCATED`` warning so a truncated JSON parse
# never silently passes validation.
_TRUNCATED_SENTINEL_KEY = "_verifiers_stdin_truncated"


def read_hook_input() -> dict[str, Any]:
    """Read JSON input from stdin (Claude Code hook protocol).

    Reads at most ``_MAX_STDIN_BYTES + 1`` bytes. If the extra byte
    arrived, the payload was longer than the cap and the resulting
    JSON is almost certainly truncated. In that case we still attempt
    to parse the capped slice (some inputs may be valid JSON for the
    first N bytes) but mark the result with the
    ``_verifiers_stdin_truncated`` sentinel key so the hook entry
    points can emit a warning instead of silent-passing.

    Failed parse → empty dict (legacy behavior preserved). The
    truncation sentinel is appended in either case when the cap was
    hit, so the caller can distinguish "small bad JSON" from "input
    too large".
    """
    raw = sys.stdin.read(_MAX_STDIN_BYTES + 1)
    truncated = len(raw) > _MAX_STDIN_BYTES
    payload = raw[:_MAX_STDIN_BYTES] if truncated else raw

    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, EOFError):
        data = {}

    if truncated:
        data[_TRUNCATED_SENTINEL_KEY] = _MAX_STDIN_BYTES
    return data


def stdin_truncation_finding(cap_bytes: int) -> Finding:
    """Build the warning Finding for a stdin cap-hit (Phase38b, A5).

    Stop / router / security_hook all check ``read_hook_input``'s
    output for the truncation sentinel and emit this finding before
    bailing — silent-passing on truncated input would defeat the
    point of having a hook.
    """
    return Finding(
        severity="warning",
        file="stdin",
        rule="VERIFIERS-STDIN-TRUNCATED",
        message=(
            f"Hook input exceeded the {cap_bytes:,}-byte stdin cap and was truncated. "
            "Validators did not run for this invocation."
        ),
        fix=(
            "Reduce the size of the edit (e.g., split a huge generated-file "
            "rewrite into smaller chunks) or, if the payload is legitimately "
            "this large, raise ``_MAX_STDIN_BYTES`` in lib/validators/base.py."
        ),
        kind="sentinel",
    )


def write_hook_output(output: dict[str, Any]) -> None:
    """Write JSON output to stdout (Claude Code hook protocol)."""
    print(json.dumps(output, ensure_ascii=False))
