"""Tests for V35 — Go Context Propagation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.go_context_propagation import GoContextPropagationValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoContextPropagationValidator:
    return GoContextPropagationValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _internal_go(tmp_project: Path, name: str = "foo.go", subpkg: str = "service") -> Path:
    """Return path for server/internal/<subpkg>/<name>."""
    return tmp_project / "server" / "internal" / subpkg / name


# ── 1. Caller ctx used — no finding ──────────────────────────────────────────


class TestCallerCtxUsedPasses:
    def test_caller_ctx_used_passes(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project)
        _write(
            path,
            """
            package service

            import (
                "context"
                "time"
            )

            func Fetch(ctx context.Context, id string) ([]byte, error) {
                ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
                defer cancel()
                return nil, nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 2. context.Background() mid-flow → V35 error ─────────────────────────────


class TestMidFlowBackgroundErrors:
    def test_mid_flow_background_errors(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project)
        _write(
            path,
            """
            package service

            import (
                "context"
                "time"
            )

            func Render(ctx context.Context) error {
                ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
                defer cancel()
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        errors = [f for f in findings if f.rule == "V35-MID-FLOW-BACKGROUND-CTX"]
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "Background" in errors[0].message


# ── 3. context.TODO() mid-flow → V35 error ───────────────────────────────────


class TestMidFlowTodoErrors:
    def test_mid_flow_todo_errors(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project)
        _write(
            path,
            """
            package service

            import (
                "context"
                "time"
            )

            func GetByID(ctx context.Context, id string) error {
                ctx, cancel := context.WithTimeout(context.TODO(), 10*time.Second)
                defer cancel()
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        errors = [f for f in findings if f.rule == "V35-MID-FLOW-BACKGROUND-CTX"]
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "TODO" in errors[0].message


# ── 4. signal.NotifyContext present → goroutine root exemption ───────────────


class TestSignalNotifyContextExempts:
    def test_signal_notify_context_exempts(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="worker.go", subpkg="jobs")
        _write(
            path,
            """
            package jobs

            import (
                "context"
                "os"
                "os/signal"
            )

            func RunWorker() {
                ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
                defer cancel()
                _ = ctx
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 5. Test file (_test.go) is skipped ───────────────────────────────────────


class TestTestFileSkipped:
    def test_test_file_skipped(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="foo_test.go")
        _write(
            path,
            """
            package service

            import (
                "context"
                "testing"
            )

            func TestSomething(t *testing.T) {
                ctx := context.Background()
                _ = ctx
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 6. No internal/ directory → no findings ──────────────────────────────────


class TestNoInternalDirNoFindings:
    def test_no_internal_dir_no_findings(self, validator, tmp_path):
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 7. Package-scope var declaration → exempt ────────────────────────────────


class TestVarPackageScopeExempt:
    def test_var_package_scope_exempt(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="cron.go", subpkg="cron")
        _write(
            path,
            """
            package cron

            import "context"

            var bgCtx = context.Background()

            func Run() {
                _ = bgCtx
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 8. Multiple violations in one file — each flagged independently ───────────


class TestMultipleViolationsOneFile:
    def test_multiple_violations_one_file(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="multi.go", subpkg="finance")
        _write(
            path,
            """
            package finance

            import (
                "context"
                "time"
            )

            func A(ctx context.Context) error {
                c1, cancel := context.WithTimeout(context.Background(), 5*time.Second)
                defer cancel()
                _ = c1
                return nil
            }

            func B(ctx context.Context) error {
                c2, cancel := context.WithTimeout(context.TODO(), 10*time.Second)
                defer cancel()
                _ = c2
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        errors = [f for f in findings if f.rule == "V35-MID-FLOW-BACKGROUND-CTX"]
        assert len(errors) == 2


# ── 9. validate_file Tier 2 path ─────────────────────────────────────────────


class TestValidateFileSingleFile:
    def test_validate_file_single_file(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="repo.go", subpkg="users")
        _write(
            path,
            """
            package users

            import "context"

            func List(ctx context.Context) error {
                _ = context.Background()
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert len(findings) == 1
        assert findings[0].rule == "V35-MID-FLOW-BACKGROUND-CTX"
        assert findings[0].line is not None


# ── 10. cmd/ files not scanned (V35 is internal/-only) ───────────────────────


class TestCmdFilesNotScanned:
    def test_cmd_files_not_scanned(self, validator, tmp_project, project_ctx):
        cmd_path = tmp_project / "server" / "cmd" / "main.go"
        _write(
            cmd_path,
            """
            package main

            import "context"

            func main() {
                ctx := context.Background()
                _ = ctx
            }
            """,
        )
        # Tier 2: validate_file on the cmd file should return nothing
        findings = validator.validate_file(project_ctx, str(cmd_path))
        assert findings == []

        # Tier 3: validate_project should also not flag cmd/ files
        findings_proj = validator.validate_project(project_ctx)
        assert all(f.rule != "V35-MID-FLOW-BACKGROUND-CTX" for f in findings_proj)
