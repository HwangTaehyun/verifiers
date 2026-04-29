"""Tests for hooks/validators/hasura_graphql_enforcement.py — V20.

Covers:
  - V20-SQL-IMPORT       database/sql import detection
  - V20-RAW-SQL-FORBIDDEN per-line raw SQL pattern detection
  - V20-MISSING-GRAPHQL  Service struct missing gqlClient warning
  - Hasura detection (hasura/ dir, hasura/server, compose with hasura/graphql-engine)
  - Exempt paths (migrations, *_test.go, mocks/, setup/, testdata/)
  - Mode dispatch (post_tool_use vs stop)
  - Early-exit when Hasura is not present (cost protection for non-Hasura repos)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.hasura_graphql_enforcement import (
    HasuraGraphQLEnforcementValidator,
    _is_exempt,
)
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> HasuraGraphQLEnforcementValidator:
    return HasuraGraphQLEnforcementValidator()


@pytest.fixture
def hasura_project(tmp_path: Path) -> Path:
    """Project layout where Hasura is detected via hasura/ directory."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "hasura").mkdir()
    return tmp_path


@pytest.fixture
def hasura_ctx(hasura_project: Path) -> ProjectContext:
    return ProjectContext(hasura_project)


@pytest.fixture
def non_hasura_project(tmp_path: Path) -> Path:
    """Project layout with no Hasura signals at all."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server").mkdir()
    return tmp_path


@pytest.fixture
def non_hasura_ctx(non_hasura_project: Path) -> ProjectContext:
    return ProjectContext(non_hasura_project)


# ---------------------------------------------------------------------------
# 1. _is_exempt — path classification
# ---------------------------------------------------------------------------


class TestIsExempt:
    def test_test_go_file_exempt(self) -> None:
        assert _is_exempt("server/internal/handler_test.go") is True

    def test_migration_sql_exempt(self) -> None:
        assert _is_exempt("hasura/migrations/000001_init/up.sql") is True

    def test_mocks_dir_exempt(self) -> None:
        assert _is_exempt("server/internal/mocks/foo.go") is True

    def test_setup_dir_exempt(self) -> None:
        assert _is_exempt("server/cmd/setup/seed.go") is True

    def test_testdata_dir_exempt(self) -> None:
        assert _is_exempt("server/internal/testdata/fixture.go") is True

    def test_regular_handler_not_exempt(self) -> None:
        assert _is_exempt("server/internal/handler.go") is False

    def test_test_in_filename_not_exempt(self) -> None:
        # 'test' substring in a regular filename should not exempt.
        assert _is_exempt("server/internal/test_helper.go") is False


# ---------------------------------------------------------------------------
# 2. Hasura detection
# ---------------------------------------------------------------------------


class TestDetectHasura:
    def test_hasura_dir_detected(
        self, validator: HasuraGraphQLEnforcementValidator, hasura_ctx: ProjectContext
    ) -> None:
        assert validator._detect_hasura(hasura_ctx) is True

    def test_no_hasura_returns_false(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        non_hasura_ctx: ProjectContext,
    ) -> None:
        assert validator._detect_hasura(non_hasura_ctx) is False

    def test_compose_with_hasura_image_detected(
        self, validator: HasuraGraphQLEnforcementValidator, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "docker-compose.yaml").write_text(
            "services:\n  graphql-engine:\n    image: hasura/graphql-engine:v2.0.0\n"
        )
        ctx = ProjectContext(tmp_path)
        assert validator._detect_hasura(ctx) is True

    def test_compose_without_hasura_returns_false(
        self, validator: HasuraGraphQLEnforcementValidator, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "docker-compose.yaml").write_text("services:\n  postgres:\n    image: postgres:15\n")
        ctx = ProjectContext(tmp_path)
        assert validator._detect_hasura(ctx) is False


# ---------------------------------------------------------------------------
# 3. Per-file raw SQL detection
# ---------------------------------------------------------------------------


class TestCheckGoFile:
    def test_database_sql_import_flagged(
        self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path
    ) -> None:
        go_file = hasura_project / "server" / "internal" / "repo.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text('package internal\n\nimport (\n\t"database/sql"\n)\n\nvar _ sql.DB\n')
        findings = validator._check_go_file(str(go_file))
        rules = [f.rule for f in findings]
        assert "V20-SQL-IMPORT" in rules
        sql_import_finding = next(f for f in findings if f.rule == "V20-SQL-IMPORT")
        assert sql_import_finding.severity == "error"
        assert sql_import_finding.line is not None and sql_import_finding.line > 0

    def test_query_context_flagged(self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path) -> None:
        go_file = hasura_project / "server" / "internal" / "repo.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text('package internal\n\nfunc Get(db *sql.DB) {\n\tdb.QueryContext(ctx, "SELECT 1")\n}\n')
        findings = validator._check_go_file(str(go_file))
        assert any(f.rule == "V20-RAW-SQL-FORBIDDEN" for f in findings)

    def test_select_from_string_flagged(
        self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path
    ) -> None:
        go_file = hasura_project / "server" / "internal" / "repo.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text("package internal\n\nvar q = `SELECT id FROM users WHERE id = $1`\n")
        findings = validator._check_go_file(str(go_file))
        assert any(f.rule == "V20-RAW-SQL-FORBIDDEN" for f in findings)

    def test_comment_lines_skipped(self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path) -> None:
        go_file = hasura_project / "server" / "internal" / "repo.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text("package internal\n\n// SELECT id FROM users — example in a comment\n")
        findings = validator._check_go_file(str(go_file))
        # Pure-comment SELECT must not flag.
        assert not any(f.rule == "V20-RAW-SQL-FORBIDDEN" for f in findings)

    def test_service_struct_without_gqlclient_warns(
        self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path
    ) -> None:
        go_file = hasura_project / "server" / "internal" / "service.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text("package internal\n\ntype UserService struct {\n\tdb *sql.DB\n}\n")
        findings = validator._check_go_file(str(go_file))
        assert any(f.rule == "V20-MISSING-GRAPHQL" for f in findings)
        warning = next(f for f in findings if f.rule == "V20-MISSING-GRAPHQL")
        assert warning.severity == "warning"

    def test_service_struct_with_gqlclient_no_warning(
        self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path
    ) -> None:
        go_file = hasura_project / "server" / "internal" / "service.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text("package internal\n\ntype UserService struct {\n\tgqlClient graphql.Client\n}\n")
        findings = validator._check_go_file(str(go_file))
        assert not any(f.rule == "V20-MISSING-GRAPHQL" for f in findings)

    def test_clean_file_no_findings(self, validator: HasuraGraphQLEnforcementValidator, hasura_project: Path) -> None:
        go_file = hasura_project / "server" / "internal" / "clean.go"
        go_file.parent.mkdir(parents=True)
        go_file.write_text('package internal\n\nfunc Hello() string {\n\treturn "world"\n}\n')
        findings = validator._check_go_file(str(go_file))
        assert findings == []


# ---------------------------------------------------------------------------
# 4. validate — mode dispatch + Hasura gating
# ---------------------------------------------------------------------------


class TestValidate:
    # _DATABASE_SQL_IMPORT requires the Go import-block form
    # ('\t"database/sql"' on its own line). Use that form below.
    _BAD_GO = 'package internal\n\nimport (\n\t"database/sql"\n)\n\nvar _ sql.DB\n'

    def test_no_hasura_short_circuits(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        non_hasura_ctx: ProjectContext,
        non_hasura_project: Path,
    ) -> None:
        # Even with a clearly violating Go file, no findings are emitted
        # because the project has no Hasura signal — keeps cost zero for
        # non-Hasura repos using the verifier suite.
        bad_file = non_hasura_project / "server" / "repo.go"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text(self._BAD_GO)
        result = validator.validate(non_hasura_ctx, file_path=str(bad_file), mode="post_tool_use")
        assert result.findings == []

    def test_post_tool_use_skips_exempt_files(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        hasura_ctx: ProjectContext,
        hasura_project: Path,
    ) -> None:
        test_file = hasura_project / "server" / "internal" / "service_test.go"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(self._BAD_GO)
        result = validator.validate(hasura_ctx, file_path=str(test_file), mode="post_tool_use")
        assert result.findings == []

    def test_post_tool_use_flags_violation_in_real_file(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        hasura_ctx: ProjectContext,
        hasura_project: Path,
    ) -> None:
        repo = hasura_project / "server" / "internal" / "repo.go"
        repo.parent.mkdir(parents=True, exist_ok=True)
        repo.write_text(self._BAD_GO)
        result = validator.validate(hasura_ctx, file_path=str(repo), mode="post_tool_use")
        assert any(f.rule == "V20-SQL-IMPORT" for f in result.findings)

    def test_stop_mode_scans_project(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        hasura_ctx: ProjectContext,
        hasura_project: Path,
    ) -> None:
        # Two Go files: one violating, one exempt (in mocks/). Stop mode
        # should pick up only the violating one.
        bad = hasura_project / "server" / "internal" / "repo.go"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(self._BAD_GO)

        mock = hasura_project / "server" / "internal" / "mocks" / "fake.go"
        mock.parent.mkdir(parents=True, exist_ok=True)
        mock.write_text(self._BAD_GO)

        result = validator.validate(hasura_ctx, file_path=None, mode="stop")
        # The path stored in findings comes from rglob'd ctx.server_dir,
        # which has been resolved through git's toplevel — compare via
        # Path.resolve() on both sides to handle macOS /var ↔ /private/var.
        files_with_findings = {Path(f.file).resolve() for f in result.findings}
        assert bad.resolve() in files_with_findings
        assert mock.resolve() not in files_with_findings

    def test_post_tool_use_non_go_file_no_findings(
        self,
        validator: HasuraGraphQLEnforcementValidator,
        hasura_ctx: ProjectContext,
        hasura_project: Path,
    ) -> None:
        ts_file = hasura_project / "web" / "src" / "App.tsx"
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text("import 'database/sql';\n")  # nonsensical TS line
        result = validator.validate(hasura_ctx, file_path=str(ts_file), mode="post_tool_use")
        assert result.findings == []
