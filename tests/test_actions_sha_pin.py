"""Tests for V40 — actions-sha-pin.

Covers:
  - V40-ACTION-NOT-PINNED      — third-party action uses floating tag (error)
  - V40-FIRST-PARTY-NOT-PINNED — actions/* uses floating tag (warning)
  - SHA-pinned actions pass cleanly
  - Local, Docker, and comment lines are skipped
  - validate_file (Tier 2) and validate_project (Tier 3) paths
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.actions_sha_pin import ActionsSHAPinValidator


# ── Fixtures ────────────────────────────────────────────────────────────

VALID_SHA = "a1b2c3d4e5f6789012345678901234567890abcd"
assert len(VALID_SHA) == 40


@pytest.fixture
def validator() -> ActionsSHAPinValidator:
    return ActionsSHAPinValidator()


@pytest.fixture
def workflows_dir(tmp_project: Path) -> Path:
    """Create and return .github/workflows/ under tmp_project."""
    d = tmp_project / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Helper ──────────────────────────────────────────────────────────────


def _write_workflow(path: Path, steps: str) -> None:
    path.write_text("on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n" + steps)


# ── 1. Third-party floating tag → error ─────────────────────────────────


class TestFloatingTagThirdParty:
    def test_third_party_errors(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      - uses: oven-sh/setup-bun@v1\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V40-ACTION-NOT-PINNED"]
        assert len(errors) == 1
        assert "oven-sh/setup-bun" in errors[0].message
        assert errors[0].severity == "error"

    def test_third_party_line_number_correct(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: oven-sh/setup-bun@v1\n"
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V40-ACTION-NOT-PINNED"]
        assert errors[0].line == 6


# ── 2. First-party floating tag → warning ───────────────────────────────


class TestFloatingTagFirstParty:
    def test_first_party_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      - uses: actions/checkout@v4\n")
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V40-FIRST-PARTY-NOT-PINNED"]
        assert len(warnings) == 1
        assert "actions/checkout" in warnings[0].message
        assert warnings[0].severity == "warning"

    def test_first_party_latest_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      - uses: actions/cache@latest\n")
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V40-FIRST-PARTY-NOT-PINNED"]
        assert len(warnings) == 1
        assert "latest" in warnings[0].message


# ── 3. SHA-pinned action passes ─────────────────────────────────────────


class TestShaPinPasses:
    def test_sha_pin_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, f"      - uses: oven-sh/setup-bun@{VALID_SHA}\n")
        findings = validator.validate_project(project_ctx)
        pinned = [f for f in findings if f.rule in ("V40-ACTION-NOT-PINNED", "V40-FIRST-PARTY-NOT-PINNED")]
        assert pinned == []

    def test_sha_pin_with_comment_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(
            wf,
            f"      - uses: actions/checkout@{VALID_SHA}  # v4\n",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. Local action skipped ─────────────────────────────────────────────


class TestLocalActionSkipped:
    def test_local_action_skipped(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      - uses: ./.github/actions/foo\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Docker action skipped ─────────────────────────────────────────────


class TestDockerActionSkipped:
    def test_docker_action_skipped(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      - uses: docker://nginx:latest\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. Comment line skipped ─────────────────────────────────────────────


class TestCommentLineSkipped:
    def test_comment_line_skipped(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "      # - uses: actions/checkout@v4\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. Multiple workflow files — all violations flagged ──────────────────


class TestMultipleWorkflowFiles:
    def test_multiple_files_all_flagged(self, validator, tmp_project, workflows_dir, project_ctx):
        wf1 = workflows_dir / "ci.yml"
        wf2 = workflows_dir / "release.yaml"
        _write_workflow(wf1, "      - uses: oven-sh/setup-bun@v1\n")
        _write_workflow(wf2, "      - uses: gitleaks/gitleaks-action@v2\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V40-ACTION-NOT-PINNED"]
        assert len(errors) == 2
        files_found = {f.file for f in errors}
        assert str(wf1) in files_found
        assert str(wf2) in files_found


# ── 8. No workflows dir → no findings ───────────────────────────────────


class TestNoWorkflowsDirNoFindings:
    def test_no_workflows_dir_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no .github/workflows directory
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. validate_file (Tier 2) — only edited file scanned ─────────────────


class TestValidateFileSingleWorkflow:
    def test_validate_file_single_workflow(self, validator, tmp_project, workflows_dir, project_ctx):
        wf1 = workflows_dir / "ci.yml"
        wf2 = workflows_dir / "release.yaml"
        _write_workflow(wf1, "      - uses: oven-sh/setup-bun@v1\n")
        _write_workflow(wf2, "      - uses: gitleaks/gitleaks-action@v2\n")

        # Tier 2: only wf1 was "edited"
        findings = validator.validate_file(project_ctx, str(wf1))
        errors = [f for f in findings if f.rule == "V40-ACTION-NOT-PINNED"]
        assert len(errors) == 1
        assert "oven-sh/setup-bun" in errors[0].message
        # wf2 violations must NOT appear
        assert all("gitleaks" not in f.message for f in findings)

    def test_validate_file_missing_path_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_file(project_ctx, str(tmp_project / "nonexistent.yml"))
        assert findings == []
