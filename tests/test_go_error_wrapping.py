"""Tests for V34 — Go Error Wrapping (%w)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.go_error_wrapping import GoErrorWrappingValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoErrorWrappingValidator:
    return GoErrorWrappingValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _go(tmp_project: Path, rel: str) -> Path:
    """Return path for a Go file relative to tmp_project."""
    return tmp_project / rel


# ── 1. Wrapped return → no findings ──────────────────────────────────────


class TestWrappedReturnPasses:
    def test_wrapped_return_passes(self, validator, tmp_project, project_ctx):
        """fmt.Errorf with %w on the preceding line: no finding."""
        path = _go(tmp_project, "server/internal/finance/invoice.go")
        _write(
            path,
            """
            package finance

            import (
                "fmt"
            )

            func findInvoice(id string) error {
                err := lookup(id)
                if err != nil {
                    err = fmt.Errorf("find invoice %s: %w", id, err)
                    return err
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []

    def test_inline_wrapped_return_passes(self, validator, tmp_project, project_ctx):
        """return fmt.Errorf(...%w..., err) on a single line: no finding."""
        path = _go(tmp_project, "server/internal/api/handler.go")
        _write(
            path,
            """
            package api

            import "fmt"

            func doWork() error {
                err := work()
                if err != nil {
                    return fmt.Errorf("doWork failed: %w", err)
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 2. Bare return err → warning ─────────────────────────────────────────


class TestBareReturnErrWarns:
    def test_bare_return_err_warns(self, validator, tmp_project, project_ctx):
        """return err without preceding wrap: one V34-BARE-ERROR-RETURN warning."""
        path = _go(tmp_project, "server/cmd/normalize-cmf/main.go")
        _write(
            path,
            """
            package main

            func run() error {
                err := doSomething()
                if err != nil {
                    return err
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warns = [f for f in findings if f.rule == "V34-BARE-ERROR-RETURN"]
        assert len(warns) == 1
        assert warns[0].severity == "warning"
        assert warns[0].line is not None


# ── 3. Multi-value return (return foo, err) → flagged ────────────────────


class TestReturnNilErrForm:
    def test_return_nil_err_form(self, validator, tmp_project, project_ctx):
        """return nil, err without preceding wrap is flagged."""
        path = _go(tmp_project, "server/internal/repo/user.go")
        _write(
            path,
            """
            package repo

            func findUser(id string) (*User, error) {
                u, err := db.Query(id)
                if err != nil {
                    return nil, err
                }
                return u, nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warns = [f for f in findings if f.rule == "V34-BARE-ERROR-RETURN"]
        assert len(warns) == 1


# ── 4. Generated file (gen/ path) skipped ────────────────────────────────


class TestGeneratedFileSkipped:
    def test_generated_file_skipped(self, validator, tmp_project, project_ctx):
        """Files under gen/ are skipped entirely."""
        path = _go(tmp_project, "server/gen/proto/foo.go")
        _write(
            path,
            """
            package proto

            func bar() error {
                err := something()
                if err != nil {
                    return err
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 5. Test file skipped ──────────────────────────────────────────────────


class TestTestFileSkipped:
    def test_test_file_skipped(self, validator, tmp_project, project_ctx):
        """*_test.go files are not scanned."""
        path = _go(tmp_project, "server/internal/repo/user_test.go")
        _write(
            path,
            """
            package repo

            import "testing"

            func TestFindUser(t *testing.T) {
                err := findUser("x")
                if err != nil {
                    return err
                }
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 6. // Code generated header skipped ──────────────────────────────────


class TestCodeGeneratedMarkerSkipped:
    def test_code_generated_marker_skipped(self, validator, tmp_project, project_ctx):
        """File whose first lines contain '// Code generated' is skipped."""
        path = _go(tmp_project, "server/internal/gen_output.go")
        _write(
            path,
            """
            // Code generated by protoc-gen-go. DO NOT EDIT.
            // source: foo.proto

            package internal

            func generated() error {
                err := doSomething()
                if err != nil {
                    return err
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 7. connect.NewError satisfies the wrapping check ─────────────────────


class TestConnectNewErrorSatisfies:
    def test_connect_newerror_satisfies(self, validator, tmp_project, project_ctx):
        """connect.NewError on the preceding line: no finding."""
        path = _go(tmp_project, "server/internal/api/grpc.go")
        _write(
            path,
            """
            package api

            import "connectrpc.com/connect"

            func (s *Server) GetUser(ctx context.Context, req *connect.Request[GetUserRequest]) (*connect.Response[GetUserResponse], error) {
                user, err := s.repo.Find(ctx, req.Msg.Id)
                if err != nil {
                    return nil, connect.NewError(connect.CodeNotFound, err)
                }
                return connect.NewResponse(&GetUserResponse{User: user}), nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 8. Empty project → no findings ───────────────────────────────────────


class TestNoInternalDirNoFindings:
    def test_no_internal_dir_no_findings(self, validator, tmp_path):
        """ProjectContext with no Go files → validate_project returns empty."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 9. validate_file single-file Tier-2 path ─────────────────────────────


class TestValidateFileSingleFile:
    def test_validate_file_single_file(self, validator, tmp_project, project_ctx):
        """Tier 2 path: validate_file returns findings for a single file."""
        path = _go(tmp_project, "server/internal/svc/payment.go")
        _write(
            path,
            """
            package svc

            func charge(amount int) error {
                err := gateway.Charge(amount)
                if err != nil {
                    return err
                }
                return nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert any(f.rule == "V34-BARE-ERROR-RETURN" for f in findings)


# ── 10. Multiple bare returns in one file ────────────────────────────────


class TestMultipleBareReturnsInOneFile:
    def test_multiple_bare_returns_in_one_file(self, validator, tmp_project, project_ctx):
        """Each bare return in a file produces an independent finding."""
        path = _go(tmp_project, "server/cmd/worker/main.go")
        _write(
            path,
            """
            package main

            func step1() error {
                err := doA()
                if err != nil {
                    return err
                }
                return nil
            }

            func step2() error {
                err := doB()
                if err != nil {
                    return err
                }
                return nil
            }

            func step3() (int, error) {
                n, err := doC()
                if err != nil {
                    return 0, err
                }
                return n, nil
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warns = [f for f in findings if f.rule == "V34-BARE-ERROR-RETURN"]
        assert len(warns) == 3
        # Each finding should have a distinct line number
        lines = [f.line for f in warns]
        assert len(set(lines)) == 3
