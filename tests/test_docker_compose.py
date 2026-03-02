"""Tests for V05: Docker Compose Validator.

Covers:
  - V05-PORT-CONFLICT
  - V05-VHOST-NO-NETWORK
  - V05-UNDEFINED-NETWORK
  - V05-MISSING-HEALTHCHECK
  - V05-MISSING-ENV-VAR
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.docker_compose import DockerComposeValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> DockerComposeValidator:
    return DockerComposeValidator()


# ---------------------------------------------------------------------------
# Helper: write docker-compose.yaml content into the tmp_project
# ---------------------------------------------------------------------------


def _write_compose(tmp_project: Path, content: str) -> Path:
    """Overwrite docker-compose.yaml in the tmp project and return its path."""
    compose_file = tmp_project / "docker-compose.yaml"
    compose_file.write_text(content)
    return compose_file


# ===========================================================================
# 1. _check_port_conflicts
# ===========================================================================


class TestPortConflicts:
    """V05-PORT-CONFLICT: two services with the same host port."""

    def test_same_host_port_produces_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  api:
    image: node
    ports:
      - "8080:3000"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-PORT-CONFLICT"]
        assert len(errors) == 1
        assert "8080" in errors[0].message
        assert "web" in errors[0].message
        assert "api" in errors[0].message

    def test_different_host_ports_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  api:
    image: node
    ports:
      - "3000:3000"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-PORT-CONFLICT"]
        assert len(errors) == 0


# ===========================================================================
# 2. _check_virtual_host_network
# ===========================================================================


class TestVirtualHostNetwork:
    """V05-VHOST-NO-NETWORK: VIRTUAL_HOST set but not on nginx-proxy network."""

    def test_vhost_without_nginx_proxy_network_produces_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    image: myapp
    environment:
      VIRTUAL_HOST: app.example.com
    networks:
      - backend
networks:
  backend:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(errors) == 1
        assert "app" in errors[0].message
        assert "nginx-proxy" in errors[0].message

    def test_vhost_with_nginx_proxy_network_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    image: myapp
    environment:
      VIRTUAL_HOST: app.example.com
    networks:
      - nginx-proxy
      - backend
networks:
  nginx-proxy:
    external: true
  backend:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(errors) == 0


# ===========================================================================
# 3. _check_network_references
# ===========================================================================


class TestNetworkReferences:
    """V05-UNDEFINED-NETWORK: service references a network not in top-level."""

    def test_undefined_network_produces_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    image: myapp
    networks:
      - nonexistent
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-UNDEFINED-NETWORK"]
        assert len(errors) == 1
        assert "nonexistent" in errors[0].message
        assert "app" in errors[0].message

    def test_defined_network_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  app:
    image: myapp
    networks:
      - backend
networks:
  backend:
    driver: bridge
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-UNDEFINED-NETWORK"]
        assert len(errors) == 0


# ===========================================================================
# 4. _check_depends_on_healthcheck
# ===========================================================================


class TestDependsOnHealthcheck:
    """V05-MISSING-HEALTHCHECK: depends_on service_healthy but target has no healthcheck."""

    def test_service_healthy_without_healthcheck_produces_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  db:
    image: postgres
  app:
    image: myapp
    depends_on:
      db:
        condition: service_healthy
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-MISSING-HEALTHCHECK"]
        assert len(errors) == 1
        assert "app" in errors[0].message
        assert "db" in errors[0].message
        assert "healthcheck" in errors[0].message

    def test_service_healthy_with_healthcheck_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        _write_compose(
            tmp_project,
            """\
version: "3"
services:
  db:
    image: postgres
    healthcheck:
      test: ["CMD", "pg_isready"]
      interval: 10s
      timeout: 5s
      retries: 5
  app:
    image: myapp
    depends_on:
      db:
        condition: service_healthy
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-MISSING-HEALTHCHECK"]
        assert len(errors) == 0


# ===========================================================================
# 5. _check_env_var_references
# ===========================================================================


class TestEnvVarReferences:
    """V05-MISSING-ENV-VAR: ${VAR} without default and not in .env."""

    def test_undefined_env_var_produces_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        # Make sure neither .env nor .env.example defines UNDEFINED_VAR
        (tmp_project / ".env.example").write_text("")
        # Remove .env if it exists
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
        var_names = [e.message for e in errors]
        assert len(errors) >= 2
        assert any("DB_USER" in m for m in var_names)
        assert any("DB_PASS" in m for m in var_names)

    def test_env_var_with_default_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        # Make sure .env and .env.example are empty (no definitions)
        (tmp_project / ".env.example").write_text("")
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
      DATABASE_URL: "postgres://${DB_USER:-admin}:${DB_PASS:-secret}@db:5432/mydb"
networks: {}
""",
        )
        result = validator.validate(project_ctx)

        errors = [f for f in result.findings if f.rule == "V05-MISSING-ENV-VAR"]
        assert len(errors) == 0

    def test_env_var_defined_in_dotenv_no_finding(
        self,
        validator: DockerComposeValidator,
        tmp_project: Path,
        project_ctx: ProjectContext,
    ) -> None:
        # Define the vars in .env
        (tmp_project / ".env").write_text("DB_USER=admin\nDB_PASS=secret\n")

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

    def test_env_var_defined_in_dotenv_example_no_finding(
        self,
        validator: DockerComposeValidator,
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
