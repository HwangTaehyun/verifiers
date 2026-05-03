"""Tests for V80 — go-circular-deps (Phase 73)."""

from __future__ import annotations

import pytest

from hooks.validators.go_circular_deps import GoCircularDepsValidator


@pytest.fixture
def validator() -> GoCircularDepsValidator:
    return GoCircularDepsValidator()


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# ── 1. No cycles → silent ────────────────────────────────────────────────────


class TestNoCyclesSilent:
    def test_linear_imports_pass(self, validator, tmp_project, project_ctx):
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nimport "testproject/internal/b"\nvar _ = b.X\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nimport "testproject/internal/c"\nvar _ = c.Y\n',
        )
        _write(
            tmp_project / "server" / "internal" / "c" / "c.go",
            'package c\nvar Y int\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_no_go_mod_silent(self, validator, tmp_project, project_ctx):
        # tmp_project has server/go.mod; remove it
        (tmp_project / "server" / "go.mod").unlink()
        # Create a cycle that should be IGNORED because no go.mod
        _write(
            tmp_project / "server" / "x.go",
            'package x\nimport "anything/y"\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Two-package cycle → V80-CIRCULAR-DEPS ─────────────────────────────────


class TestTwoPackageCycle:
    def test_a_imports_b_imports_a(self, validator, tmp_project, project_ctx):
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nimport "testproject/internal/b"\nvar _ = b.X\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nimport "testproject/internal/a"\nvar _ = a.Y\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V80-CIRCULAR-DEPS"
        msg = findings[0].message
        assert "testproject/internal/a" in msg
        assert "testproject/internal/b" in msg
        assert "2 packages" in msg


# ── 3. Three-package cycle ───────────────────────────────────────────────────


class TestThreePackageCycle:
    def test_three_pkg_cycle(self, validator, tmp_project, project_ctx):
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nimport "testproject/internal/b"\nvar _ = b.X\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nimport "testproject/internal/c"\nvar _ = c.X\n',
        )
        _write(
            tmp_project / "server" / "internal" / "c" / "c.go",
            'package c\nimport "testproject/internal/a"\nvar _ = a.X\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert "3 packages" in findings[0].message


# ── 4. External imports ignored ──────────────────────────────────────────────


class TestExternalImportsIgnored:
    def test_third_party_imports_no_cycle(self, validator, tmp_project, project_ctx):
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\n'
            'import (\n'
            '  "fmt"\n'
            '  "github.com/jmoiron/sqlx"\n'
            '  "testproject/internal/b"\n'
            ')\n'
            'var _ = b.X; var _ = sqlx.DB{}; var _ = fmt.Println\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nvar X int\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Test files excluded from cycle detection ──────────────────────────────


class TestTestFilesExcluded:
    def test_test_file_cycle_ignored(self, validator, tmp_project, project_ctx):
        # Production code is acyclic; only the test file creates a cycle.
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nvar X int\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nimport "testproject/internal/a"\nvar _ = a.X\n',
        )
        # Test in a/ imports b/ — would form a cycle if test files counted.
        _write(
            tmp_project / "server" / "internal" / "a" / "a_test.go",
            'package a\nimport (\n  "testing"\n  "testproject/internal/b"\n)\nfunc TestX(t *testing.T) { _ = b.X }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. Self-package imports excluded ─────────────────────────────────────────


class TestSelfImportsExcluded:
    def test_no_self_loop_finding(self, validator, tmp_project, project_ctx):
        # Two files in same package; one imports its own package (rare but possible).
        # Should not be flagged as a cycle.
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nvar X int\n',
        )
        _write(
            tmp_project / "server" / "internal" / "a" / "b.go",
            'package a\nvar Y int = X\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. Multiple independent cycles ───────────────────────────────────────────


class TestMultipleCycles:
    def test_two_disjoint_cycles_both_flagged(self, validator, tmp_project, project_ctx):
        # Cycle 1: a ↔ b
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nimport "testproject/internal/b"\nvar _ = b.X\n',
        )
        _write(
            tmp_project / "server" / "internal" / "b" / "b.go",
            'package b\nimport "testproject/internal/a"\nvar _ = a.Y\n',
        )
        # Cycle 2: x ↔ y
        _write(
            tmp_project / "server" / "internal" / "x" / "x.go",
            'package x\nimport "testproject/internal/y"\nvar _ = y.A\n',
        )
        _write(
            tmp_project / "server" / "internal" / "y" / "y.go",
            'package y\nimport "testproject/internal/x"\nvar _ = x.B\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 2
        msgs = " ".join(f.message for f in findings)
        assert "testproject/internal/a" in msgs and "testproject/internal/b" in msgs
        assert "testproject/internal/x" in msgs and "testproject/internal/y" in msgs


# ── 8. validate_file is no-op (Stop-only) ────────────────────────────────────


class TestValidateFileNoOp:
    def test_validate_file_returns_empty(self, validator, tmp_project, project_ctx):
        _write(
            tmp_project / "server" / "internal" / "a" / "a.go",
            'package a\nimport "testproject/internal/b"\n',
        )
        f = tmp_project / "server" / "internal" / "a" / "a.go"
        # file_patterns = [] → validate_file uses default (returns [])
        findings = validator.validate_file(project_ctx, str(f))
        assert findings == []
