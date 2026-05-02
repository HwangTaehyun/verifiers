"""Core validator data classes — Phase 71 T3 (architectural review H1).

Where Phase 71 T1+T2+T4 chased per-Stop wall-clock wins, T3 fixes a
**layering invariant** that's been silently broken since the lib/
directory grew its first cache module: ``lib/parallel_runner.py``,
``lib/per_file_cache.py``, ``lib/feedback_tracker.py``, and
``lib/validator_registry.py`` all imported ``Finding`` / ``BaseValidator``
from ``hooks/validators/base.py``. That's a lower-level package
(``lib/``) reaching into a higher-level one (``hooks/``) — classic
dependency inversion. Adding any new ``lib/`` cache module that needs
``Finding`` deepened the violation.

This module hosts the pure-data + abstract-base parts that downstream
``lib/`` modules genuinely need:

  - :class:`Finding`             — single validation finding
  - :class:`ValidationResult`    — collected findings from one run
  - :class:`BaseValidator`       — abstract validator base
  - :func:`_compile_patterns`    — fnmatch → regex memoization

``hooks/validators/base.py`` re-exports the same names so existing
imports keep working (back-compat shim). New code should import from
``lib.validators_core`` directly.

What stays in ``hooks/validators/base.py``:

  - hook I/O helpers (``read_hook_input``, ``write_hook_output``)
  - finding → JSON output formatters (``format_output``,
    ``_build_reason``, ``_dedup_findings``)
  - the truncation sentinel builder (``stdin_truncation_finding``)

Those are tier-specific (Claude Code hook protocol) and don't belong in
``lib/``.

Citation: architecture review H1 (architecture-strategist agent,
2026-05-02 session). The agent flagged the cycle and recommended this
exact split.
"""

from __future__ import annotations

import functools
import re
from dataclasses import asdict, dataclass, field
from fnmatch import translate

from lib.json_logger import JsonLogger
from lib.project_context import ProjectContext


@dataclass
class Finding:
    """A single validation finding.

    ``kind`` distinguishes ordinary findings (the validator detected a
    rule violation) from sentinels (the validator itself crashed or
    timed out). Sentinels must NEVER be silenced by ``exclude.paths``
    — that would let a worker death pass as a clean approve, defeating
    the whole point of having a sentinel. The Phase 36 (A4) audit fix
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


# ── Phase 62-N3: pre-compiled file_patterns ────────────────────────────


@functools.lru_cache(maxsize=128)
def _compile_patterns(patterns: tuple[str, ...]) -> tuple["re.Pattern[str]", ...]:
    """Translate fnmatch globs to compiled regex once per pattern set.

    Used by ``BaseValidator.should_run`` to avoid the per-call
    ``fnmatch.translate`` + ``re.compile`` cost on every Tier-1/Tier-2
    dispatch (49 validators × N patterns × frequency-of-edits).

    The lru_cache is keyed by the patterns tuple, so all instances of
    the same validator class share one compiled set.
    """
    return tuple(re.compile(translate(p)) for p in patterns)


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

    Migration history (S4 audit, Phase 29-32):
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
        """Check if this validator should run for the given file.

        Phase 62-N3: file_patterns are pre-compiled to regex once per
        validator class via ``_compile_patterns``. The compilation
        happens on first invocation; subsequent calls hit the
        module-level lru_cache. Eliminates the per-call ``fnmatch``
        translation cost (~50-100ms across 49 validators per edit).
        """
        if not self.file_patterns:
            return True
        compiled = _compile_patterns(tuple(self.file_patterns))
        return any(p.match(file_path) for p in compiled)

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

        Phase 33b: the logger is rebuilt per ``run()`` invocation against
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
