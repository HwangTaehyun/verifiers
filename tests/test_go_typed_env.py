"""Tests for V62 — go-typed-env (Phase 73)."""

from __future__ import annotations

import pytest
import yaml

from hooks.validators.go_typed_env import GoTypedEnvValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> GoTypedEnvValidator:
    return GoTypedEnvValidator()


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


# ── 1. Allowed dirs (default: internal/config, cmd) → no findings ────────────


class TestAllowedDirsPass:
    def test_internal_config_can_read_env(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "config" / "config.go"
        _write(
            f,
            'package config\n'
            'import "os"\n'
            'func Load() string { return os.Getenv("APP_DB_URL") }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_cmd_main_can_read_env(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "cmd" / "api" / "main.go"
        _write(
            f,
            'package main\n'
            'import "os"\n'
            'func main() { _ = os.Getenv("APP_PORT") }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Forbidden locations → V62-DIRECT-ENV ──────────────────────────────────


class TestForbiddenLocationsFlagged:
    def test_handler_direct_env_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "os"\n'
            'func H() string { return os.Getenv("APP_API_KEY") }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V62-DIRECT-ENV"
        assert findings[0].severity == "warning"
        assert "APP_API_KEY" in findings[0].message

    def test_lookupenv_also_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "service" / "x.go"
        _write(
            f,
            'package service\n'
            'import "os"\n'
            'func S() { v, _ := os.LookupEnv("APP_X"); _ = v }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V62-DIRECT-ENV"


# ── 3. Escape hatch ──────────────────────────────────────────────────────────


class TestEscapeHatch:
    def test_env_direct_ok_silences(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "service" / "x.go"
        _write(
            f,
            'package service\n'
            'import "os"\n'
            'func S() string {\n'
            '    return os.Getenv("APP_X") // verifier:env-direct-ok bootstrap\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. Test files skipped ────────────────────────────────────────────────────


class TestTestFilesSkipped:
    def test_test_file_skipped(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "x_test.go"
        _write(
            f,
            'package internal\n'
            'import "os"\n'
            'func TestX(t *testing.T) { _ = os.Getenv("APP_X") }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Custom config_dirs override ───────────────────────────────────────────


class TestCustomConfigDirs:
    def test_custom_dirs_in_config(self, validator, tmp_project):
        cfg_dir = tmp_project / ".verifiers"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump({"go": {"config_dirs": ["pkg/settings"]}})
        )
        ctx = ProjectContext(tmp_project)

        # Inside custom allowed dir → no finding
        f1 = tmp_project / "server" / "pkg" / "settings" / "load.go"
        _write(f1, 'package settings\nimport "os"\nfunc L() { _ = os.Getenv("X") }\n')
        # Default allowed (internal/config) is now overridden, so this fires
        f2 = tmp_project / "server" / "internal" / "config" / "config.go"
        _write(f2, 'package config\nimport "os"\nfunc C() { _ = os.Getenv("Y") }\n')

        findings = validator.validate_project(ctx)
        # f1 silenced (in custom dir), f2 flagged (no longer in allowlist)
        assert len(findings) == 1
        assert findings[0].file == str(f2)


# ── 6. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_target(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "handlers" / "x.go"
        _write(
            f,
            'package handlers\n'
            'import "os"\n'
            'func H() { _ = os.Getenv("X") }\n',
        )
        findings = validator.validate_file(project_ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V62-DIRECT-ENV"


# ── 7. Multiple violations in same file ──────────────────────────────────────


class TestMultipleViolations:
    def test_multiple_getenv_each_flagged(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "h.go"
        _write(
            f,
            'package h\n'
            'import "os"\n'
            'func F() {\n'
            '    a := os.Getenv("A")\n'
            '    b := os.Getenv("B")\n'
            '    _, _ = a, b\n'
            '}\n',
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 2
        names = sorted(f.message for f in findings)
        assert any("\"A\"" in n for n in names)
        assert any("\"B\"" in n for n in names)
