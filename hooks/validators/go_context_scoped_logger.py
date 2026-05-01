"""V39: Go Context-Scoped Logger Discipline.

Checks that Go files under ``internal/`` (non-test, non-middleware) use
context-scoped zerolog loggers (``zerolog.Ctx(ctx)`` / ``log.Ctx(ctx)``)
rather than the global logger (``log.Info()``, ``log.Error()``, etc.).

Global zerolog calls without context retrieval silently drop request_id and
other contextual fields, fragmenting distributed traces.

Exemptions
----------
* Files containing ``_test.go`` suffix — test setup often uses global loggers.
* Files under ``middleware/`` directories — middleware is the legitimate place
  to inject the context-scoped logger downstream via ``logger.WithContext(ctx)``.
* Files outside ``internal/`` directories.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.per_file_cache import PerFileCache  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches global zerolog calls: log.Info(, log.Error(, log.Warn(, log.Debug(,
# log.Trace(, log.Fatal(, log.Panic(
_GLOBAL_LOGGER_CALL = re.compile(r"\blog\.(Info|Error|Warn|Debug|Trace|Fatal|Panic)\(")

# Matches context-scoped logger retrieval: zerolog.Ctx(ctx) or log.Ctx(ctx)
_CTX_RETRIEVAL = re.compile(r"\bCtx\(")


def _is_eligible(file_path: Path) -> bool:
    """Return True for non-test, non-middleware Go files under an ``internal/`` directory."""
    path_str = str(file_path)
    if file_path.suffix != ".go":
        return False
    if file_path.name.endswith("_test.go"):
        return False
    if "/internal/" not in path_str:
        return False
    # Skip middleware/ — legitimate place to use global logger to inject context
    if "/middleware/" in path_str:
        return False
    return True


def _scan_file(file_path: Path) -> list[Finding]:
    try:
        text = file_path.read_text(errors="replace")
    except OSError:
        return []

    # Check if file has context-scoped retrieval anywhere
    if _CTX_RETRIEVAL.search(text):
        return []

    # Find the first global logger call
    lines = text.splitlines()
    first_line: int | None = None
    for i, line in enumerate(lines):
        if _GLOBAL_LOGGER_CALL.search(line):
            first_line = i + 1
            break

    if first_line is None:
        return []

    return [
        Finding(
            severity="warning",
            file=str(file_path),
            line=first_line,
            rule="V39-GLOBAL-LOGGER-MISUSE",
            message=(
                "File uses global zerolog calls (log.Info/Error/etc.) but never retrieves a "
                "context-scoped logger via zerolog.Ctx(ctx) / log.Ctx(ctx). Request_id and "
                "other contextual fields must be threaded manually at every call site."
            ),
            fix=(
                "At request entry, attach logger to context: "
                '`logger := log.With().Str("request_id", rid).Logger(); ctx = logger.WithContext(ctx)`. '
                "Downstream, retrieve with `zerolog.Ctx(ctx).Info().Msg(...)`. "
                "If this file legitimately runs outside any request context (cron task, init), "
                "add `//nolint:V39 // background context, no request scope`."
            ),
        )
    ]


class GoContextScopedLoggerValidator(BaseValidator):
    """V39: Go Context-Scoped Logger Discipline."""

    id = "V39-go-context-scoped-logger"
    name = "Go Context-Scoped Logger Discipline"
    file_patterns: list[str] = [
        "server/internal/**/*.go",
        "**/internal/**/*.go",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): scan the single edited file."""
        path = Path(file_path)
        if not _is_eligible(path):
            return []
        return _scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): scan internal/**/*.go for ctx-scoped logger discipline.

        Phase 69: per-file mtime cache (Phase 64.4 pattern). Findings are
        purely a function of file content. Uses ``ctx.file_index``
        (Phase 65) for the file walk.
        """
        if ctx.server_dir is None:
            return []
        server_resolved = ctx.server_dir.resolve()
        cache = PerFileCache.load(ctx.project_root, self.id, config_fingerprint="")

        findings: list[Finding] = []
        for candidate in ctx.file_index.find_by_pattern("*.go"):
            try:
                candidate.resolve().relative_to(server_resolved)
            except (ValueError, OSError):
                continue
            if not _is_eligible(candidate):
                continue
            try:
                mtime_ns = candidate.stat().st_mtime_ns
            except OSError:
                continue
            cached = cache.get(str(candidate), mtime_ns)
            if cached is not None:
                findings.extend(cached)
                continue
            fresh = _scan_file(candidate)
            cache.put(str(candidate), mtime_ns, fresh)
            findings.extend(fresh)
        cache.save()
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoContextScopedLoggerValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
