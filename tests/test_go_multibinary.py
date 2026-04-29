"""Tests for V25 — Go multi-binary discipline.

Covers:
  - V25-NO-GRACEFUL-SHUTDOWN — main.go missing SIGTERM handler
  - V25-NO-TOOLS-FILE / V25-TOOLS-NO-BUILD-TAG — tools.go conventions
  - V25-AIR-DEAD-PATH / V25-CMD-NO-AIR-CONFIG — air mapping
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.go_multibinary import GoMultiBinaryValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoMultiBinaryValidator:
    return GoMultiBinaryValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """server/cmd/<name>/main.go layout matching the user's monorepo."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server" / "cmd").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


# ── 1. No-cmd short-circuit ─────────────────────────────────────────


class TestNoCmd:
    def test_no_server_dir_returns_empty(self, validator, tmp_path):
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert validator.validate_project(ctx) == []

    def test_empty_cmd_dir_returns_empty(self, validator, repo, ctx):
        # cmd/ exists but no subdirs with main.go
        assert validator.validate_project(ctx) == []


# ── 2. V25-NO-GRACEFUL-SHUTDOWN ─────────────────────────────────────


class TestGracefulShutdown:
    def test_main_with_signal_notify_passes(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "server" / "main.go",
            """
            package main
            import (
                "context"
                "os/signal"
                "syscall"
            )
            func main() {
                ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
                defer cancel()
                _ = ctx
            }
            """,
        )
        # Also create .air.toml so V25-CMD-NO-AIR-CONFIG doesn't fire
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        # tools.go to silence V25-NO-TOOLS-FILE
        _write(repo / "server" / "tools.go", "//go:build tools\n\npackage tools\n")
        findings = validator.validate_project(ctx)
        gs = [f for f in findings if f.rule == "V25-NO-GRACEFUL-SHUTDOWN"]
        assert gs == []

    def test_main_with_signal_notify_legacy_form_passes(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "worker" / "main.go",
            """
            package main
            import (
                "os"
                "os/signal"
                "syscall"
            )
            func main() {
                sig := make(chan os.Signal, 1)
                signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
                <-sig
            }
            """,
        )
        _write(repo / "server" / ".air.worker.toml", 'cmd = "go build -o ./tmp/worker ./cmd/worker"\n')
        _write(repo / "server" / "tools.go", "//go:build tools\n\npackage tools\n")
        findings = validator.validate_project(ctx)
        gs = [f for f in findings if f.rule == "V25-NO-GRACEFUL-SHUTDOWN"]
        assert gs == []

    def test_main_without_signal_warns(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "worker" / "main.go",
            """
            package main
            import "fmt"
            func main() {
                fmt.Println("hello")
            }
            """,
        )
        _write(repo / "server" / ".air.worker.toml", 'cmd = "go build -o ./tmp/worker ./cmd/worker"\n')
        _write(repo / "server" / "tools.go", "//go:build tools\n\npackage tools\n")
        findings = validator.validate_project(ctx)
        gs = [f for f in findings if f.rule == "V25-NO-GRACEFUL-SHUTDOWN"]
        assert len(gs) == 1
        assert "cmd/worker/main.go" in gs[0].file


# ── 3. tools.go presence + build tag ────────────────────────────────


class TestToolsGo:
    def test_no_tools_file_warns(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "server" / "main.go",
            'package main\nimport ("context"; "os/signal")\nfunc main(){signal.NotifyContext(context.Background())}',
        )
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        # No tools.go
        findings = validator.validate_project(ctx)
        tg = [f for f in findings if f.rule == "V25-NO-TOOLS-FILE"]
        assert len(tg) == 1

    def test_tools_no_build_tag_warns(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "server" / "main.go",
            'package main\nimport ("context"; "os/signal")\nfunc main(){signal.NotifyContext(context.Background())}',
        )
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        _write(
            repo / "server" / "tools.go",
            """
            // Tools file but no build tag
            package tools
            import _ "github.com/Khan/genqlient"
            """,
        )
        findings = validator.validate_project(ctx)
        tg = [f for f in findings if f.rule == "V25-TOOLS-NO-BUILD-TAG"]
        assert len(tg) == 1

    def test_tools_with_new_build_tag_passes(self, validator, repo, ctx):
        _write(
            repo / "server" / "cmd" / "server" / "main.go",
            'package main\nimport ("context"; "os/signal")\nfunc main(){signal.NotifyContext(context.Background())}',
        )
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        _write(
            repo / "server" / "tools.go",
            """
            //go:build tools
            // +build tools

            package tools
            import _ "github.com/Khan/genqlient"
            """,
        )
        findings = validator.validate_project(ctx)
        tg = [f for f in findings if f.rule.startswith("V25-TOOLS-") or f.rule == "V25-NO-TOOLS-FILE"]
        assert tg == []


# ── 4. air mapping ──────────────────────────────────────────────────


class TestAirMapping:
    def _seed_passing_main(self, repo, name="server"):
        _write(
            repo / "server" / "cmd" / name / "main.go",
            'package main\nimport ("context"; "os/signal")\nfunc main(){signal.NotifyContext(context.Background())}',
        )
        _write(repo / "server" / "tools.go", "//go:build tools\n\npackage tools\n")

    def test_air_pointing_at_dead_cmd_warns(self, validator, repo, ctx):
        self._seed_passing_main(repo, "server")
        # Air config refers to a non-existent cmd/legacy
        _write(repo / "server" / ".air.legacy.toml", 'cmd = "go build -o ./tmp/legacy ./cmd/legacy"\n')
        # And the canonical .air.toml so cmd/server has its config
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        findings = validator.validate_project(ctx)
        dp = [f for f in findings if f.rule == "V25-AIR-DEAD-PATH"]
        assert len(dp) == 1
        assert "legacy" in dp[0].message

    def test_cmd_without_air_config_warns(self, validator, repo, ctx):
        # Two cmds, only one has air config
        self._seed_passing_main(repo, "server")
        self._seed_passing_main(repo, "worker")
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        # No .air.worker.toml
        findings = validator.validate_project(ctx)
        no_air = [f for f in findings if f.rule == "V25-CMD-NO-AIR-CONFIG"]
        assert len(no_air) == 1
        assert "cmd/worker" in no_air[0].message

    def test_canonical_server_covered_by_bare_air_toml(self, validator, repo, ctx):
        # cmd/server is the canonical "server" binary; .air.toml (bare)
        # is allowed to cover it without forcing .air.server.toml
        self._seed_passing_main(repo, "server")
        _write(repo / "server" / ".air.toml", 'cmd = "go build -o ./tmp/server ./cmd/server"\n')
        findings = validator.validate_project(ctx)
        no_air = [f for f in findings if f.rule == "V25-CMD-NO-AIR-CONFIG"]
        assert no_air == []

    def test_no_air_configs_no_air_findings(self, validator, repo, ctx):
        # Project doesn't use Air at all → no air-related findings
        self._seed_passing_main(repo, "server")
        findings = validator.validate_project(ctx)
        assert all(not f.rule.startswith("V25-AIR-") and f.rule != "V25-CMD-NO-AIR-CONFIG" for f in findings)
