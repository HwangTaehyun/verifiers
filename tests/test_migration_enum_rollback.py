"""Tests for V46: Migration Enum Rollback.

Covers:
  - up.sql with non-enum ALTER TABLE — no findings
  - up.sql adds enum, down.sql has rename-swap (ALTER TABLE) — no findings
  - up.sql adds enum, down.sql empty/comment-only — V46-ENUM-IRREVERSIBLE
  - down.sql with -- MANUAL ROLLBACK REQUIRED marker — no findings
  - Manual marker is matched case-insensitively
  - up.sql without paired down.sql — V46-ENUM-IRREVERSIBLE
  - No migrations directory — no findings
  - validate_file (Tier 2) triggers the same full-scan logic
  - Multiple independent migrations each checked separately
  - ALTER TABLE in a comment in down.sql does NOT satisfy the rollback check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.migration_enum_rollback import MigrationEnumRollbackValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> MigrationEnumRollbackValidator:
    return MigrationEnumRollbackValidator()


def make_migration(
    tmp_project: Path,
    ts: str,
    name: str,
    up_sql: str,
    down_sql: str | None = "",
) -> tuple[Path, Path | None]:
    """Create a migration directory with up.sql (and optionally down.sql) under the default Hasura path.

    Pass down_sql=None to simulate a missing down.sql file.
    Returns (up_path, down_path_or_None).
    """
    mdir = tmp_project / "server" / "hasura" / "migrations" / "default" / f"{ts}_{name}"
    mdir.mkdir(parents=True, exist_ok=True)
    up = mdir / "up.sql"
    up.write_text(up_sql)
    if down_sql is None:
        return up, None
    down = mdir / "down.sql"
    down.write_text(down_sql)
    return up, down


# ---------------------------------------------------------------------------
# 1. No ALTER TYPE ADD VALUE — no findings
# ---------------------------------------------------------------------------


class TestNoAlterTypeNoFindings:
    def test_no_alter_type_no_findings(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """up.sql with ALTER TABLE ADD COLUMN (not an enum change) should not trigger V46."""
        make_migration(
            tmp_project,
            "1700000001000",
            "add_column",
            up_sql="ALTER TABLE payment_records ADD COLUMN notes TEXT;",
            down_sql="ALTER TABLE payment_records DROP COLUMN notes;",
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert v46 == [], f"Expected no V46 findings, got: {v46}"


# ---------------------------------------------------------------------------
# 2. ALTER TYPE ADD VALUE with rename-swap rollback — no findings
# ---------------------------------------------------------------------------


class TestAlterTypeWithRollbackPasses:
    def test_alter_type_with_rollback_passes(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """up.sql adds enum value; down.sql contains ALTER TYPE RENAME TO + ALTER TABLE swap."""
        make_migration(
            tmp_project,
            "1700000001000",
            "add_enum_value",
            up_sql=(
                "ALTER TYPE finance_billing_cycle ADD VALUE 'ONE_TIME';\n"
                "ALTER TYPE finance_billing_cycle ADD VALUE 'INSTALLMENT';\n"
            ),
            down_sql=(
                "ALTER TYPE finance_billing_cycle RENAME TO finance_billing_cycle_legacy;\n"
                "CREATE TYPE finance_billing_cycle AS ENUM ('MONTHLY', 'YEARLY');\n"
                "ALTER TABLE payment_records ALTER COLUMN billing_cycle TYPE finance_billing_cycle "
                "USING billing_cycle::text::finance_billing_cycle;\n"
                "DROP TYPE finance_billing_cycle_legacy;\n"
            ),
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert v46 == [], f"Expected no V46 findings (rename-swap present), got: {v46}"


# ---------------------------------------------------------------------------
# 3. ALTER TYPE ADD VALUE, down.sql empty/comment-only — V46
# ---------------------------------------------------------------------------


class TestAlterTypeNoRollbackWarns:
    def test_alter_type_no_rollback_warns(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """up.sql adds enum value; down.sql has only a comment — should emit V46."""
        make_migration(
            tmp_project,
            "1700000001000",
            "irreversible_enum",
            up_sql="ALTER TYPE finance_billing_cycle ADD VALUE 'ONE_TIME';\n",
            down_sql="-- Postgres does not support ALTER TYPE ... DROP VALUE\n-- This migration is irreversible.\n",
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert len(v46) == 1, f"Expected 1 V46 finding, got: {v46}"
        assert "ALTER TYPE ... ADD VALUE" in v46[0].message


# ---------------------------------------------------------------------------
# 4. Manual marker in down.sql satisfies V46
# ---------------------------------------------------------------------------


class TestMarkerSatisfies:
    def test_marker_satisfies(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """down.sql with -- MANUAL ROLLBACK REQUIRED should silence V46."""
        make_migration(
            tmp_project,
            "1700000001000",
            "manual_marker",
            up_sql="ALTER TYPE finance_billing_cycle ADD VALUE 'CUSTOM';\n",
            down_sql=(
                "-- MANUAL ROLLBACK REQUIRED: enum values cannot be dropped from finance_billing_cycle\n"
                "-- To revert: run migrations before this one.\n"
            ),
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert v46 == [], f"Expected no V46 (manual marker present), got: {v46}"


# ---------------------------------------------------------------------------
# 5. Manual marker is matched case-insensitively
# ---------------------------------------------------------------------------


class TestMarkerCaseInsensitive:
    def test_marker_case_insensitive(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """-- manual rollback required (lowercase) should also satisfy V46."""
        make_migration(
            tmp_project,
            "1700000001000",
            "lowercase_marker",
            up_sql="ALTER TYPE finance_billing_cycle ADD VALUE 'BIANNUAL';\n",
            down_sql="-- manual rollback required: handled manually\n",
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert v46 == [], f"Expected no V46 (lowercase marker accepted), got: {v46}"


# ---------------------------------------------------------------------------
# 6. Missing down.sql — V46
# ---------------------------------------------------------------------------


class TestMissingDownSqlWarns:
    def test_missing_down_sql_warns(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """up.sql with ALTER TYPE ADD VALUE but no down.sql should emit V46."""
        make_migration(
            tmp_project,
            "1700000001000",
            "no_down_sql",
            up_sql="ALTER TYPE finance_billing_cycle ADD VALUE 'ONE_TIME';\n",
            down_sql=None,  # missing
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert len(v46) == 1, f"Expected 1 V46 finding for missing down.sql, got: {v46}"


# ---------------------------------------------------------------------------
# 7. No migrations directory — no findings
# ---------------------------------------------------------------------------


class TestNoMigrationsDirNoFindings:
    def test_no_migrations_dir_no_findings(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """Empty project with no up.sql files should return no findings."""
        # tmp_project has server/hasura/migrations/testproject/ but no up.sql files
        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert v46 == []


# ---------------------------------------------------------------------------
# 8. validate_file (Tier 2) triggers the same full-scan logic
# ---------------------------------------------------------------------------


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """Tier 2 path (validate_file) must run the same project-wide logic."""
        up_file, _ = make_migration(
            tmp_project,
            "1700000001000",
            "tier2_test",
            up_sql="ALTER TYPE billing_cycle ADD VALUE 'WEEKLY';\n",
            down_sql="-- no rollback logic\n",
        )

        findings = validator.validate_file(project_ctx, str(up_file))
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert len(v46) == 1, f"Expected 1 V46 from validate_file, got: {v46}"


# ---------------------------------------------------------------------------
# 9. Multiple independent migrations — each checked separately
# ---------------------------------------------------------------------------


class TestMultipleMigrationsIndependent:
    def test_multiple_migrations_independent(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """Migration A has rollback, Migration B does not — only B should be flagged."""
        # Migration A: has proper rename-swap
        make_migration(
            tmp_project,
            "1700000001000",
            "enum_with_rollback",
            up_sql="ALTER TYPE status_type ADD VALUE 'PENDING';\n",
            down_sql=(
                "ALTER TYPE status_type RENAME TO status_type_v1;\n"
                "CREATE TYPE status_type AS ENUM ('ACTIVE', 'INACTIVE');\n"
                "ALTER TABLE orders ALTER COLUMN status TYPE status_type "
                "USING status::text::status_type;\n"
                "DROP TYPE status_type_v1;\n"
            ),
        )
        # Migration B: no rollback
        make_migration(
            tmp_project,
            "1700000002000",
            "enum_no_rollback",
            up_sql="ALTER TYPE payment_status ADD VALUE 'FAILED';\n",
            down_sql="-- this migration cannot be reversed\n",
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert len(v46) == 1, f"Expected exactly 1 V46 (only migration B), got: {v46}"
        assert "1700000002000" in v46[0].file


# ---------------------------------------------------------------------------
# 10. ALTER TABLE inside a comment in down.sql should NOT satisfy rollback check
# ---------------------------------------------------------------------------


class TestAlterTypeInCommentSkipped:
    def test_alter_table_in_comment_not_counted(
        self,
        validator: MigrationEnumRollbackValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """A commented-out -- ALTER TABLE in down.sql must NOT satisfy the rollback indicator.

        The rename-swap check looks for ALTER TABLE at the start of a line (not inside
        a comment). This test ensures a line like:
            -- ALTER TABLE foo ALTER COLUMN bar TYPE old_type ...
        does not trick V46 into thinking a rename-swap rollback exists.
        """
        make_migration(
            tmp_project,
            "1700000001000",
            "commented_alter_table",
            up_sql="ALTER TYPE billing_type ADD VALUE 'QUARTERLY';\n",
            down_sql=(
                "-- To reverse this migration manually:\n"
                "-- ALTER TABLE billing_records ALTER COLUMN type TYPE billing_type_legacy;\n"
                "-- This is just documentation, not executable SQL.\n"
            ),
        )

        findings = validator.validate_project(project_ctx)
        v46 = [f for f in findings if f.rule == "V46-ENUM-IRREVERSIBLE"]
        assert len(v46) == 1, f"Commented-out ALTER TABLE should not satisfy rollback check; got: {v46}"
