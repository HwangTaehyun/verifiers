"""Tests for V36 — Go HTTP Server Hardening."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.go_http_hardening import GoHttpHardeningValidator
from lib.project_context import ProjectContext


# ── Helpers ─────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoHttpHardeningValidator:
    return GoHttpHardeningValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _main_go(tmp_project: Path, subdir: str = "server") -> Path:
    """Return path for server/cmd/<subdir>/main.go."""
    return tmp_project / "server" / "cmd" / subdir / "main.go"


# ── 1. Full timeouts + graceful shutdown → no findings ──────────────


class TestFullTimeoutsPass:
    def test_full_timeouts_pass(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import (
                "context"
                "net/http"
                "os"
                "os/signal"
                "syscall"
                "time"
            )

            func main() {
                ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
                defer cancel()

                mux := http.NewServeMux()

                server := &http.Server{
                    Addr:              ":8080",
                    Handler:           mux,
                    ReadHeaderTimeout: 5 * time.Second,
                    ReadTimeout:       30 * time.Second,
                    WriteTimeout:      60 * time.Second,
                    IdleTimeout:       120 * time.Second,
                }

                go func() {
                    <-ctx.Done()
                    server.Shutdown(context.Background())
                }()

                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 2. No timeouts → V36-NO-HTTP-TIMEOUTS error ──────────────────────


class TestNoTimeoutsErrors:
    def test_no_timeouts_errors(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                server := &http.Server{
                    Addr:    ":8080",
                    Handler: mux,
                }
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        errors = [f for f in findings if f.rule == "V36-NO-HTTP-TIMEOUTS"]
        assert len(errors) == 1
        assert errors[0].severity == "error"


# ── 3. Partial timeouts (ReadTimeout only) → still flagged ───────────


class TestPartialTimeoutsErrors:
    def test_partial_timeouts_errors(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import (
                "net/http"
                "time"
            )

            func main() {
                server := &http.Server{
                    Addr:        ":8080",
                    ReadTimeout: 30 * time.Second,
                }
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        errors = [f for f in findings if f.rule == "V36-NO-HTTP-TIMEOUTS"]
        assert len(errors) == 1


# ── 4. signal.NotifyContext present → no V36-NO-GRACEFUL-SHUTDOWN ─────


class TestGracefulShutdownPresent:
    def test_graceful_shutdown_present(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import (
                "context"
                "net/http"
                "os"
                "os/signal"
                "syscall"
                "time"
            )

            func main() {
                ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
                defer cancel()

                server := &http.Server{
                    Addr:              ":8080",
                    ReadHeaderTimeout: 5 * time.Second,
                    WriteTimeout:      60 * time.Second,
                }

                go func() { <-ctx.Done(); server.Shutdown(ctx) }()
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        shutdown_warns = [f for f in findings if f.rule == "V36-NO-GRACEFUL-SHUTDOWN"]
        assert shutdown_warns == []


# ── 5. No graceful shutdown → V36-NO-GRACEFUL-SHUTDOWN warning ───────


class TestNoGracefulShutdown:
    def test_no_graceful_shutdown(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import (
                "net/http"
                "time"
            )

            func main() {
                server := &http.Server{
                    Addr:              ":8080",
                    ReadHeaderTimeout: 5 * time.Second,
                    WriteTimeout:      60 * time.Second,
                }
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        warns = [f for f in findings if f.rule == "V36-NO-GRACEFUL-SHUTDOWN"]
        assert len(warns) == 1
        assert warns[0].severity == "warning"


# ── 6. Non-main.go file skipped ───────────────────────────────────────


class TestNonMainFileSkipped:
    def test_non_main_file_skipped(self, validator, tmp_project, project_ctx):
        # A file that is not main.go should return no findings
        path = tmp_project / "server" / "cmd" / "server" / "server.go"
        _write(
            path,
            """
            package main

            import "net/http"

            func setup() *http.Server {
                return &http.Server{Addr: ":8080"}
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []

    def test_file_outside_cmd_skipped(self, validator, tmp_project, project_ctx):
        # main.go not under /cmd/ should be skipped
        path = tmp_project / "server" / "main.go"
        _write(
            path,
            """
            package main

            import "net/http"

            func main() {
                server := &http.Server{Addr: ":8080"}
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 7. No http.Server literal → no findings ───────────────────────────


class TestNoHttpServerLiteral:
    def test_no_http_server_literal(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project)
        _write(
            path,
            """
            package main

            import "fmt"

            func main() {
                fmt.Println("hello")
            }
            """,
        )
        findings = validator.validate_file(project_ctx, str(path))
        assert findings == []


# ── 8. ctx.server_dir is None → validate_project returns empty ────────


class TestNoServerDirReturnsEmpty:
    def test_no_server_dir_returns_empty(self, validator, tmp_path):
        # ProjectContext with no server/ directory → server_dir is None
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert ctx.server_dir is None
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 9. validate_project walks cmd/*/main.go under server_dir ──────────


class TestValidateProjectWalks:
    def test_validate_project_finds_violations(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project, subdir="api")
        _write(
            path,
            """
            package main

            import "net/http"

            func main() {
                server := &http.Server{Addr: ":3000", Handler: nil}
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V36-NO-HTTP-TIMEOUTS"]
        assert len(errors) >= 1

    def test_validate_project_passes_hardened_file(self, validator, tmp_project, project_ctx):
        path = _main_go(tmp_project, subdir="worker")
        _write(
            path,
            """
            package main

            import (
                "context"
                "net/http"
                "os/signal"
                "syscall"
                "time"
                "os"
            )

            func main() {
                ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
                defer cancel()
                server := &http.Server{
                    Addr:              ":9000",
                    ReadHeaderTimeout: 5 * time.Second,
                    WriteTimeout:      60 * time.Second,
                }
                go func() { <-ctx.Done(); server.Shutdown(ctx) }()
                server.ListenAndServe()
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []
