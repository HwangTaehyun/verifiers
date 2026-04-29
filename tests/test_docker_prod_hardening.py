"""Tests for V26 — docker compose production hardening."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.docker_prod_hardening import DockerProdHardeningValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> DockerProdHardeningValidator:
    return DockerProdHardeningValidator()


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


# ── 1. Production-file classification gate ────────────────────────────


class TestProductionGate:
    def test_dev_compose_ignored(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.yaml",
            """
            services:
              api:
                image: app:1.0
                volumes: ['./.env:/app/.env:ro']
                environment:
                  VIRTUAL_HOST: api.localhost
            """,
        )
        # Dev compose → V26 doesn't fire
        findings = validator.validate_project(ctx)
        assert findings == []

    def test_prod_compose_classified(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
            """,
        )
        findings = validator.validate_project(ctx)
        # No limits → V26-PROD-NO-RESOURCE-LIMITS fires
        assert any(f.rule == "V26-PROD-NO-RESOURCE-LIMITS" for f in findings)


# ── 2. V26-PROD-NO-RESOURCE-LIMITS ───────────────────────────────────


class TestResourceLimits:
    def test_no_limits_warns(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                deploy:
                  replicas: 2
            """,
        )
        findings = validator.validate_project(ctx)
        rl = [f for f in findings if f.rule == "V26-PROD-NO-RESOURCE-LIMITS"]
        assert len(rl) == 1

    def test_with_limits_passes(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                deploy:
                  resources:
                    limits:
                      memory: 512M
                      cpus: "0.5"
            """,
        )
        findings = validator.validate_project(ctx)
        rl = [f for f in findings if f.rule == "V26-PROD-NO-RESOURCE-LIMITS"]
        assert rl == []


# ── 3. V26-PROD-NO-HEALTHCHECK ───────────────────────────────────────


class TestHealthcheck:
    def test_depends_on_healthy_without_healthcheck_errors(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              db:
                image: postgres:16
                deploy:
                  resources: { limits: { memory: 256M, cpus: "0.5" } }
              api:
                image: app:1.0
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
                depends_on:
                  db:
                    condition: service_healthy
            """,
        )
        findings = validator.validate_project(ctx)
        hc = [f for f in findings if f.rule == "V26-PROD-NO-HEALTHCHECK"]
        assert len(hc) == 1
        assert "'db'" in hc[0].message

    def test_with_healthcheck_passes(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready"]
                  interval: 10s
                deploy:
                  resources: { limits: { memory: 256M, cpus: "0.5" } }
              api:
                image: app:1.0
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
                depends_on:
                  db:
                    condition: service_healthy
            """,
        )
        findings = validator.validate_project(ctx)
        hc = [f for f in findings if f.rule == "V26-PROD-NO-HEALTHCHECK"]
        assert hc == []


# ── 4. V26-PROD-SECRET-BIND-MOUNT ────────────────────────────────────


class TestSecretMount:
    def test_env_bind_mount_errors(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                volumes:
                  - ./.env:/app/.env:ro
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            """,
        )
        findings = validator.validate_project(ctx)
        sm = [f for f in findings if f.rule == "V26-PROD-SECRET-BIND-MOUNT"]
        assert len(sm) == 1

    def test_pem_bind_mount_errors(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                volumes:
                  - ./certs/jwt.pem:/etc/secrets/jwt.pem:ro
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            """,
        )
        findings = validator.validate_project(ctx)
        sm = [f for f in findings if f.rule == "V26-PROD-SECRET-BIND-MOUNT"]
        assert len(sm) == 1

    def test_data_volume_passes(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                volumes:
                  - app_data:/var/data
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            volumes:
              app_data:
            """,
        )
        findings = validator.validate_project(ctx)
        sm = [f for f in findings if f.rule == "V26-PROD-SECRET-BIND-MOUNT"]
        assert sm == []


# ── 5. V26-PROD-LOCALHOST-VHOST ──────────────────────────────────────


class TestLocalhostVhost:
    def test_virtual_host_localhost_errors(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                environment:
                  VIRTUAL_HOST: api.localhost
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            """,
        )
        findings = validator.validate_project(ctx)
        lh = [f for f in findings if f.rule == "V26-PROD-LOCALHOST-VHOST"]
        assert len(lh) == 1

    def test_real_domain_passes(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                environment:
                  VIRTUAL_HOST: api.example.com
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            """,
        )
        findings = validator.validate_project(ctx)
        lh = [f for f in findings if f.rule == "V26-PROD-LOCALHOST-VHOST"]
        assert lh == []

    def test_traefik_label_localhost_errors(self, validator, repo, ctx):
        _write(
            repo / "docker-compose.production.yaml",
            """
            services:
              api:
                image: app:1.0
                labels:
                  - "traefik.http.routers.api.rule=Host(`api.localhost`)"
                deploy:
                  resources: { limits: { memory: 512M, cpus: "1.0" } }
            """,
        )
        findings = validator.validate_project(ctx)
        lh = [f for f in findings if f.rule == "V26-PROD-LOCALHOST-VHOST"]
        assert len(lh) == 1
