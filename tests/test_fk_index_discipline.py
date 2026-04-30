"""Tests for V47: Foreign Key Index Discipline.

Covers:
  - FK with matching index → no findings
  - FK without any index → V47-FK-NO-INDEX
  - ALTER TABLE ADD CONSTRAINT FOREIGN KEY form detected
  - Composite PRIMARY KEY leftmost column counts as index
  - Index defined in earlier migration covers FK in later migration
  - No migrations directory → no findings
  - Multiple FKs, only the unindexed one flagged
  - validate_file (Tier 2) triggers the same full-scan logic
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.fk_index_discipline import FkIndexDisciplineValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> FkIndexDisciplineValidator:
    return FkIndexDisciplineValidator()


def make_migration(tmp_project: Path, ts: str, name: str, sql: str) -> Path:
    """Create a migration directory with up.sql under the default Hasura path."""
    mdir = tmp_project / "server" / "hasura" / "migrations" / "default" / f"{ts}_{name}"
    mdir.mkdir(parents=True, exist_ok=True)
    up = mdir / "up.sql"
    up.write_text(sql)
    return up


# ---------------------------------------------------------------------------
# 1. FK with index — should pass
# ---------------------------------------------------------------------------


class TestFkWithIndexPasses:
    def test_fk_with_index_passes(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        make_migration(
            tmp_project,
            "1700000001000",
            "create_parent",
            """
CREATE TABLE finance_opportunities (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL
);
""",
        )
        make_migration(
            tmp_project,
            "1700000002000",
            "create_child",
            """
CREATE TABLE payment_records (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    amount DECIMAL(10,2)
);

CREATE INDEX IF NOT EXISTS idx_payment_records_opportunity_id
    ON payment_records(opportunity_id);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert errors == [], f"Expected no V47-FK-NO-INDEX findings, got: {errors}"


# ---------------------------------------------------------------------------
# 2. FK without index — should error
# ---------------------------------------------------------------------------


class TestFkWithoutIndexErrors:
    def test_fk_without_index_errors(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        make_migration(
            tmp_project,
            "1700000001000",
            "create_tables",
            """
CREATE TABLE contracts (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    amount DECIMAL(10,2)
);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert len(errors) == 1
        assert "contracts.opportunity_id" in errors[0].message
        assert "finance_opportunities" in errors[0].message


# ---------------------------------------------------------------------------
# 3. ALTER TABLE ADD CONSTRAINT FOREIGN KEY form
# ---------------------------------------------------------------------------


class TestAlterTableFkFormDetected:
    def test_alter_table_fk_form_detected(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        make_migration(
            tmp_project,
            "1700000001000",
            "add_fk_no_index",
            """
ALTER TABLE amendments
    ADD CONSTRAINT fk_amendments_opportunity
    FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert len(errors) == 1
        assert "amendments.opportunity_id" in errors[0].message

    def test_alter_table_fk_with_index_passes(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        make_migration(
            tmp_project,
            "1700000001000",
            "add_fk_with_index",
            """
ALTER TABLE invoices
    ADD CONSTRAINT fk_invoices_opportunity
    FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id);

CREATE INDEX IF NOT EXISTS idx_invoices_opportunity_id ON invoices(opportunity_id);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert errors == []


# ---------------------------------------------------------------------------
# 4. Composite PK leftmost column covers FK
# ---------------------------------------------------------------------------


class TestCompositePkCoversFk:
    def test_composite_pk_covers_fk(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """PRIMARY KEY (fk_col, other) — leftmost col counts as an implicit index."""
        make_migration(
            tmp_project,
            "1700000001000",
            "composite_pk",
            """
CREATE TABLE order_items (
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    line_no INT NOT NULL,
    qty INT,
    PRIMARY KEY (opportunity_id, line_no)
);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert errors == [], f"Composite PK leftmost col should count as index, got: {errors}"


# ---------------------------------------------------------------------------
# 5. Index in earlier migration covers FK in later migration
# ---------------------------------------------------------------------------


class TestIndexInEarlierMigration:
    def test_index_in_earlier_migration(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Index defined in migration 001, FK added in migration 002 — should pass."""
        make_migration(
            tmp_project,
            "1700000001000",
            "create_index_first",
            """
CREATE INDEX IF NOT EXISTS idx_contracts_opportunity_id ON contracts(opportunity_id);
""",
        )
        make_migration(
            tmp_project,
            "1700000002000",
            "add_fk_later",
            """
ALTER TABLE contracts
    ADD CONSTRAINT fk_contracts_opportunity
    FOREIGN KEY (opportunity_id) REFERENCES finance_opportunities(id);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert errors == [], f"Index in earlier migration should cover FK, got: {errors}"


# ---------------------------------------------------------------------------
# 6. No migrations directory → no findings
# ---------------------------------------------------------------------------


class TestNoMigrationsDirNoFindings:
    def test_no_migrations_dir_no_findings(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Empty project with no migrations directory should produce no findings."""
        # tmp_project has no server/hasura/migrations by default
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert errors == []


# ---------------------------------------------------------------------------
# 7. Multiple FKs, only unindexed one is flagged
# ---------------------------------------------------------------------------


class TestMultipleFksOneIndexed:
    def test_multiple_fks_one_indexed(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Two FK columns in same table — only the one without an index is flagged."""
        make_migration(
            tmp_project,
            "1700000001000",
            "two_fks",
            """
CREATE TABLE invoice_items (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id),
    contract_id BIGINT NOT NULL REFERENCES contracts(id),
    amount DECIMAL(10,2)
);

-- Only index opportunity_id, not contract_id
CREATE INDEX idx_invoice_items_opportunity_id ON invoice_items(opportunity_id);
""",
        )

        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert len(errors) == 1
        assert "contract_id" in errors[0].message
        # opportunity_id should NOT appear in errors
        assert not any("opportunity_id" in e.message for e in errors)


# ---------------------------------------------------------------------------
# 8. validate_file (Tier 2) triggers the same full-scan logic
# ---------------------------------------------------------------------------


class TestValidateFileTriggersFullScan:
    def test_validate_file_triggers_full_scan(
        self, validator: FkIndexDisciplineValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Tier 2 path (validate_file) must run the same cross-file logic as validate_project."""
        up_file = make_migration(
            tmp_project,
            "1700000001000",
            "fk_no_index",
            """
CREATE TABLE amendments_detail (
    id BIGSERIAL PRIMARY KEY,
    opportunity_id BIGINT NOT NULL REFERENCES finance_opportunities(id)
);
""",
        )

        findings = validator.validate_file(project_ctx, str(up_file))
        errors = [f for f in findings if f.rule == "V47-FK-NO-INDEX"]
        assert len(errors) == 1
        assert "amendments_detail.opportunity_id" in errors[0].message
