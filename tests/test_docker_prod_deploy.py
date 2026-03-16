"""Tests for V17: Docker Production Deployment validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.docker_prod_deploy import DockerProdDeployValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> DockerProdDeployValidator:
    return DockerProdDeployValidator()


@pytest.fixture
def docker_project(tmp_path: Path) -> Path:
    """Create a project structure with Docker files for testing."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server" / "config").mkdir(parents=True)
    (tmp_path / "server" / "docker").mkdir(parents=True)
    (tmp_path / "web" / "docker").mkdir(parents=True)
    (tmp_path / "server" / "config" / "testproject.local.yaml").write_text("port: 8080\n")
    (tmp_path / ".env.example").write_text("TAG=latest\n")
    return tmp_path


@pytest.fixture
def docker_ctx(docker_project: Path) -> ProjectContext:
    return ProjectContext(docker_project)


# ══════════════════════════════════════════════════════════════════
# Validator metadata
# ══════════════════════════════════════════════════════════════════


class TestValidatorMetadata:
    def test_id(self, validator: DockerProdDeployValidator) -> None:
        assert validator.id == "V17-docker-prod-deploy"

    def test_name(self, validator: DockerProdDeployValidator) -> None:
        assert validator.name == "Docker Production Deployment Validator"

    def test_file_patterns(self, validator: DockerProdDeployValidator) -> None:
        assert "**/Dockerfile*" in validator.file_patterns
        assert "**/docker-compose*.yaml" in validator.file_patterns
        assert "**/.dockerignore" in validator.file_patterns

    def test_should_run_dockerfile(self, validator: DockerProdDeployValidator) -> None:
        assert validator.should_run("/project/server/docker/Dockerfile") is True
        assert validator.should_run("/project/server/docker/omas-server.Dockerfile") is True

    def test_should_run_compose(self, validator: DockerProdDeployValidator) -> None:
        assert validator.should_run("/project/docker-compose.production.yaml") is True
        assert validator.should_run("/project/docker-compose.override.yaml") is True

    def test_should_not_run_unrelated(self, validator: DockerProdDeployValidator) -> None:
        assert validator.should_run("/project/main.go") is False
        assert validator.should_run("/project/package.json") is False


# ══════════════════════════════════════════════════════════════════
# Dockerfile: Multi-stage
# ══════════════════════════════════════════════════════════════════


class TestDockerfileMultistage:
    def test_single_stage_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25-alpine\nRUN go build -o app .\nCMD [\"./app\"]\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-NO-MULTISTAGE" in rules

    def test_multistage_no_warning(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25-alpine AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\nUSER app\nEXPOSE 7778\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-NO-MULTISTAGE" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: USER directive
# ══════════════════════════════════════════════════════════════════


class TestDockerfileUser:
    def test_no_user_in_prod_stage_errors(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V17-DOCKERFILE-NO-USER"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_user_in_prod_stage_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nRUN adduser -S app\nUSER app\nCOPY --from=builder /app /app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-NO-USER" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: EXPOSE
# ══════════════════════════════════════════════════════════════════


class TestDockerfileExpose:
    def test_no_expose_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-NO-EXPOSE" in rules

    def test_expose_present_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-NO-EXPOSE" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: COPY . . without .dockerignore
# ══════════════════════════════════════════════════════════════════


class TestDockerfileCopyAll:
    def test_copy_all_without_dockerignore_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nCOPY . .\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-COPY-ALL" in rules

    def test_copy_all_with_dockerignore_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nCOPY . .\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\n"
        )
        (docker_project / "server" / ".dockerignore").write_text(".env\n.git\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DOCKERFILE-COPY-ALL" not in rules


# ══════════════════════════════════════════════════════════════════
# Production compose: Port exposure
# ══════════════════════════════════════════════════════════════════


class TestProdPortExposed:
    def test_ports_in_production_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    ports:\n"
            "      - '8080:8080'\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-PROD-PORT-EXPOSED" in rules

    def test_empty_ports_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    ports: []\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-PROD-PORT-EXPOSED" not in rules


# ══════════════════════════════════════════════════════════════════
# Production compose: Dev mode
# ══════════════════════════════════════════════════════════════════


class TestProdDevMode:
    def test_dev_mode_true_errors(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    environment:\n"
            "      APP_DEV: 'true'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V17-PROD-DEV-MODE"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_dev_mode_false_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    environment:\n"
            "      APP_DEV: 'false'\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-PROD-DEV-MODE" not in rules

    def test_hasura_dev_mode_errors(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  hasura:\n"
            "    environment:\n"
            "      HASURA_GRAPHQL_DEV_MODE: 'true'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V17-PROD-DEV-MODE"]
        assert len(errors) >= 1

    def test_node_env_development_errors(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  web:\n"
            "    environment:\n"
            "      NODE_ENV: development\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V17-PROD-DEV-MODE"]
        assert len(errors) == 1


# ══════════════════════════════════════════════════════════════════
# Production compose: Wildcard CORS
# ══════════════════════════════════════════════════════════════════


class TestProdWildcardCors:
    def test_wildcard_cors_errors(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  hasura:\n"
            "    environment:\n"
            "      HASURA_GRAPHQL_CORS_DOMAIN: '*'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V17-PROD-WILDCARD-CORS"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_specific_cors_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  hasura:\n"
            "    environment:\n"
            "      HASURA_GRAPHQL_CORS_DOMAIN: 'https://example.com'\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-PROD-WILDCARD-CORS" not in rules


# ══════════════════════════════════════════════════════════════════
# Dev override: Volume mount
# ══════════════════════════════════════════════════════════════════


class TestDevVolumeMount:
    def test_no_volumes_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.override.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    build:\n"
            "      target: dev\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DEV-NO-VOLUME-MOUNT" in rules

    def test_with_volumes_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.override.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    build:\n"
            "      target: dev\n"
            "    volumes:\n"
            "      - .:/app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DEV-NO-VOLUME-MOUNT" not in rules


# ══════════════════════════════════════════════════════════════════
# Dev override: Build target
# ══════════════════════════════════════════════════════════════════


class TestDevBuildTarget:
    def test_wrong_target_warns(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.override.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    build:\n"
            "      target: prod\n"
            "    volumes:\n"
            "      - .:/app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DEV-NO-BUILD-TARGET" in rules

    def test_dev_target_ok(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.override.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    build:\n"
            "      target: dev\n"
            "    volumes:\n"
            "      - .:/app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V17-DEV-NO-BUILD-TARGET" not in rules


# ══════════════════════════════════════════════════════════════════
# No findings on clean project
# ══════════════════════════════════════════════════════════════════


class TestCleanProject:
    def test_no_docker_files_no_findings(
        self, validator: DockerProdDeployValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        """Project with no Docker files should produce zero findings."""
        result = validator.validate(docker_ctx)
        assert len(result.findings) == 0
