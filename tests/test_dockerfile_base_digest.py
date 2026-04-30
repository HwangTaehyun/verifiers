"""Tests for V44 — dockerfile-base-digest-pin.

Covers:
  - V44-FROM-NO-DIGEST  — FROM line uses tag-only ref without @sha256 digest (warning)
  - Digest-pinned images pass cleanly
  - ARG-substituted refs ($VAR) are skipped
  - Multi-stage intermediate stage refs are skipped
  - Comment lines are skipped
  - --platform flag is handled correctly
  - validate_file (Tier 2) and validate_project (Tier 3) paths
"""

from __future__ import annotations


import pytest

from hooks.validators.dockerfile_base_digest import DockerfileBaseDigestValidator

# A valid 64-hex-char sha256 digest
VALID_DIGEST = "a" * 64
assert len(VALID_DIGEST) == 64


@pytest.fixture
def validator() -> DockerfileBaseDigestValidator:
    return DockerfileBaseDigestValidator()


# ── 1. Digest-pinned image passes ────────────────────────────────────────────


class TestDigestPinnedPasses:
    def test_digest_pinned_passes(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(f"FROM golang:1.25-bookworm@sha256:{VALID_DIGEST}\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_distroless_with_digest_passes(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(f"FROM gcr.io/distroless/static:nonroot@sha256:{VALID_DIGEST}\nCOPY app /app\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Tag-only ref → warning ─────────────────────────────────────────────────


class TestTagOnlyWarns:
    def test_tag_only_warns(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM golang:1.25-bookworm\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V44-FROM-NO-DIGEST"
        assert findings[0].severity == "warning"
        assert "golang:1.25-bookworm" in findings[0].message
        assert findings[0].line == 1

    def test_latest_tag_warns(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM nginx:latest\n")
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V44-FROM-NO-DIGEST"


# ── 3. ARG-substituted ref is skipped ────────────────────────────────────────


class TestArgSubstitutedSkipped:
    def test_arg_substituted_skipped(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("ARG BASE_IMAGE=golang:1.25\nFROM ${BASE_IMAGE}\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_dollar_var_skipped(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM $MY_IMAGE\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. Multi-stage intermediate ref is skipped ───────────────────────────────


class TestMultiStageIntermediateRefSkipped:
    def test_multi_stage_intermediate_ref_skipped(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(
            f"FROM golang:1.25-bookworm@sha256:{VALID_DIGEST} AS build\n"
            "RUN go build .\n"
            "FROM build AS prod\n"
            "COPY --from=build /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        # Only the unpinned multi-stage copy ref — but `build` is a stage name, not an image.
        # No findings expected: first FROM is pinned, second FROM references a stage.
        assert findings == []

    def test_multi_stage_final_stage_unpinned_warns(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(
            f"FROM golang:1.25-bookworm@sha256:{VALID_DIGEST} AS build\n"
            "RUN go build .\n"
            "FROM debian:bookworm-slim\n"
            "COPY --from=build /app /app\n"
        )
        findings = validator.validate_project(project_ctx)
        # debian:bookworm-slim has no digest
        assert len(findings) == 1
        assert "debian:bookworm-slim" in findings[0].message


# ── 5. Distroless without digest warns ───────────────────────────────────────


class TestDistrolessWithoutDigestWarns:
    def test_distroless_without_digest_warns(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM gcr.io/distroless/static:nonroot\nCOPY app /app\n")
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V44-FROM-NO-DIGEST"
        assert "gcr.io/distroless/static:nonroot" in findings[0].message


# ── 6. Comment line is skipped ───────────────────────────────────────────────


class TestCommentLineSkipped:
    def test_comment_line_skipped(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("# FROM nginx:latest\nFROM scratch\n")
        findings = validator.validate_project(project_ctx)
        # scratch has no tag, no digest — but also no "/" or ":" so heuristic
        # may classify it as a stage ref. Let's check it warns (it's a real base).
        # Actually scratch is special: it has no tag; it should warn.
        assert all(f.line != 1 for f in findings), "Comment line must not be flagged"


# ── 7. --platform flag is handled ────────────────────────────────────────────


class TestPlatformFlagHandled:
    def test_platform_flag_handled(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text("FROM --platform=linux/amd64 golang:1.25\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V44-FROM-NO-DIGEST"
        assert "golang:1.25" in findings[0].message

    def test_platform_flag_with_digest_passes(self, validator, tmp_project, project_ctx):
        df = tmp_project / "Dockerfile"
        df.write_text(f"FROM --platform=linux/amd64 golang:1.25@sha256:{VALID_DIGEST}\nRUN go build .\n")
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. No Dockerfile → no findings ───────────────────────────────────────────


class TestNoDockerfileNoFindings:
    def test_no_dockerfile_no_findings(self, validator, tmp_project, project_ctx):
        # tmp_project has no Dockerfile at all
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. Multiple Dockerfiles — each is checked ────────────────────────────────


class TestMultipleDockerfilesEachChecked:
    def test_multiple_dockerfiles_each_checked(self, validator, tmp_project, project_ctx):
        df1 = tmp_project / "Dockerfile"
        df2 = tmp_project / "server.Dockerfile"
        df1.write_text("FROM nginx:1.27-alpine\n")
        df2.write_text("FROM golang:1.25-bookworm\n")
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 2
        files_found = {f.file for f in findings}
        assert str(df1) in files_found
        assert str(df2) in files_found


# ── 10. validate_file (Tier 2) — only edited file scanned ────────────────────


class TestValidateFileSingleDockerfile:
    def test_validate_file_single_dockerfile(self, validator, tmp_project, project_ctx):
        df1 = tmp_project / "Dockerfile"
        df2 = tmp_project / "server.Dockerfile"
        df1.write_text("FROM nginx:1.27-alpine\n")
        df2.write_text("FROM golang:1.25-bookworm\n")

        # Tier 2: only df1 was "edited"
        findings = validator.validate_file(project_ctx, str(df1))
        assert len(findings) == 1
        assert "nginx:1.27-alpine" in findings[0].message
        # df2 violations must NOT appear
        assert all("golang" not in f.message for f in findings)

    def test_validate_file_missing_path_returns_empty(self, validator, tmp_project, project_ctx):
        findings = validator.validate_file(project_ctx, str(tmp_project / "nonexistent"))
        assert findings == []
