"""Tests for V23 — buf governance.

Covers:
  - V23-LOCK-DRIFT — buf.yaml ↔ buf.lock dep set diff
  - V23-PROTOVALIDATE-MISSING — required-looking field with no validation
  - V23-BREAKING-* — buf breaking (subprocess; gracefully no-ops when buf is absent)
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.buf_governance import BufGovernanceValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> BufGovernanceValidator:
    return BufGovernanceValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal layout: project root with .git + server/ subdirectory."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server").mkdir()
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write(path: Path, body: str) -> None:
    """Write body with leading whitespace stripped per-line so triple-quoted
    test fixtures don't break YAML / proto indentation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


# ── 1. _find_buf_dir / no-buf short-circuit ──────────────────────────


class TestNoBuf:
    def test_no_bufyaml_returns_empty(self, validator, ctx):
        # Project has no buf.yaml anywhere → V23 emits zero findings,
        # zero subprocess calls.
        with patch("subprocess.run", side_effect=AssertionError("must not call subprocess")):
            findings = validator.validate_project(ctx)
        assert findings == []


# ── 2. V23-LOCK-DRIFT — declared vs locked deps ──────────────────────


class TestLockDrift:
    def test_no_deps_no_findings(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")
        with patch("subprocess.run") as mocked:
            mocked.return_value.returncode = 1  # buf breaking absent → no extra findings
            findings = validator.validate_project(ctx)
        drift = [f for f in findings if f.rule == "V23-LOCK-DRIFT"]
        assert drift == []

    def test_missing_lock_warns(self, validator, repo, ctx):
        _write(
            repo / "server" / "buf.yaml",
            """
            version: v2
            modules: [{path: proto}]
            deps:
              - buf.build/bufbuild/protovalidate
            """,
        )
        # No buf.lock file
        findings = validator.validate_file(ctx, str(repo / "server" / "buf.yaml"))
        drift = [f for f in findings if f.rule == "V23-LOCK-DRIFT"]
        assert any("buf.lock is missing" in f.message for f in drift)

    def test_dep_in_yaml_not_in_lock(self, validator, repo, ctx):
        _write(
            repo / "server" / "buf.yaml",
            """
            version: v2
            modules: [{path: proto}]
            deps:
              - buf.build/bufbuild/protovalidate
              - buf.build/googleapis/googleapis
            """,
        )
        _write(
            repo / "server" / "buf.lock",
            """
            version: v2
            deps:
              - name: buf.build/googleapis/googleapis
                commit: abc123
            """,
        )
        findings = validator.validate_file(ctx, str(repo / "server" / "buf.yaml"))
        drift = [f for f in findings if f.rule == "V23-LOCK-DRIFT"]
        assert len(drift) == 1
        assert "protovalidate" in drift[0].message

    def test_stale_dep_in_lock(self, validator, repo, ctx):
        _write(
            repo / "server" / "buf.yaml",
            """
            version: v2
            modules: [{path: proto}]
            deps:
              - buf.build/bufbuild/protovalidate
            """,
        )
        _write(
            repo / "server" / "buf.lock",
            """
            version: v2
            deps:
              - name: buf.build/bufbuild/protovalidate
                commit: abc123
              - name: buf.build/old/removed
                commit: def456
            """,
        )
        findings = validator.validate_file(ctx, str(repo / "server" / "buf.yaml"))
        drift = [f for f in findings if f.rule == "V23-LOCK-DRIFT"]
        assert any("buf.build/old/removed" in f.message for f in drift)
        assert any("no longer declared" in f.message for f in drift)

    def test_in_sync_no_findings(self, validator, repo, ctx):
        _write(
            repo / "server" / "buf.yaml",
            """
            version: v2
            modules: [{path: proto}]
            deps:
              - buf.build/bufbuild/protovalidate
            """,
        )
        _write(
            repo / "server" / "buf.lock",
            """
            version: v2
            deps:
              - name: buf.build/bufbuild/protovalidate
                commit: abc123
            """,
        )
        findings = validator.validate_file(ctx, str(repo / "server" / "buf.yaml"))
        drift = [f for f in findings if f.rule == "V23-LOCK-DRIFT"]
        assert drift == []


# ── 3. V23-PROTOVALIDATE-MISSING ─────────────────────────────────────


class TestProtovalidate:
    def test_required_hint_field_without_rule_warns(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            syntax = "proto3";
            package users.v1;
            message CreateUser {
              string id = 1;
              string email = 2;
              string name = 3;
            }
            """,
        )
        findings = validator.validate_project(ctx)
        pv = [f for f in findings if f.rule == "V23-PROTOVALIDATE-MISSING"]
        # id, email, name are all in _REQUIRED_HINT_NAMES → 3 findings
        assert len(pv) == 3
        assert any("id" in f.message for f in pv)
        assert any("email" in f.message for f in pv)

    def test_field_with_validation_passes(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            syntax = "proto3";
            import "buf/validate/validate.proto";
            message CreateUser {
              string id = 1 [(buf.validate.field).required = true];
              string email = 2 [(buf.validate.field).string.email = true];
            }
            """,
        )
        findings = validator.validate_project(ctx)
        pv = [f for f in findings if f.rule == "V23-PROTOVALIDATE-MISSING"]
        assert pv == []

    def test_legacy_validate_rules_also_pass(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            message CreateUser {
              string id = 1 [(validate.rules).string.min_len = 1];
            }
            """,
        )
        findings = validator.validate_project(ctx)
        pv = [f for f in findings if f.rule == "V23-PROTOVALIDATE-MISSING"]
        assert pv == []

    def test_non_hint_field_ignored(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")
        _write(
            repo / "server" / "proto" / "users.proto",
            """
            message Detail {
              string description = 1;
              string note = 2;
            }
            """,
        )
        findings = validator.validate_project(ctx)
        pv = [f for f in findings if f.rule == "V23-PROTOVALIDATE-MISSING"]
        # description, note aren't in the hint list — not flagged
        assert pv == []


# ── 4. V23-BREAKING — graceful when buf is absent ────────────────────


class TestBreaking:
    def test_buf_not_installed_returns_empty(self, validator, repo, ctx):
        _write(repo / "server" / "buf.yaml", "version: v2\nmodules: [{path: proto}]\n")

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "git":
                # Pretend git resolution succeeds
                from unittest.mock import MagicMock

                m = MagicMock(returncode=0)
                m.stdout = ".git\n"
                return m
            if cmd[0] == "buf":
                raise FileNotFoundError("buf not on PATH")
            from unittest.mock import MagicMock

            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            findings = validator.validate_project(ctx)
        # Should have logged but emitted no V23-BREAKING findings
        assert all(not f.rule.startswith("V23-BREAKING-") for f in findings)


# ── 4. V23-TS-NOCHECK — buf.gen.yaml plugin opt enforcement (Phase 71+) ──


class TestTsNocheck:
    """Phase 71 follow-up: enforce ``ts_nocheck=false`` on TS-targeting
    Connect-RPC plugins so generated code is part of strict-TS checking
    instead of silently bypassed via ``// @ts-nocheck`` headers."""

    def _write_buf_gen(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip())

    def test_ts_nocheck_false_no_finding(self, validator, repo, ctx):
        """Explicit ``ts_nocheck=false`` → no finding."""
        self._write_buf_gen(
            repo / "web" / "buf.gen.yaml",
            """
            version: v2
            plugins:
              - remote: buf.build/bufbuild/es:v1.10.0
                out: src/api/connectRpc/gen
                opt:
                  - target=ts
                  - import_extension=none
                  - ts_nocheck=false
            """,
        )
        findings = validator.validate_project(ctx)
        assert all(not f.rule.startswith("V23-TS-NOCHECK") for f in findings)

    def test_ts_nocheck_missing_emits_finding(self, validator, repo, ctx):
        """No ``ts_nocheck`` opt at all → V23-TS-NOCHECK-MISSING."""
        self._write_buf_gen(
            repo / "web" / "buf.gen.yaml",
            """
            version: v2
            plugins:
              - remote: buf.build/bufbuild/es:v1.10.0
                out: src/api/connectRpc/gen
                opt:
                  - target=ts
                  - import_extension=none
            """,
        )
        findings = validator.validate_project(ctx)
        nocheck = [f for f in findings if f.rule == "V23-TS-NOCHECK-MISSING"]
        assert len(nocheck) == 1
        assert "ts_nocheck=false" in nocheck[0].fix

    def test_ts_nocheck_true_emits_finding(self, validator, repo, ctx):
        """Explicit ``ts_nocheck=true`` → V23-TS-NOCHECK-ENABLED."""
        self._write_buf_gen(
            repo / "web" / "buf.gen.yaml",
            """
            version: v2
            plugins:
              - remote: buf.build/bufbuild/es:v1.10.0
                out: src/api/connectRpc/gen
                opt:
                  - target=ts
                  - ts_nocheck=true
            """,
        )
        findings = validator.validate_project(ctx)
        nocheck = [f for f in findings if f.rule == "V23-TS-NOCHECK-ENABLED"]
        assert len(nocheck) == 1

    def test_non_ts_plugin_ignored(self, validator, repo, ctx):
        """Plugins not targeting TS (e.g. Go) are not flagged."""
        self._write_buf_gen(
            repo / "server" / "buf.gen.yaml",
            """
            version: v2
            plugins:
              - remote: buf.build/protocolbuffers/go:v1.34.0
                out: gen/go
                opt:
                  - paths=source_relative
            """,
        )
        findings = validator.validate_project(ctx)
        assert all(not f.rule.startswith("V23-TS-NOCHECK") for f in findings)

    def test_connectrpc_es_plugin_also_checked(self, validator, repo, ctx):
        """``connectrpc/es`` plugin is also a TS target — same rule applies."""
        self._write_buf_gen(
            repo / "web" / "buf.gen.yaml",
            """
            version: v2
            plugins:
              - remote: buf.build/connectrpc/es:v1.6.1
                out: src/api/connectRpc/gen
                opt:
                  - target=ts
            """,
        )
        findings = validator.validate_project(ctx)
        nocheck = [f for f in findings if f.rule.startswith("V23-TS-NOCHECK")]
        assert len(nocheck) == 1
