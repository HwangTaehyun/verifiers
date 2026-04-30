"""Tests for V57 — sbom-ci-step.

Covers:
  - anchore/sbom-action in uses → pass
  - cyclonedx/gh-gomod-generate-sbom in uses → pass
  - cyclonedx-gomod in run command → pass
  - syft in run command → pass
  - aquasecurity/trivy-action with format: cyclonedx → pass
  - No SBOM tool anywhere → V57-NO-SBOM-CI warning
  - One of multiple workflows satisfies (project-level check) → pass
  - No .github/workflows/ directory → no findings
  - Invalid YAML is handled gracefully (no crash)
  - validate_file delegates to full project check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.sbom_ci_step import SbomCiStepValidator


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> SbomCiStepValidator:
    return SbomCiStepValidator()


@pytest.fixture
def workflows_dir(tmp_project: Path) -> Path:
    """Create and return .github/workflows/ under tmp_project."""
    d = tmp_project / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. anchore/sbom-action in uses → pass ────────────────────────────────────


class TestAnchoreSbomActionPasses:
    def test_anchore_sbom_action_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Generate SBOM\n"
            "        uses: anchore/sbom-action@v0\n"
            "        with:\n"
            "          format: cyclonedx-json\n"
            "          output-file: sbom.cdx.json\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. cyclonedx/gh-gomod-generate-sbom in uses → pass ───────────────────────


class TestCyclonedxGomodUsesPasses:
    def test_cyclonedx_gomod_uses_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  sbom:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: cyclonedx/gh-gomod-generate-sbom@v1\n"
            "        with:\n"
            "          version: v1\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 3. cyclonedx-gomod in run command → pass ─────────────────────────────────


class TestCyclonedxGomodRunPasses:
    def test_cyclonedx_gomod_run_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  sbom:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Generate SBOM\n"
            "        run: cyclonedx-gomod app -output sbom.json .\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. syft in run command → pass ────────────────────────────────────────────


class TestSyftRunPasses:
    def test_syft_run_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  sbom:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Generate SBOM with Syft\n"
            "        run: syft . -o cyclonedx-json=sbom.json\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. trivy-action with cyclonedx format → pass ─────────────────────────────


class TestTrivyWithCyclonedxFormatPasses:
    def test_trivy_with_cyclonedx_format_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  sbom:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Generate SBOM via Trivy\n"
            "        uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          scan-type: fs\n"
            "          format: cyclonedx\n"
            "          output: sbom.cdx.json\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. No SBOM tool anywhere → V57-NO-SBOM-CI warning ────────────────────────


class TestNoSbomAnywhereWarns:
    def test_no_sbom_anywhere_warns(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: go build ./...\n"
            "      - uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          image-ref: myapp:latest\n"
            "          format: sarif\n"
        )
        findings = validator.validate_project(project_ctx)
        warnings = [f for f in findings if f.rule == "V57-NO-SBOM-CI"]
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        assert "SBOM" in warnings[0].message
        assert "V43" in warnings[0].message


# ── 7. One of multiple workflows satisfies → no findings ─────────────────────


class TestOneOfMultipleWorkflowsSatisfies:
    def test_one_of_multiple_workflows_satisfies(self, validator, tmp_project, workflows_dir, project_ctx):
        # ci.yml: no SBOM tool
        ci = workflows_dir / "ci.yml"
        ci.write_text(
            "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: go test ./...\n"
        )
        # release.yml: has anchore/sbom-action
        release = workflows_dir / "release.yml"
        release.write_text(
            "on:\n"
            "  push:\n"
            "    tags: ['v*']\n"
            "jobs:\n"
            "  release:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: anchore/sbom-action@v0\n"
            "        with:\n"
            "          format: spdx-json\n"
            "          output-file: sbom.spdx.json\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. No .github/workflows/ directory → no findings ─────────────────────────


class TestNoWorkflowsDirReturnsEmpty:
    def test_no_workflows_dir_returns_empty(self, validator, tmp_project, project_ctx):
        # tmp_project has no .github/workflows/ by default
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. Invalid YAML handled gracefully — no crash ────────────────────────────


class TestInvalidYamlHandledGracefully:
    def test_invalid_yaml_handled_gracefully(self, validator, tmp_project, workflows_dir, project_ctx):
        bad = workflows_dir / "broken.yml"
        bad.write_text("on: [push]\njobs:\n  build: {\n  this is not valid yaml\n")
        # A second valid-but-no-SBOM workflow to ensure the check still fires
        good = workflows_dir / "ci.yml"
        good.write_text("on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n")
        # Must not raise; invalid file is skipped, warning is still emitted
        findings = validator.validate_project(project_ctx)
        assert isinstance(findings, list)
        warnings = [f for f in findings if f.rule == "V57-NO-SBOM-CI"]
        assert len(warnings) == 1


# ── 10. validate_file delegates to full project check ────────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, workflows_dir, project_ctx):
        # ci.yml (the "edited" file): no SBOM
        ci = workflows_dir / "ci.yml"
        ci.write_text(
            "on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: go build ./...\n"
        )
        # release.yml: has SBOM — validate_file must still find it and pass
        release = workflows_dir / "release.yml"
        release.write_text(
            "on: [push]\njobs:\n  sbom:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: anchore/sbom-action@v0\n"
        )
        findings = validator.validate_file(project_ctx, str(ci))
        assert findings == []

    def test_validate_file_warns_when_no_sbom_anywhere(self, validator, tmp_project, workflows_dir, project_ctx):
        ci = workflows_dir / "ci.yml"
        ci.write_text(
            "on: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: go build ./...\n"
        )
        findings = validator.validate_file(project_ctx, str(ci))
        warnings = [f for f in findings if f.rule == "V57-NO-SBOM-CI"]
        assert len(warnings) == 1
