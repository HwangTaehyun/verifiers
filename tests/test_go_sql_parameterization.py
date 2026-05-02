"""Tests for V61 — go-sql-parameterization (Phase 72).

Covers:
  - V61-SQL-CONCAT  — string concat into db/tx call (error)
  - V61-SQL-SPRINTF — fmt.Sprintf into db/tx call (error)
  - Placeholder syntax passes (?, :name)
  - Test files (_test.go) skipped
  - Escape-hatch comment silences finding
  - Argument-position concat (NOT first arg) is not flagged
  - validate_file (Tier 2) vs validate_project (Tier 3)
"""

from __future__ import annotations

import pytest

from hooks.validators.go_sql_parameterization import GoSqlParameterizationValidator


@pytest.fixture
def validator() -> GoSqlParameterizationValidator:
    return GoSqlParameterizationValidator()


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# ── 1. Placeholder syntax passes ─────────────────────────────────────────────


class TestPlaceholderSyntaxPasses:
    def test_question_mark_passes(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "user.go"
        _write(
            f,
            'package internal\n'
            'func get(db *sql.DB, id string) {\n'
            '    rows, _ := db.Query("SELECT * FROM users WHERE id = ?", id)\n'
            '    _ = rows\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_named_param_passes(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u.go"
        _write(
            f,
            'package internal\n'
            'func ins(db *sqlx.DB, name string) {\n'
            '    db.NamedExec("INSERT INTO u (n) VALUES (:n)", map[string]any{"n": name})\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. String concat → V61-SQL-CONCAT ────────────────────────────────────────


class TestStringConcatFlagged:
    def test_db_query_concat_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "user.go"
        _write(
            f,
            'package internal\n'
            'func get(db *sql.DB, id string) {\n'
            '    db.Query("SELECT * FROM users WHERE id = " + id)\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V61-SQL-CONCAT"
        assert findings[0].severity == "error"
        assert findings[0].line == 3

    def test_tx_exec_concat_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u.go"
        _write(
            f,
            'package internal\n'
            'func ins(tx *sql.Tx, n string) {\n'
            '    tx.Exec("INSERT INTO u VALUES(\'" + n + "\')")\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V61-SQL-CONCAT"


# ── 3. fmt.Sprintf → V61-SQL-SPRINTF ─────────────────────────────────────────


class TestSprintfFlagged:
    def test_sprintf_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u.go"
        _write(
            f,
            'package internal\n'
            'func get(db *sql.DB, id string) {\n'
            '    db.Query(fmt.Sprintf("SELECT * FROM u WHERE id = %s", id))\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V61-SQL-SPRINTF"
        assert findings[0].severity == "error"


# ── 4. Escape hatch ──────────────────────────────────────────────────────────


class TestEscapeHatch:
    def test_verifier_safe_comment_silences(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u.go"
        _write(
            f,
            'package internal\n'
            'func get(db *sql.DB, table string) {\n'
            '    // table validated against allowlist before this call\n'
            '    db.Query("SELECT * FROM " + table) // verifier:sql-safe table from allowlist\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Test files skipped ────────────────────────────────────────────────────


class TestTestFilesSkipped:
    def test_underscore_test_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u_test.go"
        _write(
            f,
            'package internal\n'
            'func TestQuery(t *testing.T) {\n'
            '    db.Query("SELECT * FROM u WHERE id = " + tt.id)\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. Concat in 2nd arg (NOT building SQL) — must pass ──────────────────────


class TestConcatInSecondArgPasses:
    def test_query_with_int_arith_arg_not_flagged(self, validator, tmp_project, project_ctx):
        # The `+` lives in the second arg (an int expr), not the SQL string.
        f = tmp_project / "server" / "internal" / "u.go"
        _write(
            f,
            'package internal\n'
            'func get(db *sql.DB, a, b int) {\n'
            '    db.Query("SELECT * FROM u WHERE n = ?", a+b)\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_scans_target(self, validator, tmp_project, project_ctx):
        f1 = tmp_project / "server" / "internal" / "a.go"
        f2 = tmp_project / "server" / "internal" / "b.go"
        _write(
            f1,
            'package internal\n'
            'func a(db *sql.DB, id string) { db.Query("SELECT 1 WHERE id = " + id) }\n',
        )
        _write(
            f2,
            'package internal\n'
            'func b(db *sql.DB, id string) { db.Query("SELECT 2 WHERE id = " + id) }\n',
        )
        findings = validator.validate_file(project_ctx, str(f1))
        assert len(findings) == 1
        assert findings[0].file == str(f1)

    def test_validate_file_test_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "u_test.go"
        _write(
            f,
            'package internal\n'
            'func Test(t *testing.T) { db.Query("SELECT 1 WHERE id = " + id) }\n',
        )
        findings = validator.validate_file(project_ctx, str(f))
        assert findings == []
