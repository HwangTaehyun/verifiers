"""Tests for V58 — reproducible-build-markers.

Covers:
  - Dockerfile with ARG SOURCE_DATE_EPOCH passes
  - Dockerfile with ENV SOURCE_DATE_EPOCH= passes
  - Dockerfile with no marker warns
  - Dev Dockerfile (filename *dev*) is exempt
  - Dev Dockerfile (final stage AS dev, no prod) is exempt
  - Workflow passing build-args with SOURCE_DATE_EPOCH satisfies a bare Dockerfile
  - No Dockerfiles → empty findings
  - Multi-stage: only final stage checked — intermediate marker not enough
  - validate_file delegates to full _check (project-wide)
  - Invalid YAML in workflow handled gracefully (regex fallback)
"""

from __future__ import annotations


import pytest

from hooks.validators.reproducible_build_markers import ReproducibleBuildMarkersValidator


@pytest.fixture
def validator() -> ReproducibleBuildMarkersValidator:
    return ReproducibleBuildMarkersValidator()


# ── 1. ARG SOURCE_DATE_EPOCH passes ──────────────────────────────────────────


class TestDockerfileWithArgSourceDateEpochPasses:
    def test_dockerfile_with_arg_source_date_epoch_passes(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25-bookworm AS builder\n"
            "RUN go build .\n"
            "FROM debian:bookworm-slim AS prod\n"
            "ARG SOURCE_DATE_EPOCH\n"
            "ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}\n"
            "COPY --from=builder /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. ENV SOURCE_DATE_EPOCH= passes ─────────────────────────────────────────


class TestDockerfileWithEnvSourceDateEpochPasses:
    def test_dockerfile_with_env_source_date_epoch_passes(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM alpine:3.20\nENV SOURCE_DATE_EPOCH=0\nRUN echo hello\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 3. No marker → warning ────────────────────────────────────────────────────


class TestDockerfileNoMarkerWarns:
    def test_dockerfile_no_marker_warns(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25-bookworm AS builder\n"
            "RUN go build .\n"
            "FROM debian:bookworm-slim AS prod\n"
            "COPY --from=builder /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V58-NO-SOURCE-DATE-EPOCH"
        assert f.severity == "warning"
        assert "SOURCE_DATE_EPOCH" in f.message
        assert str(df) == f.file


# ── 4. Dev Dockerfile exempt — filename has *dev* ────────────────────────────


class TestDevDockerfileExempt:
    def test_dev_dockerfile_exempt(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile.dev"
        df.write_text("FROM golang:1.25-bookworm\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_dev_in_name_also_exempt(self, validator, tmp_project, project_ctx):
        df = tmp_project / "dev.Dockerfile"
        df.write_text("FROM node:20\nRUN npm install\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. Final stage AS dev (no prod*) → exempt ────────────────────────────────


class TestDockerfileWithDevFinalStageExempt:
    def test_dockerfile_with_dev_final_stage_exempt(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25 AS build\nRUN go build .\nFROM debian:slim AS dev\nCOPY --from=build /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_dockerfile_with_dev_and_prod_stage_not_exempt(self, validator, tmp_project, project_ctx):
        """If both dev and prod stages exist, treat as prod Dockerfile."""
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25 AS build\n"
            "RUN go build .\n"
            "FROM debian:slim AS dev\n"
            "COPY --from=build /app /app\n"
            "FROM debian:slim AS prod\n"
            "COPY --from=build /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        # Final stage is prod, no SOURCE_DATE_EPOCH → should warn
        assert len(findings) == 1
        assert findings[0].rule == "V58-NO-SOURCE-DATE-EPOCH"


# ── 6. Workflow passing build-args satisfies Dockerfile ──────────────────────


class TestWorkflowPassesBuildArgSatisfiesDockerfile:
    def test_workflow_passes_build_arg_satisfies_dockerfile(self, validator, tmp_project, project_ctx):
        # Dockerfile has no marker
        df = tmp_project / "Dockerfile"
        df.write_text("FROM alpine:3.20\nRUN echo hello\n")

        # Workflow passes SOURCE_DATE_EPOCH via build-args
        wf_dir = tmp_project / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "build.yml").write_text(
            "name: build\n"
            "on: [push]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: docker/build-push-action@v5\n"
            "        with:\n"
            "          build-args: |\n"
            "            SOURCE_DATE_EPOCH=${{ github.event.head_commit.timestamp }}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. No Dockerfiles → empty ─────────────────────────────────────────────────


class TestNoDockerfilesReturnsEmpty:
    def test_no_dockerfiles_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. Multi-stage: only final stage checked ─────────────────────────────────


class TestMultistageOnlyFinalStageChecked:
    def test_multistage_only_final_stage_checked(self, validator, tmp_project, project_ctx):
        """Marker in intermediate builder stage only — final stage lacks it → flag."""
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25-bookworm AS builder\n"
            "ARG SOURCE_DATE_EPOCH\n"  # marker in intermediate stage
            "RUN go build .\n"
            "FROM debian:bookworm-slim AS prod\n"  # final stage — no marker
            "COPY --from=builder /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V58-NO-SOURCE-DATE-EPOCH"

    def test_multistage_marker_in_final_stage_passes(self, validator, tmp_project, project_ctx):
        """Marker in final stage passes regardless of intermediate stages."""
        df = tmp_project / "Dockerfile"
        df.write_text(
            "FROM golang:1.25-bookworm AS builder\n"
            "RUN go build .\n"
            "FROM debian:bookworm-slim AS prod\n"
            "ARG SOURCE_DATE_EPOCH\n"  # marker in final stage
            "COPY --from=builder /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. validate_file delegates to full project check ─────────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, project_ctx):
        """validate_file should run _check (project-wide), not just the edited file."""
        df1 = tmp_project / "Dockerfile"
        df2 = tmp_project / "server.Dockerfile"
        df1.write_text("FROM alpine:3.20\nRUN echo a\n")
        df2.write_text("FROM alpine:3.20\nRUN echo b\n")

        # Passing one file path: should still check both (full project sweep)
        findings = validator.validate_file(project_ctx, str(df1))
        # Both Dockerfiles lack marker → 2 findings
        assert len(findings) == 2
        rules = {f.rule for f in findings}
        assert rules == {"V58-NO-SOURCE-DATE-EPOCH"}


# ── 10. Invalid YAML in workflow handled gracefully ───────────────────────────


class TestInvalidYamlInWorkflowHandledGracefully:
    def test_invalid_yaml_in_workflow_handled_gracefully(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM alpine:3.20\nRUN echo hello\n")

        wf_dir = tmp_project / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        # Write deliberately malformed YAML (binary-ish garbage)
        (wf_dir / "broken.yml").write_bytes(b"\xff\xfe invalid: [yaml: {{{\n")

        # Should not raise; malformed workflow simply doesn't satisfy SDE
        findings = validator.validate_project(project_ctx)
        assert any(f.rule == "V58-NO-SOURCE-DATE-EPOCH" for f in findings)
