"""Tests for V43 — ci-image-scanning.

Covers:
  - V43-NO-IMAGE-SCAN — build job missing scanner (error)
  - Build with scanner in same job → pass
  - Build with scanner in downstream job → pass
  - Grype in run: step satisfies scanner check
  - docker/build-push-action without scanner → error
  - Scanner in unrelated job (no needs link) → still flagged
  - Multiple workflows checked independently
  - No build jobs → no findings
  - No workflows directory → no findings
  - validate_file (Tier 2) path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.ci_image_scanning import CiImageScanningValidator


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> CiImageScanningValidator:
    return CiImageScanningValidator()


@pytest.fixture
def workflows_dir(tmp_project: Path) -> Path:
    """Create and return .github/workflows/ under tmp_project."""
    d = tmp_project / ".github" / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. No build jobs → no findings ───────────────────────────────────────────


class TestNoBuildJobs:
    def test_no_build_jobs_no_findings(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: pytest\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. docker build + trivy in same job → pass ───────────────────────────────


class TestBuildWithTrivyInSameJob:
    def test_build_with_trivy_in_same_job_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build-and-scan:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Build image\n"
            "        run: docker build -t myapp:latest .\n"
            "      - name: Scan with Trivy\n"
            "        uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          image-ref: myapp:latest\n"
            "          severity: CRITICAL\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 3. Build in job A, trivy in job B with needs: [A] → pass ─────────────────


class TestBuildWithTrivyInDependentJob:
    def test_build_with_trivy_in_dependent_job_passes(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: docker build -t myapp:${{ github.sha }} .\n"
            "  scan:\n"
            "    needs: [build]\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Scan with Trivy\n"
            "        uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          image-ref: myapp:${{ github.sha }}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. docker build with no scanner → V43-NO-IMAGE-SCAN ──────────────────────


class TestBuildNoScanner:
    def test_build_no_scanner_errors(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: docker build -t myapp:latest .\n"
            "      - run: docker push myapp:latest\n"
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V43-NO-IMAGE-SCAN"]
        assert len(errors) == 1
        assert "build" in errors[0].message
        assert errors[0].severity == "error"


# ── 5. grype in run: satisfies scanner check ─────────────────────────────────


class TestGrypeInRunSatisfies:
    def test_grype_in_run_satisfies(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t myapp:latest .\n"
            "      - name: Scan with Grype\n"
            "        run: grype myapp:latest --fail-on critical\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. docker/build-push-action without scanner → error ──────────────────────


class TestBuildPushActionNoScanner:
    def test_build_push_action_no_scanner_errors(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build-push:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Build and push\n"
            "        uses: docker/build-push-action@v5\n"
            "        with:\n"
            "          push: true\n"
            "          tags: myregistry/myapp:latest\n"
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V43-NO-IMAGE-SCAN"]
        assert len(errors) == 1
        assert "build-push" in errors[0].message


# ── 7. Scanner in unrelated job (no needs link) → still flagged ──────────────


class TestScannerInUnrelatedJobDoesNotCount:
    def test_scanner_in_unrelated_job_does_not_count(self, validator, tmp_project, workflows_dir, project_ctx):
        wf = workflows_dir / "ci.yml"
        wf.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t myapp:latest .\n"
            "  scan-other:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          image-ref: someother:latest\n"
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V43-NO-IMAGE-SCAN"]
        assert len(errors) == 1
        assert "build" in errors[0].message


# ── 8. Multiple workflows checked independently ───────────────────────────────


class TestMultipleWorkflowsIndependent:
    def test_multiple_workflows_independent(self, validator, tmp_project, workflows_dir, project_ctx):
        # wf1: build with scanner → pass
        wf1 = workflows_dir / "ci.yml"
        wf1.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t app:latest .\n"
            "      - uses: aquasecurity/trivy-action@master\n"
            "        with:\n"
            "          image-ref: app:latest\n"
        )
        # wf2: build without scanner → error
        wf2 = workflows_dir / "release.yaml"
        wf2.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  release-build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t app:release .\n"
        )
        findings = validator.validate_project(project_ctx)
        errors = [f for f in findings if f.rule == "V43-NO-IMAGE-SCAN"]
        assert len(errors) == 1
        assert str(wf2) in errors[0].file


# ── 9. No workflows directory → no findings ──────────────────────────────────


class TestNoWorkflowsNoFindings:
    def test_no_workflows_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no .github/workflows directory
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 10. validate_file (Tier 2) — only the edited file is scanned ─────────────


class TestValidateFileSingleWorkflow:
    def test_validate_file_single_workflow(self, validator, tmp_project, workflows_dir, project_ctx):
        wf1 = workflows_dir / "ci.yml"
        wf1.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t app:latest .\n"
        )
        wf2 = workflows_dir / "release.yaml"
        wf2.write_text(
            "on: [push]\n"
            "jobs:\n"
            "  release-build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: docker build -t app:release .\n"
        )
        # Tier 2: only wf1 was "edited"
        findings = validator.validate_file(project_ctx, str(wf1))
        errors = [f for f in findings if f.rule == "V43-NO-IMAGE-SCAN"]
        assert len(errors) == 1
        assert "build" in errors[0].message
        # wf2 violations must NOT appear
        assert all(str(wf2) not in f.file for f in findings)

    def test_validate_file_missing_path_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_file(project_ctx, str(tmp_project / "nonexistent.yml"))
        assert findings == []
