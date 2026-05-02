"""Tests for V60 — go-layer-imports (Phase 72).

Covers:
  - No config → no findings (no-op for unconfigured projects)
  - Allowed direction: handler → service ✓
  - Allowed direction: service → repo ✓
  - Forbidden: handler → repo (skip service) ✗
  - Forbidden: repo → handler (reverse) ✗
  - Same-layer imports always allowed (handler → handler)
  - External imports unconstrained (3rd party deps)
  - Escape hatch via line comment
  - Test files (_test.go) skipped
  - validate_file (Tier 2) vs validate_project (Tier 3)
  - Block import syntax handled
"""

from __future__ import annotations

import pytest
import yaml

from hooks.validators.go_layer_imports import GoLayerImportsValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> GoLayerImportsValidator:
    return GoLayerImportsValidator()


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _config_layered(tmp_project):
    """Write standard handler→service→repo layered config + return ctx."""
    cfg_dir = tmp_project / ".verifiers"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "go": {
                    "layers": {
                        "handlers": "internal/handlers",
                        "services": "internal/services",
                        "repos": "internal/repos",
                    },
                    "allowed_imports": {
                        "handlers": ["services"],
                        "services": ["repos"],
                        "repos": [],
                    },
                }
            }
        )
    )
    return ProjectContext(tmp_project)


# ── 1. No config → no findings ───────────────────────────────────────────────


class TestNoConfigNoOp:
    def test_no_config_returns_empty(self, validator, tmp_project, project_ctx):
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/repos/user"\n'
            'func H() { _ = user.Get }\n',
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []  # no go.layers config → no-op


# ── 2. Allowed directions ────────────────────────────────────────────────────


class TestAllowedDirectionsPass:
    def test_handler_imports_service_passes(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/services/user"\n'
            'func H() { _ = user.Get }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []

    def test_service_imports_repo_passes(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "services" / "user.go"
        _write(
            f,
            'package services\n'
            'import "myapp/internal/repos/user"\n'
            'func S() { _ = user.Find }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 3. Forbidden directions ──────────────────────────────────────────────────


class TestForbiddenDirectionsFlagged:
    def test_handler_skips_service_to_repo_flagged(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/repos/user"\n'
            'func H() { _ = user.Find }\n',
        )
        findings = validator.validate_project(ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V60-LAYER-SKIP"
        assert "handlers" in findings[0].message
        assert "repos" in findings[0].message

    def test_repo_imports_handler_reverse_flagged(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "repos" / "user.go"
        _write(
            f,
            'package repos\n'
            'import "myapp/internal/handlers/user"\n'
            'func R() { _ = user.H }\n',
        )
        findings = validator.validate_project(ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V60-LAYER-SKIP"


# ── 4. Same-layer always allowed ─────────────────────────────────────────────


class TestSameLayerAllowed:
    def test_handler_imports_handler_allowed(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/handlers/admin"\n'
            'func H() { _ = admin.G }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 5. External imports unconstrained ────────────────────────────────────────


class TestExternalImportsUnconstrained:
    def test_third_party_import_passes(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import (\n'
            '    "github.com/jmoiron/sqlx"\n'
            '    "myapp/internal/services/user"\n'
            ')\n'
            'func H() { _ = sqlx.DB{}; _ = user.Get }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 6. Block import syntax ───────────────────────────────────────────────────


class TestBlockImportSyntax:
    def test_block_import_one_violation(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            '\n'
            'import (\n'
            '    "fmt"\n'
            '    "myapp/internal/services/user"\n'
            '    "myapp/internal/repos/user"\n'
            ')\n'
            'func H() { fmt.Println("x") }\n',
        )
        findings = validator.validate_project(ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V60-LAYER-SKIP"
        # Line 6 = the repos import
        assert findings[0].line == 6


# ── 7. Escape hatch ──────────────────────────────────────────────────────────


class TestEscapeHatch:
    def test_layer_skip_ok_silences(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/repos/user" // verifier:layer-skip-ok read-only optimization\n'
            'func H() { _ = user.Find }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 8. Test files skipped ────────────────────────────────────────────────────


class TestTestFilesSkipped:
    def test_test_file_skipped(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user_test.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/repos/user"\n'
            'func TestH(t *testing.T) { _ = user.Find }\n',
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 9. validate_file (Tier 2) ────────────────────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_only_scans_target(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        f = tmp_project / "server" / "internal" / "handlers" / "user.go"
        _write(
            f,
            'package handlers\n'
            'import "myapp/internal/repos/user"\n'
            'func H() { _ = user.Find }\n',
        )
        findings = validator.validate_file(ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V60-LAYER-SKIP"


# ── 10. File outside any layer = unconstrained ───────────────────────────────


class TestFileOutsideLayerUnconstrained:
    def test_cmd_main_imports_repo_passes(self, validator, tmp_project):
        ctx = _config_layered(tmp_project)
        # cmd/api/main.go is not in any layer (no internal/handlers etc.)
        f = tmp_project / "server" / "cmd" / "api" / "main.go"
        _write(
            f,
            'package main\n'
            'import (\n'
            '    "myapp/internal/handlers"\n'
            '    "myapp/internal/repos"\n'
            ')\n'
            'func main() { _ = handlers.New(); _ = repos.New() }\n',
        )
        findings = validator.validate_project(ctx)
        # cmd/api/main.go is not classified into any layer → wiring is allowed
        assert findings == []
