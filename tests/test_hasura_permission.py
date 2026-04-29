"""Tests for V24 — hasura permission audit.

Covers:
  - V24-NO-PERMISSIONS    — table with no select/insert/update/delete
  - V24-WILDCARD-COLUMNS  — columns: '*' on any permission entry
  - V24-EMPTY-FILTER      — empty {} row filter on non-admin role
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.hasura_permission import HasuraPermissionAuditValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> HasuraPermissionAuditValidator:
    return HasuraPermissionAuditValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Project with hasura/metadata/databases/default/tables/ layout."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "hasura" / "metadata" / "databases" / "default" / "tables").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _table_dir(repo: Path) -> Path:
    return repo / "hasura" / "metadata" / "databases" / "default" / "tables"


# ── 1. No-Hasura short-circuit ────────────────────────────────────────


class TestNoHasura:
    def test_no_hasura_dir_returns_empty(self, validator, tmp_path):
        # Project without hasura/ → V24 no-ops
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert validator.validate_project(ctx) == []


# ── 2. V24-NO-PERMISSIONS ─────────────────────────────────────────────


class TestNoPermissions:
    def test_table_with_no_perms_warns(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            # no permissions blocks at all
            """,
        )
        findings = validator.validate_project(ctx)
        no_perms = [f for f in findings if f.rule == "V24-NO-PERMISSIONS"]
        assert len(no_perms) == 1
        assert "public.users" in no_perms[0].message

    def test_empty_perms_list_also_warns(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_orders.yaml",
            """
            table:
              schema: public
              name: orders
            select_permissions: []
            """,
        )
        findings = validator.validate_project(ctx)
        no_perms = [f for f in findings if f.rule == "V24-NO-PERMISSIONS"]
        assert len(no_perms) == 1


# ── 3. V24-WILDCARD-COLUMNS ───────────────────────────────────────────


class TestWildcardColumns:
    def test_string_wildcard_warns(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: anonymous
                permission:
                  columns: '*'
                  filter:
                    is_public: { _eq: true }
            """,
        )
        findings = validator.validate_project(ctx)
        wc = [f for f in findings if f.rule == "V24-WILDCARD-COLUMNS"]
        assert len(wc) == 1
        assert "anonymous" in wc[0].message

    def test_list_wildcard_warns(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: user
                permission:
                  columns: ['*']
                  filter:
                    id: { _eq: X-Hasura-User-Id }
            """,
        )
        findings = validator.validate_project(ctx)
        wc = [f for f in findings if f.rule == "V24-WILDCARD-COLUMNS"]
        assert len(wc) == 1

    def test_explicit_columns_pass(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: anonymous
                permission:
                  columns: ['id', 'display_name']
                  filter:
                    is_public: { _eq: true }
            """,
        )
        findings = validator.validate_project(ctx)
        wc = [f for f in findings if f.rule == "V24-WILDCARD-COLUMNS"]
        assert wc == []


# ── 4. V24-EMPTY-FILTER ───────────────────────────────────────────────


class TestEmptyFilter:
    def test_empty_filter_on_non_admin_role_errors(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: anonymous
                permission:
                  columns: ['id']
                  filter: {}
            """,
        )
        findings = validator.validate_project(ctx)
        ef = [f for f in findings if f.rule == "V24-EMPTY-FILTER"]
        assert len(ef) == 1
        assert "anonymous" in ef[0].message

    def test_empty_filter_on_admin_exempt(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: admin
                permission:
                  columns: ['id']
                  filter: {}
            """,
        )
        findings = validator.validate_project(ctx)
        ef = [f for f in findings if f.rule == "V24-EMPTY-FILTER"]
        assert ef == []

    def test_non_empty_filter_passes(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: user
                permission:
                  columns: ['id']
                  filter:
                    id: { _eq: X-Hasura-User-Id }
            """,
        )
        findings = validator.validate_project(ctx)
        ef = [f for f in findings if f.rule == "V24-EMPTY-FILTER"]
        assert ef == []

    def test_insert_perm_no_empty_filter_check(self, validator, repo, ctx):
        # insert_permissions has no `filter:` field; V24 must not require one
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            insert_permissions:
              - role: user
                permission:
                  columns: ['id', 'display_name']
                  check:
                    id: { _eq: X-Hasura-User-Id }
            """,
        )
        findings = validator.validate_project(ctx)
        ef = [f for f in findings if f.rule == "V24-EMPTY-FILTER"]
        assert ef == []


# ── 5. Tier 2 — single-file scan via validate_file ───────────────────


class TestValidateFile:
    def test_validate_file_scans_only_that_yaml(self, validator, repo, ctx):
        _write(
            _table_dir(repo) / "public_users.yaml",
            """
            table:
              schema: public
              name: users
            select_permissions:
              - role: anonymous
                permission:
                  columns: '*'
                  filter:
                    is_public: { _eq: true }
            """,
        )
        # Other table file with a different problem — should NOT appear
        _write(
            _table_dir(repo) / "public_orders.yaml",
            """
            table:
              schema: public
              name: orders
            """,
        )
        findings = validator.validate_file(ctx, str(_table_dir(repo) / "public_users.yaml"))
        # Only the users.yaml's wildcard
        assert len(findings) == 1
        assert findings[0].rule == "V24-WILDCARD-COLUMNS"

    def test_validate_file_outside_tables_returns_empty(self, validator, repo, ctx):
        # actions.yaml lives elsewhere in metadata; V24 only handles tables/
        actions_path = repo / "hasura" / "metadata" / "actions.yaml"
        actions_path.parent.mkdir(parents=True, exist_ok=True)
        actions_path.write_text("actions: []\n")
        findings = validator.validate_file(ctx, str(actions_path))
        assert findings == []
