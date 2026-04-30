"""Tests for V48: Hasura Permission Rationale Validator.

Covers:
  - select-only table with repo-level doc marker passes
  - select-only table with per-YAML comment passes
  - select-only table with no doc marker warns (V48-HASURA-SELECT-ONLY-UNDOCUMENTED)
  - table with insert_permissions is not select-only → no finding
  - table with no permissions at all → exempt, no finding
  - empty project (no hasura dir) → no findings
  - AGENTS.md marker satisfies repo-level check
  - CLAUDE.md marker satisfies repo-level check
  - multiple tables scanned independently; one repo-level marker covers all
  - validate_file (Tier 2) path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.hasura_permission_rationale import HasuraPermissionRationaleValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> HasuraPermissionRationaleValidator:
    return HasuraPermissionRationaleValidator()


@pytest.fixture
def tables_dir(tmp_project: Path) -> Path:
    """Return the testproject metadata tables directory."""
    return tmp_project / "server" / "hasura" / "metadata" / "databases" / "testproject" / "tables"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SELECT_ONLY_YAML = """\
table:
  schema: public
  name: payments
select_permissions:
  - role: finance_admin
    permission:
      columns: "*"
      filter: {}
"""

_SELECT_PLUS_INSERT_YAML = """\
table:
  schema: public
  name: payments
select_permissions:
  - role: finance_admin
    permission:
      columns: "*"
      filter: {}
insert_permissions:
  - role: finance_admin
    permission:
      check: {}
"""

_NO_PERMISSIONS_YAML = """\
table:
  schema: public
  name: internal_only
"""


# ---------------------------------------------------------------------------
# 1. Select-only + repo-level doc marker passes
# ---------------------------------------------------------------------------


def test_select_only_with_doc_marker_passes(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """docs/architecture.md contains 'hasura-read-only' → no finding."""
    docs = tmp_project / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text(
        "## Write Path\n\nAll mutations go through gRPC. hasura-read-only applies to all tables.\n"
    )

    (tables_dir / "public_payments.yaml").write_text(_SELECT_ONLY_YAML)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 2. Select-only + per-YAML comment passes
# ---------------------------------------------------------------------------


def test_select_only_with_yaml_comment_passes(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """YAML file contains '# mutations: intentionally absent' → no finding."""
    yaml_with_comment = _SELECT_ONLY_YAML + "# mutations: intentionally absent — writes via gRPC\n"
    (tables_dir / "public_payments.yaml").write_text(yaml_with_comment)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 3. Select-only with no doc marker warns
# ---------------------------------------------------------------------------


def test_select_only_no_doc_warns(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """Select-only table with no marker → V48-HASURA-SELECT-ONLY-UNDOCUMENTED (info)."""
    (tables_dir / "public_payments.yaml").write_text(_SELECT_ONLY_YAML)

    result = validator.run(project_ctx, mode="stop")

    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.rule == "V48-HASURA-SELECT-ONLY-UNDOCUMENTED"
    assert f.severity == "info"
    assert "select_permissions" in f.message


# ---------------------------------------------------------------------------
# 4. Table with insert_permissions is not select-only → no finding
# ---------------------------------------------------------------------------


def test_table_with_insert_permissions_no_finding(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """Table with both select and insert permissions → not select-only, no finding."""
    (tables_dir / "public_payments.yaml").write_text(_SELECT_PLUS_INSERT_YAML)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 5. Table with no permissions at all → exempt, no finding
# ---------------------------------------------------------------------------


def test_table_with_no_permissions_at_all_no_finding(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """Table with no permission keys at all → private/schema-only, exempt."""
    (tables_dir / "public_internal.yaml").write_text(_NO_PERMISSIONS_YAML)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 6. Empty project (no hasura dir) → no findings
# ---------------------------------------------------------------------------


def test_no_hasura_metadata_no_findings(
    validator: HasuraPermissionRationaleValidator,
    tmp_path: Path,
) -> None:
    """Project with no hasura directory returns empty findings."""
    (tmp_path / ".git").mkdir()
    ctx = ProjectContext(tmp_path)

    result = validator.run(ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 7. AGENTS.md marker satisfies repo-level check
# ---------------------------------------------------------------------------


def test_agents_md_marker_satisfies(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """AGENTS.md at project root containing 'hasura-read-only' suppresses finding."""
    (tmp_project / "AGENTS.md").write_text(
        "# Architecture\n\nAll Hasura tables are hasura-read-only.\nMutations go through gRPC.\n"
    )
    (tables_dir / "public_payments.yaml").write_text(_SELECT_ONLY_YAML)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 8. CLAUDE.md marker satisfies repo-level check
# ---------------------------------------------------------------------------


def test_claude_md_marker_satisfies(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """CLAUDE.md at project root containing 'mutations-via-grpc' suppresses finding."""
    (tmp_project / "CLAUDE.md").write_text("# Project\n\nWrite path: mutations-via-grpc only. Hasura is read-only.\n")
    (tables_dir / "public_payments.yaml").write_text(_SELECT_ONLY_YAML)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 9. Multiple tables — one repo marker covers all
# ---------------------------------------------------------------------------


def test_multiple_tables_independent(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """When multiple tables are select-only, each is scanned independently.
    Without a marker, each produces a finding. With a single repo marker, all pass."""
    (tables_dir / "public_payments.yaml").write_text(_SELECT_ONLY_YAML)
    (tables_dir / "public_orders.yaml").write_text(_SELECT_ONLY_YAML.replace("name: payments", "name: orders"))
    (tables_dir / "public_users.yaml").write_text(_SELECT_ONLY_YAML.replace("name: payments", "name: users"))

    # Without marker: three findings
    result_no_marker = validator.run(project_ctx, mode="stop")
    assert len(result_no_marker.findings) == 3
    assert all(f.rule == "V48-HASURA-SELECT-ONLY-UNDOCUMENTED" for f in result_no_marker.findings)

    # Add repo-level marker: all pass
    (tmp_project / "AGENTS.md").write_text("hasura-read-only architectural decision.\n")
    result_with_marker = validator.run(project_ctx, mode="stop")
    assert result_with_marker.findings == []


# ---------------------------------------------------------------------------
# 10. validate_file (Tier 2) path
# ---------------------------------------------------------------------------


def test_validate_file_single_table(
    validator: HasuraPermissionRationaleValidator,
    tables_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """validate_file (Tier 2) detects undocumented select-only on the given file."""
    table_file = tables_dir / "public_payments.yaml"
    table_file.write_text(_SELECT_ONLY_YAML)

    result = validator.run(project_ctx, file_path=str(table_file), mode="post_tool_use")

    assert len(result.findings) == 1
    assert result.findings[0].rule == "V48-HASURA-SELECT-ONLY-UNDOCUMENTED"
