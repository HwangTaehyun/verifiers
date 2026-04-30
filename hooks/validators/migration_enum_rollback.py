"""V46: Migration Enum Rollback — ALTER TYPE ADD VALUE must have a reversible down.sql.

PostgreSQL does not support ALTER TYPE … DROP VALUE (versions 1–16+). There is no
way to remove an enum value once added. The canonical workaround is rename-swap.

Rules:
  V46-ENUM-IRREVERSIBLE: up.sql contains ALTER TYPE … ADD VALUE but the paired
                          down.sql neither contains an ALTER TABLE (rename-swap
                          indicator) nor the marker -- MANUAL ROLLBACK REQUIRED.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Regex patterns ────────────────────────────────────────────────────────────

# Detects ALTER TYPE <name> ADD VALUE in up.sql (case-insensitive)
_RE_ADD_VALUE = re.compile(
    r"ALTER\s+TYPE\s+\S+\s+ADD\s+VALUE",
    re.IGNORECASE,
)

# Detects ALTER TABLE ... (rename-swap rollback indicator) in down.sql.
# We match any ALTER TABLE occurrence that is NOT inside a SQL comment line.
_RE_ALTER_TABLE = re.compile(
    r"^\s*ALTER\s+TABLE\b",
    re.IGNORECASE | re.MULTILINE,
)

# Manual opt-out marker (case-insensitive per spec recommendation)
_MANUAL_MARKER = re.compile(
    r"--\s*MANUAL\s+ROLLBACK\s+REQUIRED",
    re.IGNORECASE,
)


class MigrationEnumRollbackValidator(BaseValidator):
    """V46: Migration Enum Rollback — enum ADD VALUE must have reversible down.sql."""

    id = "V46-migration-enum-rollback"
    name = "Hasura Migration Enum Rollback"
    file_patterns: list[str] = [
        "**/migrations/**/up.sql",
        "**/migrations/**/down.sql",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): when an up.sql or down.sql is edited, run full check."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): walk all migration directories under project root."""
        return self._all_checks(ctx)

    # ── Core logic ────────────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        migration_dirs = self._find_migration_dirs(ctx)
        if not migration_dirs:
            return []

        # Collect all up.sql files across the full migration tree (may be
        # multiple levels deep: migrations/<database>/<timestamp>_<name>/up.sql)
        up_sql_files: list[Path] = []
        for mdir in migration_dirs:
            up_sql_files.extend(mdir.rglob("up.sql"))

        if not up_sql_files:
            return []

        findings: list[Finding] = []
        for up_file in sorted(up_sql_files, key=lambda p: p.parent.name):
            down_file = up_file.parent / "down.sql"
            findings.extend(self._check_enum_reversible(up_file, down_file))

        return findings

    def _find_migration_dirs(self, ctx: ProjectContext) -> list[Path]:
        """Find all migration directories under the project root."""
        root = ctx.project_root

        candidates: list[Path] = []
        for pattern in (
            "server/hasura/migrations",
            "hasura/migrations",
        ):
            p = root / pattern
            if p.exists() and p.is_dir():
                candidates.append(p)

        # Generic fallback: any migrations/ directory with up.sql files
        if not candidates:
            for mdir in root.rglob("migrations"):
                if mdir.is_dir() and list(mdir.rglob("up.sql")):
                    candidates.append(mdir)

        return candidates

    def _check_enum_reversible(self, up_file: Path, down_file: Path) -> list[Finding]:
        """Return V46-ENUM-IRREVERSIBLE findings for this migration pair."""
        try:
            up_text = up_file.read_text()
        except OSError:
            return []

        # Skip if no ALTER TYPE ... ADD VALUE in up.sql
        if not _RE_ADD_VALUE.search(up_text):
            return []

        # If down.sql is missing, flag it
        if not down_file.exists():
            return [
                Finding(
                    severity="warning",
                    file=str(up_file),
                    rule="V46-ENUM-IRREVERSIBLE",
                    message=(
                        "up.sql adds enum value(s) via `ALTER TYPE ... ADD VALUE` but down.sql neither performs "
                        "a rename-swap rollback (ALTER TABLE) nor declares `-- MANUAL ROLLBACK REQUIRED`. "
                        "PostgreSQL has no `ALTER TYPE ... DROP VALUE`; this migration is silently irreversible."
                    ),
                    fix=(
                        "Either:\n"
                        "  (a) Implement rename-swap in down.sql:\n"
                        "      ALTER TYPE foo RENAME TO foo_legacy;\n"
                        "      CREATE TYPE foo AS ENUM (...);  -- without the new values\n"
                        "      ALTER TABLE t ALTER COLUMN c TYPE foo USING (c::text::foo);\n"
                        "      DROP TYPE foo_legacy;\n"
                        "  (b) Or add the marker to down.sql so V46 stops complaining:\n"
                        "      -- MANUAL ROLLBACK REQUIRED: enum values cannot be dropped from finance_billing_cycle"
                    ),
                )
            ]

        try:
            down_text = down_file.read_text()
        except OSError:
            return []

        # If down.sql has the manual marker (case-insensitive), it's acceptable
        if _MANUAL_MARKER.search(down_text):
            return []

        # If down.sql has ALTER TABLE at the start of a line (rename-swap indicator)
        # This filters out commented-out ALTER TABLE lines since _RE_ALTER_TABLE
        # uses ^ which matches line start, but comments start with -- not ALTER.
        if _RE_ALTER_TABLE.search(down_text):
            return []

        # Neither indicator present — flag it
        return [
            Finding(
                severity="warning",
                file=str(up_file),
                rule="V46-ENUM-IRREVERSIBLE",
                message=(
                    "up.sql adds enum value(s) via `ALTER TYPE ... ADD VALUE` but down.sql neither performs "
                    "a rename-swap rollback (ALTER TABLE) nor declares `-- MANUAL ROLLBACK REQUIRED`. "
                    "PostgreSQL has no `ALTER TYPE ... DROP VALUE`; this migration is silently irreversible."
                ),
                fix=(
                    "Either:\n"
                    "  (a) Implement rename-swap in down.sql:\n"
                    "      ALTER TYPE foo RENAME TO foo_legacy;\n"
                    "      CREATE TYPE foo AS ENUM (...);  -- without the new values\n"
                    "      ALTER TABLE t ALTER COLUMN c TYPE foo USING (c::text::foo);\n"
                    "      DROP TYPE foo_legacy;\n"
                    "  (b) Or add the marker to down.sql so V46 stops complaining:\n"
                    "      -- MANUAL ROLLBACK REQUIRED: enum values cannot be dropped from finance_billing_cycle"
                ),
            )
        ]


# ── Standalone execution ──────────────────────────────────────────────────────


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
    validator = MigrationEnumRollbackValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
