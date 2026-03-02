"""Tests for V04: Hasura Migration Validator.

Covers:
  - _check_timestamp_ordering (ascending order, out-of-order detection)
  - _check_duplicate_timestamps (same timestamp across directories)
  - _check_up_down_pairing (missing up.sql or down.sql)
  - _check_dangerous_ddl (DROP TABLE without IF EXISTS, TRUNCATE, safe DDL)
  - _check_metadata_consistency (orphan metadata tables)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.hasura_migration import HasuraMigrationValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> HasuraMigrationValidator:
    return HasuraMigrationValidator()


@pytest.fixture
def migration_dir(tmp_project: Path) -> Path:
    """Return the testproject migration directory."""
    return tmp_project / "server" / "hasura" / "migrations" / "testproject"


@pytest.fixture
def tables_dir(tmp_project: Path) -> Path:
    """Return the testproject metadata tables directory."""
    return tmp_project / "server" / "hasura" / "metadata" / "databases" / "testproject" / "tables"


# ---------------------------------------------------------------------------
# 1. _check_timestamp_ordering
# ---------------------------------------------------------------------------


class TestCheckTimestampOrdering:
    """Tests for _check_timestamp_ordering."""

    def test_out_of_order_timestamps_produces_finding(
        self, validator: HasuraMigrationValidator, migration_dir: Path
    ) -> None:
        """Migration dirs whose timestamps are NOT ascending (after name sort)
        should emit V04-TIMESTAMP-ORDER.

        The validator iterates directories via sorted(), which sorts
        lexicographically. A 14-digit timestamp like '99999999999999' sorts
        before a 12-digit one like '999999999999' (because '9' < '_' in
        ASCII). Since the longer timestamp string is lexicographically
        greater, the second directory's timestamp is <= the first one,
        triggering the ordering check.
        """
        # 14-digit ts sorts first; 12-digit ts sorts second but is <= the first.
        (migration_dir / "99999999999999_second").mkdir()
        (migration_dir / "999999999999_first").mkdir()
        for d in migration_dir.iterdir():
            if d.is_dir():
                (d / "up.sql").write_text("")
                (d / "down.sql").write_text("")

        findings = validator._check_timestamp_ordering(migration_dir)

        assert len(findings) == 1
        assert findings[0].rule == "V04-TIMESTAMP-ORDER"
        assert "999999999999" in findings[0].message

    def test_correct_ascending_order_no_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """Ascending timestamps should produce zero findings."""
        (migration_dir / "170000000001_create_users").mkdir()
        (migration_dir / "170000000002_create_posts").mkdir()
        (migration_dir / "170000000003_add_email").mkdir()

        findings = validator._check_timestamp_ordering(migration_dir)

        assert findings == []


# ---------------------------------------------------------------------------
# 2. _check_duplicate_timestamps
# ---------------------------------------------------------------------------


class TestCheckDuplicateTimestamps:
    """Tests for _check_duplicate_timestamps."""

    def test_duplicate_timestamp_produces_finding(
        self, validator: HasuraMigrationValidator, migration_dir: Path
    ) -> None:
        """Two directories sharing the same timestamp prefix should emit
        V04-DUPLICATE-TIMESTAMP."""
        (migration_dir / "170000000001_create_users").mkdir()
        (migration_dir / "170000000001_create_posts").mkdir()

        findings = validator._check_duplicate_timestamps(migration_dir)

        assert len(findings) == 1
        assert findings[0].rule == "V04-DUPLICATE-TIMESTAMP"
        assert "170000000001" in findings[0].message

    def test_unique_timestamps_no_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """Unique timestamps should produce zero findings."""
        (migration_dir / "170000000001_create_users").mkdir()
        (migration_dir / "170000000002_create_posts").mkdir()

        findings = validator._check_duplicate_timestamps(migration_dir)

        assert findings == []


# ---------------------------------------------------------------------------
# 3. _check_up_down_pairing
# ---------------------------------------------------------------------------


class TestCheckUpDownPairing:
    """Tests for _check_up_down_pairing."""

    def test_missing_up_sql_produces_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """A migration directory with only down.sql should emit
        V04-MISSING-FILE for up.sql."""
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        (d / "down.sql").write_text("DROP TABLE IF EXISTS users;")

        findings = validator._check_up_down_pairing(migration_dir)

        assert len(findings) == 1
        assert findings[0].rule == "V04-MISSING-FILE"
        assert "up.sql" in findings[0].message

    def test_missing_down_sql_produces_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """A migration directory with only up.sql should emit
        V04-MISSING-FILE for down.sql."""
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        (d / "up.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")

        findings = validator._check_up_down_pairing(migration_dir)

        assert len(findings) == 1
        assert findings[0].rule == "V04-MISSING-FILE"
        assert "down.sql" in findings[0].message

    def test_both_files_present_no_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """A migration with both up.sql and down.sql should produce zero
        findings."""
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        (d / "up.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")
        (d / "down.sql").write_text("DROP TABLE IF EXISTS users;")

        findings = validator._check_up_down_pairing(migration_dir)

        assert findings == []


# ---------------------------------------------------------------------------
# 4. _check_dangerous_ddl
# ---------------------------------------------------------------------------


class TestCheckDangerousDdl:
    """Tests for _check_dangerous_ddl."""

    def test_drop_table_without_if_exists_produces_finding(
        self, validator: HasuraMigrationValidator, migration_dir: Path
    ) -> None:
        """DROP TABLE without IF EXISTS in up.sql should emit
        V04-DANGEROUS-DDL."""
        d = migration_dir / "1700000001_drop_users"
        d.mkdir()
        up_sql = d / "up.sql"
        up_sql.write_text("DROP TABLE users;")

        findings = validator._check_dangerous_ddl(str(up_sql))

        assert len(findings) == 1
        assert findings[0].rule == "V04-DANGEROUS-DDL"
        assert "DROP TABLE without IF EXISTS" in findings[0].message

    def test_truncate_produces_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """TRUNCATE in up.sql should emit V04-DANGEROUS-DDL."""
        d = migration_dir / "1700000001_truncate_logs"
        d.mkdir()
        up_sql = d / "up.sql"
        up_sql.write_text("TRUNCATE logs;")

        findings = validator._check_dangerous_ddl(str(up_sql))

        assert len(findings) == 1
        assert findings[0].rule == "V04-DANGEROUS-DDL"
        assert "TRUNCATE" in findings[0].message

    def test_safe_ddl_no_finding(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """Safe DDL (CREATE TABLE, ALTER TABLE ADD COLUMN, DROP TABLE IF
        EXISTS) should produce zero findings."""
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        up_sql = d / "up.sql"
        up_sql.write_text(
            "CREATE TABLE IF NOT EXISTS users (id serial PRIMARY KEY);\n"
            "ALTER TABLE users ADD COLUMN email text;\n"
            "DROP TABLE IF EXISTS old_temp;\n"
        )

        findings = validator._check_dangerous_ddl(str(up_sql))

        assert findings == []

    def test_drop_table_if_exists_is_safe(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """DROP TABLE IF EXISTS should NOT trigger V04-DANGEROUS-DDL."""
        d = migration_dir / "1700000001_cleanup"
        d.mkdir()
        up_sql = d / "up.sql"
        up_sql.write_text("DROP TABLE IF EXISTS temp_data;")

        findings = validator._check_dangerous_ddl(str(up_sql))

        assert findings == []

    def test_non_up_sql_file_returns_empty(self, validator: HasuraMigrationValidator, migration_dir: Path) -> None:
        """A file that is not named up.sql should produce zero findings."""
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        down_sql = d / "down.sql"
        down_sql.write_text("DROP TABLE users;")

        findings = validator._check_dangerous_ddl(str(down_sql))

        assert findings == []


# ---------------------------------------------------------------------------
# 5. _check_metadata_consistency
# ---------------------------------------------------------------------------


class TestCheckMetadataConsistency:
    """Tests for _check_metadata_consistency."""

    def test_orphan_metadata_table_produces_finding(
        self,
        validator: HasuraMigrationValidator,
        migration_dir: Path,
        tables_dir: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """A table tracked in metadata but without a CREATE TABLE in any
        migration should emit V04-METADATA-ORPHAN."""
        # Metadata references a table called "orders"
        (tables_dir / "public_orders.yaml").write_text("table:\n  name: orders\n  schema: public\n")

        # Migrations only create "users"
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        (d / "up.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")
        (d / "down.sql").write_text("DROP TABLE IF EXISTS users;")

        findings = validator._check_metadata_consistency(project_ctx, migration_dir)

        assert len(findings) == 1
        assert findings[0].rule == "V04-METADATA-ORPHAN"
        assert "orders" in findings[0].message

    def test_metadata_table_with_matching_migration_no_finding(
        self,
        validator: HasuraMigrationValidator,
        migration_dir: Path,
        tables_dir: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """A table tracked in metadata that also has a CREATE TABLE in
        migrations should produce zero findings."""
        # Metadata references "users"
        (tables_dir / "public_users.yaml").write_text("table:\n  name: users\n  schema: public\n")

        # Migration creates "users"
        d = migration_dir / "1700000001_create_users"
        d.mkdir()
        (d / "up.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")
        (d / "down.sql").write_text("DROP TABLE IF EXISTS users;")

        findings = validator._check_metadata_consistency(project_ctx, migration_dir)

        assert findings == []

    def test_metadata_table_with_public_prefix_in_sql(
        self,
        validator: HasuraMigrationValidator,
        migration_dir: Path,
        tables_dir: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """CREATE TABLE public.orders should still match metadata for
        'orders'."""
        (tables_dir / "public_orders.yaml").write_text("table:\n  name: orders\n  schema: public\n")

        d = migration_dir / "1700000001_create_orders"
        d.mkdir()
        (d / "up.sql").write_text("CREATE TABLE public.orders (id serial PRIMARY KEY);")
        (d / "down.sql").write_text("DROP TABLE IF EXISTS public.orders;")

        findings = validator._check_metadata_consistency(project_ctx, migration_dir)

        assert findings == []


# ---------------------------------------------------------------------------
# 6. Integration: full validate() entry point
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """End-to-end tests through the public validate() method."""

    def test_validate_post_tool_use_with_dangerous_up_sql(
        self,
        validator: HasuraMigrationValidator,
        migration_dir: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """In post_tool_use mode, validate() should check DDL for the given
        file_path when it is an up.sql."""
        d = migration_dir / "1700000001_drop_users"
        d.mkdir()
        up_sql = d / "up.sql"
        up_sql.write_text("DROP TABLE users;")
        (d / "down.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")

        result = validator.validate(project_ctx, file_path=str(up_sql), mode="post_tool_use")

        rules = [f.rule for f in result.findings]
        assert "V04-DANGEROUS-DDL" in rules

    def test_validate_stop_mode_runs_full_scan(
        self,
        validator: HasuraMigrationValidator,
        migration_dir: Path,
        tables_dir: Path,
        project_ctx: ProjectContext,
    ) -> None:
        """In stop mode, validate() should run DDL scan on ALL up.sql files
        and also check metadata consistency."""
        # Create a migration with dangerous DDL
        d = migration_dir / "1700000001_drop_users"
        d.mkdir()
        (d / "up.sql").write_text("DROP TABLE users;")
        (d / "down.sql").write_text("CREATE TABLE users (id serial PRIMARY KEY);")

        # Create orphan metadata
        (tables_dir / "public_orders.yaml").write_text("table:\n  name: orders\n  schema: public\n")

        result = validator.validate(project_ctx, file_path=None, mode="stop")

        rules = [f.rule for f in result.findings]
        assert "V04-DANGEROUS-DDL" in rules
        assert "V04-METADATA-ORPHAN" in rules

    def test_validate_no_hasura_dir_returns_empty(self, validator: HasuraMigrationValidator, tmp_path: Path) -> None:
        """When hasura_dir does not exist, validate() should return an empty
        result."""
        # Build a minimal project with no hasura directory
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)

        result = validator.validate(ctx, file_path=None, mode="stop")

        assert result.findings == []
