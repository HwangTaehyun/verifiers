"""V20: Hasura GraphQL Enforcement Validator.

Enforces GraphQL usage over raw SQL when Hasura is present in the project.

PostToolUse checks (single Go file):
  V20-RAW-SQL-FORBIDDEN: Raw SQL usage forbidden in Hasura projects
  V20-MISSING-GRAPHQL:   Service struct missing GraphQL client field
  V20-SQL-IMPORT:        database/sql package import detected

Stop mode (project-wide):
  Same rules applied across the project's Go files. ``V20-HASURA-FOUND`` is
  emitted as info severity exactly once when Hasura is detected.

V-ID note: Originally drafted under V15 alongside V15-WRONG-DEPENDENCY
(``dependency_guard.py``), but that namespace collision broke the
"V-ID ↔ module 1:1" guarantee that ``run_single.py`` and CATALOG depend on.
Renumbered to V20 in phase 3 of the P0/P1 cleanup; rule strings carry the
new prefix so a single grep ('V20-RAW-SQL-FORBIDDEN' etc.) identifies the
source module without ambiguity.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.json_logger import log_exception
from lib.project_context import ProjectContext


# Files exempt from raw-SQL enforcement: migrations are SQL-by-design,
# tests/mocks/setup/testdata fixtures often contain test SQL strings.
_EXEMPT_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("/migrations/",),  # any migration sql/go
    ("/mocks/",),
    ("/setup/",),
    ("/testdata/",),
)


def _is_exempt(file_path: str) -> bool:
    """Return True if the path is in an exempt directory or is a test file."""
    if file_path.endswith("_test.go") or file_path.endswith(".test.ts"):
        return True
    if file_path.endswith(".sql") and "/migrations/" in file_path:
        return True
    return any(all(token in file_path for token in tokens) for tokens in _EXEMPT_PATTERNS)


# Raw SQL patterns we forbid in Hasura projects.
_SQL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\.Query(?:Row)?Context\s*\("), "raw SQL query"),
    (re.compile(r"\.ExecContext\s*\("), "raw SQL execution"),
    (re.compile(r"\.PrepareContext\s*\("), "raw SQL prepared statement"),
    (re.compile(r"\bSELECT\b\s+.*\bFROM\b", re.IGNORECASE), "raw SQL SELECT"),
    (re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE), "raw SQL INSERT"),
    (re.compile(r"\bUPDATE\b\s+\w+\s+\bSET\b", re.IGNORECASE), "raw SQL UPDATE"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), "raw SQL DELETE"),
]

_DATABASE_SQL_IMPORT = re.compile(r'^\s*"database/sql"', re.MULTILINE)
_SERVICE_STRUCT = re.compile(r"\btype\s+\w*Service\s+struct\b")

# Signals that a file is "DB-handling": presence of any of these implies
# the Service is talking to Postgres directly and SHOULD route through
# the GraphQL client. Used by the V20-MISSING-GRAPHQL heuristic to
# suppress the warning on Services that legitimately have nothing to do
# with the DB (S3/MinIO adapters, third-party API clients, in-memory
# caches, file parsers, health-check services, etc.). Without this
# guard the check fires on every `type *Service struct` regardless of
# domain — a high false-positive rate that forced per_validator config
# bloat in downstream projects.
_DB_DRIVER_SIGNAL = re.compile(
    r"\b("
    r"runHasuraSQL"  # project-local raw-SQL helper
    r"|sqlx\."  # github.com/jmoiron/sqlx
    r"|pgx\."  # github.com/jackc/pgx
    r"|gorm\."  # gorm.io/gorm
    r"|sql\.DB\b"  # *sql.DB type usage
    r"|sql\.Open\b"  # database/sql.Open call site
    r"|pgxpool\."  # pgxpool from pgx/v5
    r")",
)


class HasuraGraphQLEnforcementValidator(BaseValidator):
    """V20: Hasura GraphQL Enforcement — forbids raw SQL when Hasura is present."""

    id = "V20-hasura-graphql"
    name = "Hasura GraphQL Enforcement"
    file_patterns: list[str] = [
        "**/*.go",
        "**/docker-compose.yaml",
        "**/docker-compose.yml",
        "**/hasura/**",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        # Early-exit when Hasura is not part of the project — keeps cost
        # near zero for every non-Hasura repo using the verifier suite.
        if not self._detect_hasura(ctx):
            return ValidationResult(validator_id=self.id, findings=[])

        findings: list[Finding] = []

        if mode == "post_tool_use":
            if file_path and file_path.endswith(".go") and not _is_exempt(file_path):
                findings.extend(self._check_go_file(file_path))
        else:  # stop
            findings.extend(self._scan_project(ctx))

        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Detection ─────────────────────────────────────────────────────────

    def _detect_hasura(self, ctx: ProjectContext) -> bool:
        """Return True iff the project has a hasura/ directory or hasura
        graphql-engine in any compose file."""
        if ctx.hasura_dir is not None:
            return True

        compose_candidates = [
            ctx.project_root / "docker-compose.yaml",
            ctx.project_root / "docker-compose.yml",
        ]
        if ctx.server_dir is not None:
            compose_candidates += [
                ctx.server_dir / "docker-compose.yaml",
                ctx.server_dir / "docker-compose.yml",
            ]

        for compose in compose_candidates:
            if not compose.exists():
                continue
            try:
                content = compose.read_text(errors="replace")
            except OSError as exc:
                log_exception(
                    source="V20-hasura-graphql/_detect_hasura",
                    error=exc,
                    context={"compose": str(compose)},
                )
                continue
            if "hasura/graphql-engine" in content:
                return True

        return False

    # ── Stop-mode scan ───────────────────────────────────────────────────

    def _scan_project(self, ctx: ProjectContext) -> list[Finding]:
        """Scan all Go files under server_dir (or project_root) for SQL violations."""
        findings: list[Finding] = []
        scan_root = ctx.server_dir or ctx.project_root
        for go_file in scan_root.rglob("*.go"):
            fp = str(go_file)
            if _is_exempt(fp):
                continue
            findings.extend(self._check_go_file(fp))
        return findings

    # ── Per-file checks ──────────────────────────────────────────────────

    def _check_go_file(self, file_path: str) -> list[Finding]:
        findings: list[Finding] = []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError as exc:
            log_exception(
                source="V20-hasura-graphql/_check_go_file",
                error=exc,
                context={"file_path": file_path},
            )
            return findings

        # database/sql import
        m = _DATABASE_SQL_IMPORT.search(content)
        if m:
            line_no = content[: m.start()].count("\n") + 1
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V20-SQL-IMPORT",
                    message="database/sql import is forbidden in Hasura projects — use the GraphQL client.",
                    fix=(
                        f"Remove 'database/sql' import at {file_path}:{line_no} "
                        "and replace persistence calls with the genqlient-generated GraphQL client."
                    ),
                    line=line_no,
                )
            )

        # Per-line raw SQL pattern detection
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "/*", "*")):
                continue
            for pattern, description in _SQL_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        Finding(
                            severity="error",
                            file=file_path,
                            rule="V20-RAW-SQL-FORBIDDEN",
                            message=f"Raw SQL forbidden in Hasura project: {description}",
                            fix=(
                                f"Replace the SQL at {file_path}:{i} with a GraphQL "
                                "mutation/query via gqlClient. Hasura projects must "
                                "go through the permission/audit layer."
                            ),
                            line=i,
                        )
                    )
                    break  # one finding per line is enough

        # Service struct missing GraphQL client.
        #
        # Only fires when the file shows real DB-handling intent — otherwise
        # a Service is allowed to omit graphql.Client. Without this guard
        # the check has a high false-positive rate (S3 adapters, Stripe
        # clients, file parsers, in-memory caches, health-check services
        # all have a `type *Service struct` but legitimately never touch
        # Postgres, so demanding a graphql.Client field is meaningless).
        #
        # DB-handling signals:
        #   1. raw SQL pattern in the file body (_SQL_PATTERNS)
        #   2. database/sql import
        #   3. driver-level call site (sqlx, pgx, gorm, sql.DB, ...)
        #
        # Guard: a Service file with NONE of those signals doesn't need
        # a graphql.Client → suppress the warning so per_validator config
        # doesn't accumulate one-off file exemptions per project.
        if _SERVICE_STRUCT.search(content) and "gqlClient" not in content:
            has_sql_pattern = any(p.search(content) for p, _ in _SQL_PATTERNS)
            has_sql_import = _DATABASE_SQL_IMPORT.search(content) is not None
            has_db_driver = _DB_DRIVER_SIGNAL.search(content) is not None
            if has_sql_pattern or has_sql_import or has_db_driver:
                for i, line in enumerate(content.split("\n"), 1):
                    if _SERVICE_STRUCT.search(line):
                        findings.append(
                            Finding(
                                severity="warning",
                                file=file_path,
                                rule="V20-MISSING-GRAPHQL",
                                message="Service struct does not declare a GraphQL client field",
                                fix=(
                                    f"Add 'gqlClient graphql.Client' (or your project's equivalent) "
                                    f"to the Service struct at {file_path}:{i}."
                                ),
                                line=i,
                            )
                        )
                        break

        return findings


# ── Standalone execution ─────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone PostToolUse hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = HasuraGraphQLEnforcementValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")
    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
