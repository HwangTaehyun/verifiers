"""Tests for V45 — Dockerfile HEALTHCHECK presence."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.dockerfile_healthcheck import DockerfileHealthcheckValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> DockerfileHealthcheckValidator:
    return DockerfileHealthcheckValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


# ── 1. Pass cases ────────────────────────────────────────────────────────


class TestPassCases:
    def test_dockerfile_with_healthcheck_passes(self, validator, repo, ctx):
        """Final stage has EXPOSE and a real HEALTHCHECK — no findings."""
        _write(
            repo / "Dockerfile",
            """\
            FROM golang:1.25-bookworm AS builder
            RUN go build -o /app/server .

            FROM debian:bookworm-slim
            COPY --from=builder /app/server /usr/local/bin/server
            EXPOSE 7778
            HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
                CMD curl -f http://localhost:7778/health || exit 1
            ENTRYPOINT ["server"]
            """,
        )
        findings = validator.validate_project(ctx)
        assert findings == []

    def test_no_expose_exempt(self, validator, repo, ctx):
        """Worker-style Dockerfile with no EXPOSE is exempt — no findings."""
        _write(
            repo / "Dockerfile",
            """\
            FROM golang:1.25-bookworm
            COPY . .
            RUN go build -o worker .
            ENTRYPOINT ["./worker"]
            """,
        )
        findings = validator.validate_project(ctx)
        assert findings == []

    def test_no_dockerfile_no_findings(self, validator, repo, ctx):
        """Empty project with no Dockerfiles produces no findings."""
        findings = validator.validate_project(ctx)
        assert findings == []

    def test_comment_lines_dont_match(self, validator, repo, ctx):
        """'# HEALTHCHECK ...' comment does not satisfy the requirement."""
        _write(
            repo / "Dockerfile",
            """\
            FROM alpine:3.19
            EXPOSE 8080
            # HEALTHCHECK CMD curl localhost/health
            ENTRYPOINT ["/app"]
            """,
        )
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V45-DOCKERFILE-NO-HEALTHCHECK" in rules

    def test_grpc_healthcheck_passes(self, validator, repo, ctx):
        """Custom gRPC HEALTHCHECK (non-NONE) satisfies the requirement."""
        _write(
            repo / "Dockerfile",
            """\
            FROM golang:1.25-bookworm
            RUN go install github.com/grpc-ecosystem/grpc-health-probe/cmd/grpc_health_probe@latest
            RUN go build -o /app/server .
            EXPOSE 50051
            HEALTHCHECK --interval=30s --timeout=5s CMD grpc_health_probe -addr localhost:50051
            ENTRYPOINT ["/app/server"]
            """,
        )
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 2. Warn cases ────────────────────────────────────────────────────────


class TestWarnCases:
    def test_dockerfile_no_healthcheck_warns(self, validator, repo, ctx):
        """EXPOSE present but no HEALTHCHECK → V45-DOCKERFILE-NO-HEALTHCHECK."""
        _write(
            repo / "Dockerfile",
            """\
            FROM golang:1.25-bookworm
            RUN go build -o /app/server .
            EXPOSE 7778
            ENTRYPOINT ["/app/server"]
            """,
        )
        findings = validator.validate_project(ctx)
        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V45-DOCKERFILE-NO-HEALTHCHECK"
        assert f.severity == "warning"
        assert "7778" in f.message

    def test_healthcheck_in_intermediate_stage_does_not_count(self, validator, repo, ctx):
        """HEALTHCHECK in builder stage does not satisfy the final stage — V45 fires."""
        _write(
            repo / "Dockerfile",
            """\
            FROM golang:1.25 AS builder
            HEALTHCHECK CMD echo ok
            RUN go build .

            FROM alpine
            COPY --from=builder /app /app
            EXPOSE 3000
            """,
        )
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V45-DOCKERFILE-NO-HEALTHCHECK" in rules

    def test_multistage_only_final_stage_checked(self, validator, repo, ctx):
        """Only the final stage matters; intermediate stages with EXPOSE are irrelevant."""
        _write(
            repo / "Dockerfile",
            """\
            FROM node:20 AS build
            EXPOSE 3000
            HEALTHCHECK CMD curl localhost:3000

            FROM nginx:alpine AS final
            COPY --from=build /dist /usr/share/nginx/html
            EXPOSE 80
            """,
        )
        # Final stage has EXPOSE 80 but no HEALTHCHECK → warning
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V45-DOCKERFILE-NO-HEALTHCHECK" in rules

    def test_healthcheck_none_directive_treated_as_absence(self, validator, repo, ctx):
        """HEALTHCHECK NONE explicitly disables health checking — treated as absent → V45."""
        _write(
            repo / "Dockerfile",
            """\
            FROM debian:bookworm-slim
            EXPOSE 8080
            HEALTHCHECK NONE
            ENTRYPOINT ["/app"]
            """,
        )
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V45-DOCKERFILE-NO-HEALTHCHECK" in rules


# ── 3. Tier 2 validate_file ──────────────────────────────────────────────


class TestValidateFile:
    def test_validate_file_single_dockerfile(self, validator, repo, ctx):
        """validate_file on a single Dockerfile path works correctly."""
        df = repo / "Dockerfile"
        _write(
            df,
            """\
            FROM python:3.11-slim
            EXPOSE 5000
            ENTRYPOINT ["python", "app.py"]
            """,
        )
        findings = validator.validate_file(ctx, str(df))
        assert len(findings) == 1
        assert findings[0].rule == "V45-DOCKERFILE-NO-HEALTHCHECK"

    def test_validate_file_non_dockerfile_skipped(self, validator, repo, ctx):
        """validate_file on a non-Dockerfile path returns no findings."""
        other = repo / "docker-compose.yaml"
        _write(other, "services:\n  api:\n    image: app:1.0\n")
        findings = validator.validate_file(ctx, str(other))
        assert findings == []


# ── 4. Multiple Dockerfiles independent ─────────────────────────────────


class TestMultipleDockerfiles:
    def test_multiple_dockerfiles_independent(self, validator, repo, ctx):
        """Each Dockerfile is scanned independently; only violators are flagged."""
        # Dockerfile A: has healthcheck → pass
        _write(
            repo / "service-a" / "Dockerfile",
            """\
            FROM debian:bookworm-slim
            EXPOSE 8080
            HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1
            ENTRYPOINT ["/app"]
            """,
        )
        # Dockerfile B: no healthcheck → warn
        _write(
            repo / "service-b" / "Dockerfile",
            """\
            FROM debian:bookworm-slim
            EXPOSE 9090
            ENTRYPOINT ["/worker"]
            """,
        )
        findings = validator.validate_project(ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V45-DOCKERFILE-NO-HEALTHCHECK"
        assert "9090" in findings[0].message
