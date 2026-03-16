"""Shared test fixtures for verifier tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_context import ProjectContext


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project structure for testing.

    Returns the project root path with:
    - .git/ (empty, for git root detection)
    - server/config/ (for config validation)
    - server/graph/gqlclient/ (for genqlient validation)
    - server/proto/ (for proto validation)
    - server/hasura/migrations/testproject/
    - server/hasura/metadata/databases/testproject/tables/
    - web/src/ (for TypeScript validation)
    - web/env/ (for VITE_* validation)
    - .env.example
    - .gitignore
    - docker-compose.yaml
    """
    # Git root marker
    (tmp_path / ".git").mkdir()

    # Server structure
    server = tmp_path / "server"
    (server / "config").mkdir(parents=True)
    (server / "graph" / "gqlclient").mkdir(parents=True)
    (server / "graph" / "queries").mkdir(parents=True)
    (server / "graph" / "schemas").mkdir(parents=True)
    (server / "proto").mkdir(parents=True)
    (server / "gen").mkdir(parents=True)
    (server / "internal").mkdir(parents=True)

    # Hasura structure
    hasura = server / "hasura"
    migrations = hasura / "migrations" / "testproject"
    migrations.mkdir(parents=True)
    tables = hasura / "metadata" / "databases" / "testproject" / "tables"
    tables.mkdir(parents=True)

    # Web structure
    web = tmp_path / "web"
    (web / "src").mkdir(parents=True)
    (web / "env").mkdir(parents=True)

    # Minimum config file (for project name detection)
    (server / "config" / "testproject.local.yaml").write_text("port: 8080\n")

    # .env.example
    (tmp_path / ".env.example").write_text("")

    # .gitignore
    (tmp_path / ".gitignore").write_text(".env\n*.pem\n*.key\n.env.local\n*.p12\n")

    # docker-compose.yaml
    (tmp_path / "docker-compose.yaml").write_text("version: '3'\nservices: {}\nnetworks: {}\n")

    # Go files for Go quality validator tests
    (server / "go.mod").write_text("module testproject\n\ngo 1.21\n")
    (server / "main.go").write_text("package main\n\nfunc main() {\n}\n")

    return tmp_path


@pytest.fixture
def project_ctx(tmp_project: Path) -> ProjectContext:
    """Create a ProjectContext from the tmp_project fixture."""
    return ProjectContext(tmp_project)
