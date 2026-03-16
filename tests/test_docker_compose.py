"""Tests for V05: Docker Validator (Consolidated).

Covers 19 validation rules:
  Compose Files (5):
    - V05-PORT-CONFLICT
    - V05-VHOST-NO-NETWORK
    - V05-UNDEFINED-NETWORK
    - V05-MISSING-HEALTHCHECK
    - V05-MISSING-ENV-VAR

  Dockerfile (4):
    - V05-DOCKERFILE-NO-MULTISTAGE
    - V05-DOCKERFILE-NO-USER
    - V05-DOCKERFILE-NO-EXPOSE
    - V05-DOCKERFILE-COPY-ALL

  Production Safety (5):
    - V05-PROD-PORT-EXPOSED
    - V05-PROD-DEV-MODE
    - V05-PROD-WILDCARD-CORS
    - V05-PROD-NO-TRAEFIK-LABELS
    - V05-PROD-NO-RESOURCE-LIMITS

  Development Setup (2):
    - V05-DEV-NO-VOLUME-MOUNT
    - V05-DEV-NO-BUILD-TARGET

  Best Practices (3):
    - V05-BUILD-TARGET-MISSING
    - V05-BASE-IMAGE-LATEST
    - V05-MISSING-DOCKERIGNORE
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.docker_compose import DockerValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> DockerValidator:
    return DockerValidator()


# ---------------------------------------------------------------------------
# Helper: write docker-compose.yaml content into the tmp_project
# ---------------------------------------------------------------------------


def _write_compose(project: Path, content: str, filename: str = "docker-compose.yaml") -> None:
    (project / filename).write_text(content)


# ══════════════════════════════════════════════════════════════════
# Original V05 Compose validation tests
# ══════════════════════════════════════════════════════════════════


class TestV05PortConflicts:
    def test_different_services_same_host_port_conflict(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app1:
    ports:
      - "8080:80"
  app2:
    ports:
      - "8080:3000"  # conflict!
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        conflicts = [f for f in result.findings if f.rule == "V05-PORT-CONFLICT"]
        assert len(conflicts) == 1
        assert "8080" in conflicts[0].message
        assert conflicts[0].severity == "error"

    def test_no_port_conflicts(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app1:
    ports:
      - "8080:80"
  app2:
    ports:
      - "9000:3000"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        conflicts = [f for f in result.findings if f.rule == "V05-PORT-CONFLICT"]
        assert len(conflicts) == 0

    def test_same_service_multiple_host_ports_no_conflict(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    ports:
      - "8080:80"
      - "8081:81"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        conflicts = [f for f in result.findings if f.rule == "V05-PORT-CONFLICT"]
        assert len(conflicts) == 0


class TestV05VirtualHostNetwork:
    def test_virtual_host_without_nginx_proxy_network(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      VIRTUAL_HOST: example.com
    networks:
      - app_network
networks:
  app_network:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        vhost_issues = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(vhost_issues) == 1
        assert "nginx-proxy" in vhost_issues[0].message

    def test_virtual_host_with_nginx_proxy_network(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      VIRTUAL_HOST: example.com
    networks:
      - nginx-proxy
networks:
  nginx-proxy:
    external: true
""",
        )
        result = validator.validate(project_ctx)

        vhost_issues = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(vhost_issues) == 0

    def test_no_virtual_host_no_issue(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      OTHER_VAR: value
    networks:
      - app_network
networks:
  app_network:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        vhost_issues = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(vhost_issues) == 0


class TestV05NetworkReferences:
    def test_service_references_undefined_network(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    networks:
      - undefined_network
networks:
  defined_network:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        undefined_networks = [f for f in result.findings if f.rule == "V05-UNDEFINED-NETWORK"]
        assert len(undefined_networks) == 1
        assert "undefined_network" in undefined_networks[0].message

    def test_service_references_defined_network(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    networks:
      - defined_network
networks:
  defined_network:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        undefined_networks = [f for f in result.findings if f.rule == "V05-UNDEFINED-NETWORK"]
        assert len(undefined_networks) == 0

    def test_external_network_not_required_in_networks_section(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    networks:
      - external_network
networks:
  external_network:
    external: true
""",
        )
        result = validator.validate(project_ctx)

        undefined_networks = [f for f in result.findings if f.rule == "V05-UNDEFINED-NETWORK"]
        assert len(undefined_networks) == 0


class TestV05DependsOnHealthcheck:
    def test_depends_on_service_healthy_without_healthcheck(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:13
    # No healthcheck defined!
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        healthcheck_issues = [f for f in result.findings if f.rule == "V05-MISSING-HEALTHCHECK"]
        assert len(healthcheck_issues) == 1
        assert "db" in healthcheck_issues[0].message

    def test_depends_on_service_healthy_with_healthcheck(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    depends_on:
      db:
        condition: service_healthy
  db:
    image: postgres:13
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
      interval: 30s
      timeout: 10s
      retries: 3
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        healthcheck_issues = [f for f in result.findings if f.rule == "V05-MISSING-HEALTHCHECK"]
        assert len(healthcheck_issues) == 0

    def test_depends_on_service_started_no_healthcheck_required(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    depends_on:
      db:
        condition: service_started
  db:
    image: postgres:13
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        healthcheck_issues = [f for f in result.findings if f.rule == "V05-MISSING-HEALTHCHECK"]
        assert len(healthcheck_issues) == 0


class TestV05EnvVarReferences:
    def test_env_var_without_default_and_not_in_env(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      DATABASE_URL: "postgres://${MISSING_VAR}@localhost/db"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        env_var_issues = [f for f in result.findings if f.rule == "V05-MISSING-ENV-VAR"]
        assert len(env_var_issues) == 1
        assert "MISSING_VAR" in env_var_issues[0].message

    def test_env_var_with_default_no_issue(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      DATABASE_URL: "postgres://${DB_USER:-defaultuser}@localhost/db"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        env_var_issues = [f for f in result.findings if f.rule == "V05-MISSING-ENV-VAR"]
        assert len(env_var_issues) == 0

    def test_env_var_defined_in_env_file_no_issue(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        # Create .env with the variable
        (tmp_project / ".env").write_text("DB_USER=testuser\nDB_PASS=testpass\n")

        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    environment:
      DATABASE_URL: "postgres://${DB_USER}:${DB_PASS}@db:5432/mydb"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-MISSING-ENV-VAR"]
        assert len(errors) == 0

    def test_env_var_defined_in_dotenv_example_no_finding(
        self,
        validator: DockerValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        # Define the vars in .env.example only
        (tmp_project / ".env.example").write_text("DB_USER=\nDB_PASS=\n")
        env_file = tmp_project / ".env"
        if env_file.exists():
            env_file.unlink()

        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    image: myapp
    environment:
      DATABASE_URL: "postgres://${DB_USER}:${DB_PASS}@db:5432/mydb"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-MISSING-ENV-VAR"]
        assert len(errors) == 0


# ══════════════════════════════════════════════════════════════════
# Additional fixtures for comprehensive Docker testing
# ══════════════════════════════════════════════════════════════════


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
# Dockerfile: Multi-stage
# ══════════════════════════════════════════════════════════════════


class TestV05DockerfileMultistage:
    def test_single_stage_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25-alpine\nRUN go build -o app .\nCMD [\"./app\"]\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-NO-MULTISTAGE" in rules

    def test_multistage_no_warning(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25-alpine AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\nUSER app\nEXPOSE 7778\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-NO-MULTISTAGE" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: USER directive
# ══════════════════════════════════════════════════════════════════


class TestV05DockerfileUser:
    def test_no_user_in_prod_stage_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-DOCKERFILE-NO-USER"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_user_in_prod_stage_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nRUN adduser -S app\nUSER app\nCOPY --from=builder /app /app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-NO-USER" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: EXPOSE
# ══════════════════════════════════════════════════════════════════


class TestV05DockerfileExpose:
    def test_no_expose_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-NO-EXPOSE" in rules

    def test_expose_present_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\nCMD [\"./app\"]\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-NO-EXPOSE" not in rules


# ══════════════════════════════════════════════════════════════════
# Dockerfile: COPY . . without .dockerignore
# ══════════════════════════════════════════════════════════════════


class TestV05DockerfileCopyAll:
    def test_copy_all_without_dockerignore_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nCOPY . .\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-COPY-ALL" in rules

    def test_copy_all_with_dockerignore_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nCOPY . .\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nUSER app\nEXPOSE 7778\n"
        )
        (docker_project / "server" / ".dockerignore").write_text(".env\n.git\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-DOCKERFILE-COPY-ALL" not in rules


# ══════════════════════════════════════════════════════════════════
# Production compose: Port exposure
# ══════════════════════════════════════════════════════════════════


class TestV05ProdPortExposed:
    def test_ports_in_production_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-PROD-PORT-EXPOSED" in rules

    def test_empty_ports_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    ports: []\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-PROD-PORT-EXPOSED" not in rules


# ══════════════════════════════════════════════════════════════════
# Production compose: Dev mode
# ══════════════════════════════════════════════════════════════════


class TestV05ProdDevMode:
    def test_dev_mode_true_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  myapp:\n"
            "    environment:\n"
            "      APP_DEV: 'true'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-PROD-DEV-MODE"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_dev_mode_false_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-PROD-DEV-MODE" not in rules

    def test_hasura_dev_mode_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  hasura:\n"
            "    environment:\n"
            "      HASURA_GRAPHQL_DEV_MODE: 'true'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-PROD-DEV-MODE"]
        assert len(errors) >= 1

    def test_node_env_development_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  web:\n"
            "    environment:\n"
            "      NODE_ENV: development\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-PROD-DEV-MODE"]
        assert len(errors) == 1


# ══════════════════════════════════════════════════════════════════
# Production compose: Wildcard CORS
# ══════════════════════════════════════════════════════════════════


class TestV05ProdWildcardCors:
    def test_wildcard_cors_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        compose = docker_project / "server" / "docker-compose.production.yaml"
        compose.write_text(
            "services:\n"
            "  hasura:\n"
            "    environment:\n"
            "      HASURA_GRAPHQL_CORS_DOMAIN: '*'\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-PROD-WILDCARD-CORS"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_specific_cors_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-PROD-WILDCARD-CORS" not in rules


# ══════════════════════════════════════════════════════════════════
# Dev override: Volume mount
# ══════════════════════════════════════════════════════════════════


class TestV05DevVolumeMount:
    def test_no_volumes_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-DEV-NO-VOLUME-MOUNT" in rules

    def test_with_volumes_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-DEV-NO-VOLUME-MOUNT" not in rules


# ══════════════════════════════════════════════════════════════════
# Dev override: Build target
# ══════════════════════════════════════════════════════════════════


class TestV05DevBuildTarget:
    def test_wrong_target_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-DEV-NO-BUILD-TARGET" in rules

    def test_dev_target_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
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
        assert "V05-DEV-NO-BUILD-TARGET" not in rules


# ══════════════════════════════════════════════════════════════════
# Best Practices: Base image latest
# ══════════════════════════════════════════════════════════════════


class TestV05BaseImageLatest:
    def test_latest_tag_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:latest\nRUN go build -o app .\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-BASE-IMAGE-LATEST" in rules

    def test_no_tag_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang\nRUN go build -o app .\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-BASE-IMAGE-LATEST" in rules

    def test_specific_version_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25-alpine\nRUN go build -o app .\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-BASE-IMAGE-LATEST" not in rules


# ══════════════════════════════════════════════════════════════════
# Best Practices: Missing .dockerignore
# ══════════════════════════════════════════════════════════════════


class TestV05MissingDockerignore:
    def test_copy_all_no_dockerignore_warns(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25\nCOPY . .\nRUN go build -o app .\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-MISSING-DOCKERIGNORE" in rules

    def test_copy_all_with_dockerignore_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25\nCOPY . .\nRUN go build -o app .\n")
        (docker_project / "server" / ".dockerignore").write_text(".env\n.git\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-MISSING-DOCKERIGNORE" not in rules

    def test_no_copy_all_no_warning(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        dockerfile = docker_project / "server" / "Dockerfile"
        dockerfile.write_text("FROM golang:1.25\nCOPY src/ /app/\nRUN go build -o app .\n")

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-MISSING-DOCKERIGNORE" not in rules


# ══════════════════════════════════════════════════════════════════
# Best Practices: Build target missing
# ══════════════════════════════════════════════════════════════════


class TestV05BuildTargetMissing:
    def test_missing_target_stage_errors(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        # Create compose file with build.target referencing non-existent stage
        compose = docker_project / "docker-compose.yaml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    build:\n"
            "      context: .\n"
            "      target: missing\n"
        )

        # Create Dockerfile without the referenced stage
        dockerfile = docker_project / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\n"
        )

        result = validator.validate(docker_ctx)
        errors = [f for f in result.findings if f.rule == "V05-BUILD-TARGET-MISSING"]
        assert len(errors) == 1
        assert errors[0].severity == "error"

    def test_existing_target_stage_ok(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        # Create compose file with build.target referencing existing stage
        compose = docker_project / "docker-compose.yaml"
        compose.write_text(
            "services:\n"
            "  app:\n"
            "    build:\n"
            "      context: .\n"
            "      target: dev\n"
        )

        # Create Dockerfile with the referenced stage
        dockerfile = docker_project / "Dockerfile"
        dockerfile.write_text(
            "FROM golang:1.25 AS builder\nRUN go build -o app .\n"
            "FROM builder AS dev\nEXPOSE 8080\n"
            "FROM alpine:3.21 AS prod\nCOPY --from=builder /app /app\n"
        )

        result = validator.validate(docker_ctx)
        rules = [f.rule for f in result.findings]
        assert "V05-BUILD-TARGET-MISSING" not in rules


# ══════════════════════════════════════════════════════════════════
# No findings on clean project
# ══════════════════════════════════════════════════════════════════


class TestV05CleanProject:
    def test_no_docker_files_no_findings(
        self, validator: DockerValidator, docker_project: Path, docker_ctx: ProjectContext
    ) -> None:
        """Project with no Docker files should produce zero findings."""
        result = validator.validate(docker_ctx)
        assert len(result.findings) == 0