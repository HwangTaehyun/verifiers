"""Tests for V64 — ts-layer-imports (detection mode, Phase 72).

Covers:
  - Non-TS project → no findings (no .ts files anywhere)
  - TS project + no depcruise config + no devDep → V64-NO-LAYER-CONFIG warning
  - TS project + .dependency-cruiser.cjs at root + script wired → no findings
  - TS project + config + no script → V64-DEPCRUISE-NOT-WIRED info
  - TS project + dependency-cruiser in devDeps but no config → V64-DEPCRUISE-NOT-WIRED info
  - TS project + eslint-plugin-boundaries in devDeps + no config → V64-DEPCRUISE-NOT-WIRED info
  - .dependency-cruiser.cjs in web/ also detected
"""

from __future__ import annotations

import json

import pytest

from hooks.validators.ts_layer_imports import TsLayerImportsValidator


@pytest.fixture
def validator() -> TsLayerImportsValidator:
    return TsLayerImportsValidator()


def _add_ts_file(tmp_project):
    """Add a .ts file so the validator considers this a TS project."""
    (tmp_project / "web" / "src" / "main.ts").write_text("export const x = 1;\n")


def _add_package_json(tmp_project, *, deps=None, scripts=None, where="web"):
    """Write a package.json with the given dev/deps and scripts."""
    pkg = {
        "name": "test-pkg",
        "version": "0.0.1",
        "devDependencies": deps or {},
        "scripts": scripts or {},
    }
    (tmp_project / where / "package.json").write_text(json.dumps(pkg))


# ── 1. Non-TS project ────────────────────────────────────────────────────────


class TestNonTsProject:
    def test_no_ts_files_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no .ts/.tsx files (web/src is empty)
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. TS project with nothing → V64-NO-LAYER-CONFIG ─────────────────────────


class TestTsProjectNoLayerEnforcement:
    def test_ts_project_no_config_warns(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        _add_package_json(tmp_project)  # no relevant deps
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V64-NO-LAYER-CONFIG"
        assert findings[0].severity == "warning"


# ── 3. TS project + depcruise config + script → no findings ──────────────────


class TestFullySetUpPasses:
    def test_config_at_root_with_script_passes(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        (tmp_project / ".dependency-cruiser.cjs").write_text("module.exports = {};")
        _add_package_json(
            tmp_project,
            deps={"dependency-cruiser": "^16.0.0"},
            scripts={"deps:check": "depcruise src"},
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_config_in_web_subdir_with_script_passes(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        (tmp_project / "web" / ".dependency-cruiser.cjs").write_text("module.exports = {};")
        _add_package_json(
            tmp_project,
            deps={"dependency-cruiser": "^16.0.0"},
            scripts={"deps:check": "depcruise src"},
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. TS project + config + no script → V64-DEPCRUISE-NOT-WIRED ─────────────


class TestConfigButNoScript:
    def test_config_no_script_info(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        (tmp_project / ".dependency-cruiser.cjs").write_text("module.exports = {};")
        _add_package_json(
            tmp_project,
            deps={"dependency-cruiser": "^16.0.0"},
            scripts={"build": "vite build"},  # no depcruise
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V64-DEPCRUISE-NOT-WIRED"
        assert findings[0].severity == "info"


# ── 5. TS project + dep but no config → V64-DEPCRUISE-NOT-WIRED ──────────────


class TestDepInstalledButNoConfig:
    def test_depcruise_in_deps_no_config_info(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        _add_package_json(
            tmp_project,
            deps={"dependency-cruiser": "^16.0.0"},
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V64-DEPCRUISE-NOT-WIRED"

    def test_eslint_boundaries_in_deps_no_config_info(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        _add_package_json(
            tmp_project,
            deps={"eslint-plugin-boundaries": "^4.0.0"},
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V64-DEPCRUISE-NOT-WIRED"


# ── 6. validate_file (Tier 2) — same logic ───────────────────────────────────


class TestValidateFileTier2:
    def test_validate_file_runs_full_check(self, validator, tmp_project, project_ctx):
        _add_ts_file(tmp_project)
        _add_package_json(tmp_project)
        f = tmp_project / "web" / "package.json"
        findings = validator.validate_file(project_ctx, str(f))
        assert len(findings) == 1
        assert findings[0].rule == "V64-NO-LAYER-CONFIG"
