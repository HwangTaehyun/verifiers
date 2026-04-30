"""Tests for V37 — go-test-race-coverage.

Covers:
  - V37-CI-NO-RACE          — go test lacks -race flag (error)
  - V37-CI-NO-COVERAGE-GATE — go test runs but no coverage gate (warning)
  - Full race + coverage passes cleanly
  - Makefile and justfile race detection
  - Comment lines skipped
  - validate_file (Tier 2) and validate_project (Tier 3) paths
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.go_test_race_coverage import GoTestRaceCoverageValidator


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GoTestRaceCoverageValidator:
    return GoTestRaceCoverageValidator()


@pytest.fixture
def workflows_dir(tmp_project: Path) -> Path:
    d = tmp_project / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Helpers ───────────────────────────────────────────────────────────────


def _write_workflow(path: Path, run_cmd: str, extra_steps: str = "") -> None:
    """Write a minimal workflow YAML with a single run step."""
    path.write_text(
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v3\n"
        f"      - name: Test\n"
        f"        run: {run_cmd}\n" + extra_steps
    )


def _write_workflow_with_upload(path: Path, run_cmd: str, upload_uses: str) -> None:
    """Write a workflow with a go test step followed by an upload/codecov step."""
    path.write_text(
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v3\n"
        f"      - name: Test\n"
        f"        run: {run_cmd}\n"
        f"      - name: Upload\n"
        f"        uses: {upload_uses}\n"
    )


# ── 1. go test -race ./... passes ────────────────────────────────────────


class TestGoTestWithRacePasses:
    def test_go_test_with_race_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow_with_upload(
            wf,
            "go test -race -coverprofile=coverage.out ./...",
            "actions/upload-artifact@v3",
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert errors == []


# ── 2. go test ./... → V37-CI-NO-RACE ───────────────────────────────────


class TestGoTestNoRaceErrors:
    def test_go_test_no_race_errors(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "go test ./...")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert "-race" in errors[0].fix


# ── 3. Makefile go test without -race → V37-CI-NO-RACE ──────────────────


class TestMakefileNoRaceErrors:
    def test_makefile_no_race_errors(self, validator, tmp_project, project_ctx):
        makefile = tmp_project / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\tgo test ./...\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert str(makefile) == errors[0].file

    def test_makefile_with_race_passes(self, validator, tmp_project, project_ctx):
        makefile = tmp_project / "Makefile"
        makefile.write_text(".PHONY: test\ntest:\n\tgo test -race ./...\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert errors == []


# ── 4. justfile go test without -race → V37-CI-NO-RACE ──────────────────


class TestJustfileNoRaceErrors:
    def test_justfile_no_race_errors(self, validator, tmp_project, project_ctx):
        justfile = tmp_project / "justfile"
        justfile.write_text("@test:\n    go test ./...\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert len(errors) == 1
        assert errors[0].severity == "error"
        assert str(justfile) == errors[0].file

    def test_justfile_with_race_passes(self, validator, tmp_project, project_ctx):
        justfile = tmp_project / "justfile"
        justfile.write_text("@test:\n    go test -race ./...\n")
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert errors == []


# ── 5. -race present but no coverage → V37-CI-NO-COVERAGE-GATE ──────────


class TestWithRaceNoCoverageWarns:
    def test_with_race_no_coverage_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow(wf, "go test -race ./...")
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V37-CI-NO-COVERAGE-GATE"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        # No race error since -race is present
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert errors == []


# ── 6. Full race + coverprofile + upload-artifact passes ─────────────────


class TestFullRaceAndCoveragePasses:
    def test_full_race_and_coverage_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        _write_workflow_with_upload(
            wf,
            "go test -race -coverprofile=coverage.out ./...",
            "actions/upload-artifact@v3",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. codecov-action satisfies coverage gate ────────────────────────────


class TestCodecovActionSatisfiesCoverage:
    def test_codecov_action_satisfies_coverage(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        sha = "e84376f" + "0" * 33  # fake 40-char sha
        _write_workflow_with_upload(
            wf,
            "go test -race -coverprofile=coverage.out ./...",
            f"codecov/codecov-action@{sha}",
        )
        findings = validator.validate_project(project_ctx)
        coverage_warnings = [f for f in findings if f.rule == "V37-CI-NO-COVERAGE-GATE"]
        assert coverage_warnings == []


# ── 8. No workflows dir → no findings ────────────────────────────────────


class TestNoWorkflowsNoFindings:
    def test_no_workflows_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no .github/workflows and no Makefile/justfile
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. Comment line skipped ───────────────────────────────────────────────


class TestCommentLineSkipped:
    def test_comment_line_skipped_in_workflow(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        # The run block contains only a comment with go test
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Test\n"
            "        run: |\n"
            "          # go test ./...\n"
            "          echo done\n"
        )
        findings = validator.validate_project(project_ctx)
        race_errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert race_errors == []

    def test_comment_line_skipped_in_makefile(self, validator, tmp_project, project_ctx):
        makefile = tmp_project / "Makefile"
        makefile.write_text("# go test ./...\ntest:\n\techo done\n")
        findings = validator.validate_project(project_ctx)
        race_errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert race_errors == []


# ── 10. validate_file (Tier 2) — only edited file scanned ────────────────


class TestValidateFileSingleWorkflow:
    def test_validate_file_single_workflow(self, validator, tmp_project, workflows_dir, project_ctx):
        wf1 = workflows_dir / "ci.yml"
        wf2 = workflows_dir / "release.yaml"
        _write_workflow(wf1, "go test ./...")
        _write_workflow(wf2, "go test ./...")

        # Tier 2: only wf1 was "edited"
        findings = validator.validate_file(project_ctx, str(wf1))
        errors = [f for f in findings if f.rule == "V37-CI-NO-RACE"]
        assert len(errors) == 1
        assert errors[0].file == str(wf1)
        # wf2 violations must NOT appear
        assert all(str(wf2) not in f.file for f in findings)

    def test_validate_file_missing_path_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_file(project_ctx, str(tmp_project / "nonexistent.yml"))
        assert findings == []
