"""Tests for V39 — Go Context-Scoped Logger Discipline."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.go_context_scoped_logger import GoContextScopedLoggerValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoContextScopedLoggerValidator:
    return GoContextScopedLoggerValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _internal_go(tmp_project: Path, name: str = "foo.go", subpkg: str = "service") -> Path:
    """Return path for server/internal/<subpkg>/<name>."""
    return tmp_project / "server" / "internal" / subpkg / name


# ── 1. File uses zerolog.Ctx(ctx) — passes ───────────────────────────────────


class TestCtxScopedLoggerPasses:
    def test_ctx_scoped_logger_passes(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project)
        _write(
            path,
            """
            package service

            import (
                "context"
                "github.com/rs/zerolog"
            )

            func (h *Handler) CreateUser(ctx context.Context) error {
                logger := zerolog.Ctx(ctx)
                logger.Info().Msg("creating user")
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 2. File uses log.Ctx(ctx) — also valid ───────────────────────────────────


class TestLogCtxAlternativePasses:
    def test_log_ctx_alternative_passes(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="repo.go", subpkg="repository")
        _write(
            path,
            """
            package repository

            import (
                "context"
                "github.com/rs/zerolog/log"
            )

            func (r *Repo) GetUser(ctx context.Context, id string) error {
                logger := log.Ctx(ctx)
                logger.Info().Str("id", id).Msg("fetching user")
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 3. Global logger only, no Ctx retrieval → V39 warning ────────────────────


class TestGlobalOnlyWarns:
    def test_global_only_warns(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="payment.go", subpkg="payment")
        _write(
            path,
            """
            package payment

            import (
                "context"
                "github.com/rs/zerolog/log"
            )

            func (s *Service) ProcessPayment(ctx context.Context) error {
                log.Info().Msg("starting payment")
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warnings = [f for f in findings if f.rule == "V39-GLOBAL-LOGGER-MISUSE"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        assert "global zerolog calls" in warnings[0].message


# ── 4. middleware/ directory is skipped ──────────────────────────────────────


class TestMiddlewareDirSkipped:
    def test_middleware_dir_skipped(self, validator, tmp_project, project_ctx):
        path = tmp_project / "server" / "internal" / "middleware" / "logging.go"
        _write(
            path,
            """
            package middleware

            import (
                "context"
                "github.com/rs/zerolog/log"
            )

            func LoggingInterceptor(ctx context.Context) {
                log.Info().Msg("request received")
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
                "testing"
                "github.com/rs/zerolog/log"
            )

            func TestSomething(t *testing.T) {
                log.Info().Msg("test log")
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


# ── 7. Different log level methods all flagged ───────────────────────────────


class TestLogWarnLogErrorAlsoFlagged:
    def test_log_warn_log_error_also_flagged(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="multi_level.go", subpkg="billing")
        _write(
            path,
            """
            package billing

            import (
                "context"
                "github.com/rs/zerolog/log"
            )

            func (s *Service) Charge(ctx context.Context) error {
                log.Warn().Msg("low balance")
                log.Error().Msg("charge failed")
                log.Debug().Msg("debug info")
                log.Trace().Msg("trace info")
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warnings = [f for f in findings if f.rule == "V39-GLOBAL-LOGGER-MISUSE"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"


# ── 8. Multiple global calls → emit ONE finding at the FIRST call line ────────


class TestOnlyFirstCallLineEmitted:
    def test_only_first_call_line_emitted(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="multi_call.go", subpkg="order")
        _write(
            path,
            """
            package order

            import "github.com/rs/zerolog/log"

            func Process() {
                log.Info().Msg("first call")
                log.Error().Msg("second call")
                log.Warn().Msg("third call")
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert len(findings) == 1
        # The first global call is log.Info() — should be flagged at that line
        assert findings[0].line is not None
        # Read back to verify line number matches first log call
        lines = path.read_text().splitlines()
        flagged_line = lines[findings[0].line - 1]
        assert "log.Info" in flagged_line


# ── 9. validate_file Tier 2 path ─────────────────────────────────────────────


class TestValidateFileSingleFile:
    def test_validate_file_single_file(self, validator, tmp_project, project_ctx):
        path = _internal_go(tmp_project, name="repo.go", subpkg="users")
        _write(
            path,
            """
            package users

            import "github.com/rs/zerolog/log"

            func List() {
                log.Info().Msg("listing users")
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert len(findings) == 1
        assert findings[0].rule == "V39-GLOBAL-LOGGER-MISUSE"
        assert findings[0].line is not None
        assert findings[0].severity == "warning"


# ── 10. cmd/ files not scanned ───────────────────────────────────────────────


class TestCmdFilesNotScanned:
    def test_cmd_files_not_scanned(self, validator, tmp_project, project_ctx):
        cmd_path = tmp_project / "server" / "cmd" / "main.go"
        _write(
            cmd_path,
            """
            package main

            import "github.com/rs/zerolog/log"

            func main() {
                log.Info().Msg("server starting")
            }
            """,
        )
        # Tier 2: validate_file on cmd file returns nothing
        findings = validator.validate_file(project_ctx, str(cmd_path))
        assert findings == []

        # Tier 3: validate_project should not flag cmd/ files
        findings_proj = validator.validate_project(project_ctx)
        assert all(f.rule != "V39-GLOBAL-LOGGER-MISUSE" for f in findings_proj)
