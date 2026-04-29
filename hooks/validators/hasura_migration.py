"""V04: Hasura migration validator — timestamp ordering, up/down pairs, DDL safety.

Checks:
  V04-TIMESTAMP-ORDER: Migration directories not in ascending timestamp order
  V04-DUPLICATE-TIMESTAMP: Multiple migrations with same timestamp
  V04-MISSING-FILE: Migration missing up.sql or down.sql
  V04-DANGEROUS-DDL: Dangerous DDL in up.sql (DROP TABLE, TRUNCATE, etc.)
  V04-METADATA-ORPHAN: Metadata references table not created in migrations
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Dangerous DDL patterns ───────────────────────────────────────────────────

DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\bDROP\s+TABLE\b(?!\s+IF\s+EXISTS)", "DROP TABLE without IF EXISTS"),
    (r"\bDROP\s+COLUMN\b", "DROP COLUMN — data loss risk"),
    (r"\bTRUNCATE\b", "TRUNCATE — all data will be deleted"),
    (r"\bALTER\s+TYPE\b", "ALTER TYPE — may require table rewrite"),
]


class HasuraMigrationValidator(BaseValidator):
    """V04: Hasura Migration Validator."""

    id = "V04-hasura-migration"
    name = "Hasura Migration Validator"
    file_patterns: list[str] = [
        "**/hasura/migrations/**/*.sql",
        "**/hasura/metadata/**/*.yaml",
        "**/hasura/metadata/**/*.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-file Hasura migration check (Tier 2)."""
        if not ctx.hasura_dir or not ctx.hasura_dir.exists():
            return []
        migration_dir = self._find_migration_dir(ctx)
        if not migration_dir:
            return []

        findings = self._common_checks(migration_dir)
        if file_path.endswith("up.sql"):
            findings.extend(self._check_dangerous_ddl(file_path))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide migration sweep + metadata consistency (Tier 3)."""
        if not ctx.hasura_dir or not ctx.hasura_dir.exists():
            return []
        migration_dir = self._find_migration_dir(ctx)
        if not migration_dir:
            return []

        findings = self._common_checks(migration_dir)
        for sql_file in migration_dir.rglob("up.sql"):
            findings.extend(self._check_dangerous_ddl(str(sql_file)))
        findings.extend(self._check_metadata_consistency(ctx, migration_dir))
        return findings

    def _common_checks(self, migration_dir: Path) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._check_timestamp_ordering(migration_dir))
        findings.extend(self._check_duplicate_timestamps(migration_dir))
        findings.extend(self._check_up_down_pairing(migration_dir))
        return findings

    def _find_migration_dir(self, ctx: ProjectContext) -> Path | None:
        """Find the migration directory for the current project."""
        if not ctx.hasura_dir:
            return None

        base = ctx.hasura_dir / "migrations"
        if not base.exists():
            return None

        # Try project-specific subdirectory first
        if ctx.project_name:
            project_dir = base / ctx.project_name
            if project_dir.exists():
                return project_dir

        # Fall back to any subdirectory
        subdirs = [d for d in base.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            return subdirs[0]

        return base

    # ── Check 1: Timestamp ordering ──────────────────────────────────────

    def _check_timestamp_ordering(self, migration_dir: Path) -> list[Finding]:
        """Migration directory timestamps must be in ascending order."""
        findings: list[Finding] = []
        timestamps: list[str] = []

        for d in sorted(migration_dir.iterdir()):
            if not d.is_dir():
                continue
            match = re.match(r"^(\d{12,14})_", d.name)
            if match:
                ts = match.group(1)
                if timestamps and ts <= timestamps[-1]:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(d),
                            rule="V04-TIMESTAMP-ORDER",
                            message=f"Timestamp {ts} is not after previous {timestamps[-1]}",
                            fix=f"Rename migration directory {d.name} with a timestamp after {timestamps[-1]}",
                        )
                    )
                timestamps.append(ts)

        return findings

    # ── Check 2: Duplicate timestamps ────────────────────────────────────

    def _check_duplicate_timestamps(self, migration_dir: Path) -> list[Finding]:
        """No two migrations should share the same timestamp."""
        findings: list[Finding] = []
        ts_map: dict[str, list[str]] = defaultdict(list)

        for d in migration_dir.iterdir():
            if not d.is_dir():
                continue
            match = re.match(r"^(\d{12,14})_", d.name)
            if match:
                ts_map[match.group(1)].append(d.name)

        for ts, dirs in ts_map.items():
            if len(dirs) > 1:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(migration_dir),
                        rule="V04-DUPLICATE-TIMESTAMP",
                        message=f"Duplicate timestamp {ts}: {', '.join(dirs)}",
                        fix=f"Remove or rename one of: {', '.join(dirs)}",
                    )
                )

        return findings

    # ── Check 3: up/down file pairs ──────────────────────────────────────

    def _check_up_down_pairing(self, migration_dir: Path) -> list[Finding]:
        """Every migration must have both up.sql and down.sql."""
        findings: list[Finding] = []

        for d in migration_dir.iterdir():
            if not d.is_dir() or not re.match(r"^\d+_", d.name):
                continue

            has_up = (d / "up.sql").exists()
            has_down = (d / "down.sql").exists()

            if not has_up or not has_down:
                missing = "up.sql" if not has_up else "down.sql"
                findings.append(
                    Finding(
                        severity="error",
                        file=str(d),
                        rule="V04-MISSING-FILE",
                        message=f"Migration {d.name} is missing {missing}",
                        fix=(
                            f"Create {missing} in {d}. "
                            f"For up.sql: add forward migration. "
                            f"For down.sql: add rollback (DROP IF EXISTS)"
                        ),
                    )
                )

        return findings

    # ── Check 4: Dangerous DDL in up.sql ─────────────────────────────────

    def _check_dangerous_ddl(self, file_path: str) -> list[Finding]:
        """Detect dangerous DDL patterns in up.sql files.

        A match is suppressed when:
          1. The match itself is inside a SQL line comment (`-- ...`). This
             prevents the "INTENTIONAL" acknowledgement comment — which by
             definition *contains* the dangerous keyword — from being
             counted as its own violation.
          2. The immediately preceding non-blank line is an intent marker
             of the form `-- INTENTIONAL: ...`. This lets authors opt into
             a destructive DDL statement with an audit trail.
        """
        if not file_path.endswith("up.sql"):
            return []

        try:
            content = Path(file_path).read_text()
        except OSError:
            return []

        lines = content.split("\n")
        findings: list[Finding] = []

        for pattern, desc in DANGEROUS_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                line_num = content[: match.start()].count("\n") + 1
                line_text = lines[line_num - 1] if line_num - 1 < len(lines) else ""

                # Rule 1: skip if the match is inside a line comment. SQL
                # line comments start with `--` and run to end-of-line, so
                # if `--` appears before the match column on the same line,
                # we're inside a comment.
                stripped = line_text.lstrip()
                if stripped.startswith("--"):
                    continue
                comment_start = line_text.find("--")
                if comment_start != -1:
                    # Convert absolute match.start() to column on this line.
                    line_start = content.rfind("\n", 0, match.start()) + 1
                    col = match.start() - line_start
                    if col >= comment_start:
                        continue

                # Rule 2: skip if any of the few lines immediately above the
                # statement is an `-- INTENTIONAL:` acknowledgement. We look
                # up to 3 lines back to tolerate a short rationale block.
                acknowledged = False
                for offset in range(1, 4):
                    prev_idx = line_num - 1 - offset
                    if prev_idx < 0:
                        break
                    prev = lines[prev_idx].strip()
                    if not prev:
                        continue
                    if prev.upper().startswith("-- INTENTIONAL"):
                        acknowledged = True
                        break
                    if not prev.startswith("--"):
                        # Hit a real SQL line — stop walking back.
                        break
                if acknowledged:
                    continue

                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V04-DANGEROUS-DDL",
                        message=f"Dangerous DDL in up.sql: {desc}",
                        fix=(
                            f"Review carefully. If intentional, add a comment "
                            f"'-- INTENTIONAL: {desc}' above the statement"
                        ),
                        line=line_num,
                    )
                )

        return findings

    # ── Check 5: Metadata ↔ migration consistency ────────────────────────

    def _check_metadata_consistency(self, ctx: ProjectContext, migration_dir: Path) -> list[Finding]:
        """Tables in metadata should have CREATE TABLE in migrations."""
        findings: list[Finding] = []

        if not ctx.hasura_dir or not ctx.project_name:
            return findings

        # Collect tables from metadata
        tables_dir = ctx.hasura_dir / "metadata" / "databases" / ctx.project_name / "tables"
        if not tables_dir.exists():
            return findings

        metadata_tables: set[str] = set()
        for f in tables_dir.glob("public_*.yaml"):
            table_name = f.stem.replace("public_", "")
            metadata_tables.add(table_name)

        # Collect tables from migrations
        migration_tables: set[str] = set()
        for sql_file in migration_dir.rglob("up.sql"):
            try:
                content = sql_file.read_text()
            except OSError:
                continue
            for match in re.finditer(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?(\w+)",
                content,
                re.IGNORECASE,
            ):
                migration_tables.add(match.group(1))

        # Report orphan metadata
        for table in sorted(metadata_tables - migration_tables):
            findings.append(
                Finding(
                    severity="warning",
                    file=str(tables_dir),
                    rule="V04-METADATA-ORPHAN",
                    message=f"Table '{table}' has metadata but no CREATE TABLE in migrations",
                    fix=(f"Either create a migration for table '{table}' or remove {tables_dir}/public_{table}.yaml"),
                )
            )

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
    validator = HasuraMigrationValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
