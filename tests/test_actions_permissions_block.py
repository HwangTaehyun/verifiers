"""Tests for V41 — actions-permissions-block.

Covers:
  - V41-NO-PERMISSIONS-BLOCK — no top-level or job-level permissions (warning)
  - Top-level permissions (including empty deny-all) pass
  - All jobs with permissions pass
  - Partial job permissions warns
  - Multiple workflow files — only affected ones flagged
  - No workflows → no findings
  - Invalid YAML handled gracefully
  - validate_file (Tier 2) and validate_project (Tier 3) paths
  - Workflow with no jobs (defensive)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.actions_permissions_block import ActionsPermissionsBlockValidator


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> ActionsPermissionsBlockValidator:
    return ActionsPermissionsBlockValidator()


@pytest.fixture
def workflows_dir(tmp_project: Path) -> Path:
    """Create and return .github/workflows/ under tmp_project."""
    d = tmp_project / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. Top-level permissions present → pass ──────────────────────────────


class TestTopLevelPermissionsPasses:
    def test_top_level_permissions_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "name: CI\n"
            "on: [push]\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_top_level_empty_permissions_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        """permissions: {} (deny-all) at top level is an explicit declaration and should pass."""
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "name: CI\n"
            "on: [push]\n"
            "permissions: {}\n"
            "jobs:\n"
            "  lint:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Every job has job-level permissions → pass ────────────────────────


class TestEveryJobHasPermissionsPasses:
    def test_every_job_has_permissions_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "release.yml"
        wf.write_text(
            "name: Release\n"
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps:\n"
            "      - run: make build\n"
            "  publish:\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      contents: write\n"
            "      id-token: write\n"
            "    steps:\n"
            "      - run: make publish\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 3. No permissions anywhere → warn ───────────────────────────────────


class TestNoPermissionsAnywhereWarns:
    def test_no_permissions_anywhere_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "name: CI\n"
            "on: [push]\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: npm test\n"
        )
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V41-NO-PERMISSIONS-BLOCK"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        assert str(wf) == warnings[0].file


# ── 4. Partial job permissions → warn ────────────────────────────────────


class TestPartialJobPermissionsWarns:
    def test_partial_job_permissions_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        """Only some jobs have permissions — the one without should trigger the rule."""
        wf = workflows_dir / "build.yml"
        wf.write_text(
            "name: Build\n"
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps:\n"
            "      - run: make build\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: make test\n"
        )
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V41-NO-PERMISSIONS-BLOCK"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"


# ── 5. Multiple workflow files — flag only the one missing permissions ────


class TestMultipleWorkflowsPerFileCheck:
    def test_multiple_workflows_only_flag_missing(self, validator, tmp_project, workflows_dir, project_ctx):
        wf_good = workflows_dir / "ci.yml"
        wf_bad = workflows_dir / "deploy.yaml"
        wf_good.write_text(
            "name: CI\n"
            "on: [push]\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: npm test\n"
        )
        wf_bad.write_text(
            "name: Deploy\n"
            "on: [push]\n"
            "jobs:\n"
            "  deploy:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: make deploy\n"
        )
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V41-NO-PERMISSIONS-BLOCK"]
        assert len(warnings) == 1
        assert str(wf_bad) == warnings[0].file
        assert str(wf_good) not in {f.file for f in warnings}


# ── 6. No workflows → no findings ────────────────────────────────────────


class TestNoWorkflowsNoFindings:
    def test_no_workflows_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no .github/workflows directory
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. Invalid YAML handled gracefully ───────────────────────────────────


class TestInvalidYamlHandledGracefully:
    def test_invalid_yaml_handled_gracefully(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "broken.yml"
        wf.write_text("name: broken\n  invalid: yaml: content:\n    - [unclosed\n")
        # Should not raise; should return no findings for the broken file
        findings = validator.validate_project(project_ctx)
        # The broken file should produce no findings (graceful skip)
        assert all(f.file != str(wf) for f in findings)


# ── 8. validate_file (Tier 2) — only edited file scanned ─────────────────


class TestValidateFileSingleWorkflow:
    def test_validate_file_single_workflow(self, validator, tmp_project, workflows_dir, project_ctx):
        wf1 = workflows_dir / "ci.yml"
        wf2 = workflows_dir / "release.yaml"
        wf1.write_text(
            "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm test\n"
        )
        wf2.write_text(
            "name: Release\non: [push]\njobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make publish\n"
        )
        # Tier 2: only wf1 was "edited"
        findings = validator.validate_file(project_ctx, str(wf1))
        warnings = [f for f in findings if f.rule == "V41-NO-PERMISSIONS-BLOCK"]
        assert len(warnings) == 1
        assert str(wf1) == warnings[0].file
        # wf2 violations must NOT appear
        assert all(str(wf2) not in f.file for f in findings)

    def test_validate_file_missing_path_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_file(project_ctx, str(tmp_project / "nonexistent.yml"))
        assert findings == []


# ── 9. Workflow with no jobs — defensive ─────────────────────────────────


class TestWorkflowWithNoJobs:
    def test_workflow_with_no_jobs_no_findings(self, validator, tmp_project, workflows_dir, project_ctx):
        """A composite-only or reusable workflow may have no jobs key."""
        wf = workflows_dir / "composite.yml"
        wf.write_text(
            "name: Composite\n"
            "on:\n"
            "  workflow_call:\n"
            "    inputs:\n"
            "      name:\n"
            "        required: true\n"
            "        type: string\n"
        )
        # No jobs key — should not warn (nothing to scope)
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V41-NO-PERMISSIONS-BLOCK"]
        assert len(warnings) == 0
