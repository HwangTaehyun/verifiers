"""Tests for V27 — Connect-RPC handler completeness."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.connect_handler import ConnectHandlerValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> ConnectHandlerValidator:
    return ConnectHandlerValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """server/ + server/internal/ + server/proto/."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server" / "internal").mkdir(parents=True)
    (tmp_path / "server" / "proto").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _seed_connect_import(repo: Path) -> None:
    """Drop a Go file that imports Connect — gates V27 on."""
    _write(
        repo / "server" / "internal" / "marker.go",
        """
        package marker
        import _ "connectrpc.com/connect"
        """,
    )


# ── 1. No-Connect short-circuit ─────────────────────────────────────


class TestNoConnect:
    def test_no_connect_import_returns_empty(self, validator, repo, ctx):
        # Even with proto + handler stubs, V27 stays silent if Connect
        # isn't a project dependency.
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            service UserService {
              rpc CreateUser(CreateUserRequest) returns (CreateUserResponse);
            }
            """,
        )
        assert validator.validate_project(ctx) == []


# ── 2. V27-UNIMPLEMENTED-RPC ────────────────────────────────────────


class TestUnimplementedRpc:
    def test_proto_rpc_with_no_handler_errors(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            service UserService {
              rpc CreateUser(CreateUserRequest) returns (CreateUserResponse);
              rpc GetUser(GetUserRequest) returns (GetUserResponse);
            }
            """,
        )
        # Only CreateUser implemented
        _write(
            repo / "server" / "internal" / "users" / "handler.go",
            """
            package users
            import "context"
            type UserServiceServer struct{}
            func (s *UserServiceServer) CreateUser(ctx context.Context, req *connect.Request[X]) (*connect.Response[Y], error) {
                return nil, nil
            }
            """,
        )
        findings = validator.validate_project(ctx)
        unimpl = [f for f in findings if f.rule == "V27-UNIMPLEMENTED-RPC"]
        assert len(unimpl) == 1
        assert "GetUser" in unimpl[0].message

    def test_all_rpcs_implemented_passes(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            service UserService {
              rpc CreateUser(CreateUserRequest) returns (CreateUserResponse);
            }
            """,
        )
        _write(
            repo / "server" / "internal" / "users" / "handler.go",
            """
            package users
            import "context"
            type UserServiceServer struct{}
            func (s *UserServiceServer) CreateUser(ctx context.Context, req *connect.Request[X]) (*connect.Response[Y], error) {
                return nil, nil
            }
            """,
        )
        findings = validator.validate_project(ctx)
        unimpl = [f for f in findings if f.rule == "V27-UNIMPLEMENTED-RPC"]
        assert unimpl == []


# ── 3. V27-NO-INTERCEPTORS / V27-MISSING-*-INTERCEPTOR ──────────────


class TestInterceptors:
    def test_handler_register_without_interceptors_errors(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "server" / "wire.go",
            """
            package server
            import (
                "net/http"
                "connectrpc.com/connect"
            )
            func setup(impl *UserServiceServer) {
                mux := http.NewServeMux()
                mux.Handle(usersv1connect.NewUserServiceHandler(
                    impl,
                ))
            }
            """,
        )
        findings = validator.validate_project(ctx)
        ni = [f for f in findings if f.rule == "V27-NO-INTERCEPTORS"]
        assert len(ni) == 1

    def test_handler_with_full_interceptor_set_passes(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "server" / "wire.go",
            """
            package server
            import "connectrpc.com/connect"
            func setup(impl *UserServiceServer) {
                mux.Handle(usersv1connect.NewUserServiceHandler(
                    impl,
                    connect.WithInterceptors(
                        AuthInterceptor(jwtVerifier),
                        LoggingInterceptor(logger),
                        ValidationInterceptor(),
                    ),
                ))
            }
            """,
        )
        findings = validator.validate_project(ctx)
        ic = [f for f in findings if f.rule == "V27-NO-INTERCEPTORS" or f.rule.startswith("V27-MISSING-")]
        assert ic == []

    def test_missing_one_interceptor_warns(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "server" / "wire.go",
            """
            package server
            import "connectrpc.com/connect"
            func setup(impl *UserServiceServer) {
                mux.Handle(usersv1connect.NewUserServiceHandler(
                    impl,
                    connect.WithInterceptors(
                        AuthInterceptor(jwtVerifier),
                        LoggingInterceptor(logger),
                    ),
                ))
            }
            """,
        )
        findings = validator.validate_project(ctx)
        miss = [f for f in findings if f.rule.startswith("V27-MISSING-")]
        assert any("VALIDATION" in f.rule for f in miss)


# ── 4. V27-RAW-ERROR-RETURN ─────────────────────────────────────────


class TestRawErrorReturn:
    def test_handler_returning_raw_err_warns(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "users" / "handler.go",
            """
            package users
            import (
                "context"
                "connectrpc.com/connect"
            )
            func (s *UserServiceServer) GetUser(
                ctx context.Context,
                req *connect.Request[X],
            ) (*connect.Response[Y], error) {
                user, err := s.repo.Find(ctx, req.Msg.Id)
                if err != nil {
                    return nil, err
                }
                return connect.NewResponse(user), nil
            }
            """,
        )
        findings = validator.validate_project(ctx)
        raw = [f for f in findings if f.rule == "V27-RAW-ERROR-RETURN"]
        assert len(raw) >= 1

    def test_handler_with_connect_newerror_passes(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "users" / "handler.go",
            """
            package users
            import (
                "context"
                "connectrpc.com/connect"
            )
            func (s *UserServiceServer) GetUser(
                ctx context.Context,
                req *connect.Request[X],
            ) (*connect.Response[Y], error) {
                user, err := s.repo.Find(ctx, req.Msg.Id)
                if err != nil {
                    return nil, connect.NewError(connect.CodeNotFound, err)
                }
                return connect.NewResponse(user), nil
            }
            """,
        )
        findings = validator.validate_project(ctx)
        raw = [f for f in findings if f.rule == "V27-RAW-ERROR-RETURN"]
        assert raw == []

    def test_sentinel_error_return_warns(self, validator, repo, ctx):
        _seed_connect_import(repo)
        _write(
            repo / "server" / "internal" / "users" / "handler.go",
            """
            package users
            import (
                "context"
                "errors"
                "connectrpc.com/connect"
            )
            var ErrNotFound = errors.New("not found")
            func (s *UserServiceServer) GetUser(
                ctx context.Context,
                req *connect.Request[X],
            ) (*connect.Response[Y], error) {
                return nil, ErrNotFound
            }
            """,
        )
        findings = validator.validate_project(ctx)
        raw = [f for f in findings if f.rule == "V27-RAW-ERROR-RETURN"]
        assert len(raw) == 1
