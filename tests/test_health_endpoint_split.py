"""Tests for V50 — Health endpoint split (livez/readyz)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.health_endpoint_split import HealthEndpointSplitValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> HealthEndpointSplitValidator:
    return HealthEndpointSplitValidator()


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Minimal project root with a .git marker."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def project_ctx(tmp_project: Path) -> ProjectContext:
    return ProjectContext(tmp_project)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


# ── Tests ────────────────────────────────────────────────────────────────


class TestBothRoutesRegistered:
    def test_both_routes_registered_passes(self, validator, tmp_project, project_ctx):
        """main.go registers both /livez and /readyz — no findings."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import (
                "net/http"
                "github.com/jackc/pgx/v5"
            )

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
                    if err := db.Ping(r.Context()); err != nil {
                        w.WriteHeader(503)
                        return
                    }
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


class TestOnlyHealthRouteErrors:
    def test_only_health_route_errors(self, validator, tmp_project, project_ctx):
        """Only /health registered (not split) — V50-HEALTH-NOT-SPLIT fired."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V50-HEALTH-NOT-SPLIT" in rules


class TestOnlyLivezNoReadyz:
    def test_only_livez_no_readyz_errors(self, validator, tmp_project, project_ctx):
        """/livez exists but /readyz is absent — V50-HEALTH-NOT-SPLIT fired."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V50-HEALTH-NOT-SPLIT" in rules


class TestRoutesInSeparateFiles:
    def test_routes_in_separate_files_combined(self, validator, tmp_project, project_ctx):
        """/livez in main.go and /readyz in handlers.go — aggregated, no findings."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/livez", livenessHandler)
                registerReadyz(mux)
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        _write(
            tmp_project / "server" / "cmd" / "server" / "handlers.go",
            """
            package main

            import (
                "net/http"
                "github.com/jackc/pgx/v5"
            )

            func registerReadyz(mux *http.ServeMux) {
                mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
                    if err := db.Ping(r.Context()); err != nil {
                        w.WriteHeader(503)
                        return
                    }
                    w.WriteHeader(200)
                })
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        split_errors = [f for f in findings if f.rule == "V50-HEALTH-NOT-SPLIT"]
        assert split_errors == []


class TestNoCmdDir:
    def test_no_cmd_dir_returns_empty(self, validator, tmp_project, project_ctx):
        """No cmd/ directory anywhere — V50 does not apply, returns empty."""
        # Ensure there is no cmd/ directory
        findings = validator.validate_project(project_ctx)
        assert findings == []


class TestNoHttpRoutes:
    def test_no_http_routes_returns_empty(self, validator, tmp_project, project_ctx):
        """cmd/worker/main.go with no HTTP route registrations — background job, skip."""
        _write(
            tmp_project / "server" / "cmd" / "worker" / "main.go",
            """
            package main

            import (
                "context"
                "log"
            )

            func main() {
                ctx := context.Background()
                log.Println("worker started")
                runWorker(ctx)
            }

            func runWorker(ctx context.Context) {
                // pull from queue, no HTTP server
                for {
                    processJob(ctx)
                }
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


class TestReadyzWithoutDbPing:
    def test_readyz_without_db_ping_warns(self, validator, tmp_project, project_ctx):
        """/readyz registered but no DB import or Ping call — V50-READYZ-NO-DB-PING."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
                    // always 200, no DB check
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V50-READYZ-NO-DB-PING" in rules
        assert "V50-HEALTH-NOT-SPLIT" not in rules


class TestReadyzWithDbPing:
    def test_readyz_with_db_ping_passes(self, validator, tmp_project, project_ctx):
        """/readyz handler imports pgx and calls Ping — no V50-READYZ-NO-DB-PING."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import (
                "net/http"
                "github.com/jackc/pgx/v5/pgxpool"
            )

            var pool *pgxpool.Pool

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
                    if err := pool.Ping(r.Context()); err != nil {
                        w.WriteHeader(503)
                        return
                    }
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        findings = validator.validate_project(project_ctx)
        db_ping_warnings = [f for f in findings if f.rule == "V50-READYZ-NO-DB-PING"]
        assert db_ping_warnings == []


class TestValidateFileTrigger:
    def test_validate_file_runs_project_check(self, validator, tmp_project, project_ctx):
        """Tier 2: validate_file on a cmd/**/*.go triggers the project-wide check."""
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            """
            package main

            import "net/http"

            func main() {
                mux := http.NewServeMux()
                mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
                    w.WriteHeader(200)
                })
                http.ListenAndServe(":8080", mux)
            }
            """,
        )
        file_path = str(tmp_project / "server" / "cmd" / "server" / "main.go")
        findings = validator.validate_file(project_ctx, file_path)
        rules = [f.rule for f in findings]
        assert "V50-HEALTH-NOT-SPLIT" in rules
