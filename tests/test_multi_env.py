"""Tests for V22 — multi-env-consistency.

Covers the three rule families:
  - V22-NON-APP-PREFIX  — server env vars must use APP_ (or allowed prefix)
  - V22-ROOT-SERVER-DRIFT — root/.env.example mirrors server's APP_*
  - V22-VIPER-KEY-NO-ENV — server/config/*.yaml keys ↔ env-var declarations
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.multi_env import MultiEnvConsistencyValidator
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> MultiEnvConsistencyValidator:
    return MultiEnvConsistencyValidator()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal monorepo layout V22 understands: root + server/ + server/config/."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "config").mkdir()
    return tmp_path


@pytest.fixture
def ctx(repo: Path) -> ProjectContext:
    return ProjectContext(repo)


def _write_env(path: Path, body: str) -> None:
    path.write_text(body.strip() + "\n")


# ── 1. APP_ prefix enforcement ────────────────────────────────────────


class TestAppPrefix:
    def test_app_var_passes(self, validator, repo, ctx):
        _write_env(
            repo / "server" / ".env.example",
            "APP_DATABASE_PASSWORD=change-me\n",
        )
        findings = validator.validate_project(ctx)
        nonapp = [f for f in findings if f.rule == "V22-NON-APP-PREFIX"]
        assert nonapp == []

    def test_non_app_unrecognized_warns(self, validator, repo, ctx):
        _write_env(
            repo / "server" / ".env.example",
            "FOOBAR_KEY=value\n",
        )
        findings = validator.validate_project(ctx)
        nonapp = [f for f in findings if f.rule == "V22-NON-APP-PREFIX"]
        assert len(nonapp) == 1
        assert "FOOBAR_KEY" in nonapp[0].message

    def test_external_tool_prefix_exempt(self, validator, repo, ctx):
        # AIRFLOW_*, POSTGRES_*, HASURA_*, SF_* are tool-standard
        _write_env(
            repo / "server" / ".env.example",
            "AIRFLOW_FERNET_KEY=abc\n"
            "POSTGRES_PORT=5432\n"
            "HASURA_PORT=8080\n"
            "SF_CONSUMER_KEY=xyz\n"
            "_AIRFLOW_WWW_USER_USERNAME=airflow\n",
        )
        findings = validator.validate_project(ctx)
        nonapp = [f for f in findings if f.rule == "V22-NON-APP-PREFIX"]
        assert nonapp == []

    def test_allowed_bare_names_exempt(self, validator, repo, ctx):
        _write_env(
            repo / "server" / ".env.example",
            "DOMAIN=example.com\nAPI_DOMAIN=api.example.com\nPIPELINES_DLT_PATH=/external/dlt\n",
        )
        findings = validator.validate_project(ctx)
        nonapp = [f for f in findings if f.rule == "V22-NON-APP-PREFIX"]
        assert nonapp == []

    def test_no_env_file_no_findings(self, validator, ctx):
        # No server/.env.example → V22 short-circuits cleanly
        findings = validator.validate_project(ctx)
        assert all(f.rule != "V22-NON-APP-PREFIX" for f in findings)


# ── 2. root vs server .env.example drift ──────────────────────────────


class TestDrift:
    def test_in_sync_no_drift(self, validator, repo, ctx):
        _write_env(repo / ".env.example", "APP_DATABASE_PASSWORD=root-side\n")
        _write_env(repo / "server" / ".env.example", "APP_DATABASE_PASSWORD=server-side\n")
        findings = validator.validate_project(ctx)
        drift = [f for f in findings if f.rule == "V22-ROOT-SERVER-DRIFT"]
        assert drift == []

    def test_server_only_var_does_not_flag(self, validator, repo, ctx):
        # Server is the canonical source for APP_* vars; an APP_* present
        # in server/.env.example but absent from root/.env.example is
        # legitimate (root is for compose-orchestration vars, not APP_*).
        # The drift check is intentionally asymmetric — see V22 docstring.
        _write_env(repo / ".env.example", "APP_DATABASE_PASSWORD=x\n")
        _write_env(
            repo / "server" / ".env.example",
            "APP_DATABASE_PASSWORD=y\nAPP_NEW_FEATURE=z\n",
        )
        findings = validator.validate_project(ctx)
        drift = [f for f in findings if f.rule == "V22-ROOT-SERVER-DRIFT"]
        assert all("APP_NEW_FEATURE" not in f.message for f in drift)
        assert drift == []

    def test_root_only_var_flags_server(self, validator, repo, ctx):
        _write_env(
            repo / ".env.example",
            "APP_DATABASE_PASSWORD=x\nAPP_LEGACY_KEY=y\n",
        )
        _write_env(repo / "server" / ".env.example", "APP_DATABASE_PASSWORD=z\n")
        findings = validator.validate_project(ctx)
        drift = [f for f in findings if f.rule == "V22-ROOT-SERVER-DRIFT"]
        assert any("APP_LEGACY_KEY" in f.message for f in drift)

    def test_only_server_present_skips_drift(self, validator, repo, ctx):
        # No root .env.example → drift check no-op (nothing to compare)
        _write_env(repo / "server" / ".env.example", "APP_X=y\n")
        findings = validator.validate_project(ctx)
        assert all(f.rule != "V22-ROOT-SERVER-DRIFT" for f in findings)

    def test_non_app_vars_excluded_from_drift(self, validator, repo, ctx):
        # AIRFLOW_* legitimately on server only — must NOT drift-flag
        _write_env(repo / ".env.example", "APP_X=y\n")
        _write_env(
            repo / "server" / ".env.example",
            "APP_X=z\nAIRFLOW_FERNET_KEY=a\n",
        )
        findings = validator.validate_project(ctx)
        drift = [f for f in findings if f.rule == "V22-ROOT-SERVER-DRIFT"]
        assert all("AIRFLOW" not in f.message for f in drift)


# ── 3. Viper config-key ↔ env-var mapping ─────────────────────────────


class TestViperMapping:
    def test_canonical_key_with_env_passes(self, validator, repo, ctx):
        _write_env(
            repo / "server" / "config" / "app.yaml",
            "database:\n  password: ${APP_DATABASE_PASSWORD}\n",
        )
        _write_env(
            repo / "server" / ".env.example",
            "APP_DATABASE_PASSWORD=change-me\n",
        )
        findings = validator.validate_project(ctx)
        viper_findings = [f for f in findings if f.rule == "V22-VIPER-KEY-NO-ENV"]
        assert viper_findings == []

    def test_missing_env_for_yaml_key_warns(self, validator, repo, ctx):
        _write_env(
            repo / "server" / "config" / "app.yaml",
            "database:\n  password: hardcoded\n  host: localhost\n",
        )
        _write_env(
            repo / "server" / ".env.example",
            "APP_DATABASE_PASSWORD=change-me\n",
            # APP_DATABASE_HOST missing
        )
        findings = validator.validate_project(ctx)
        viper_findings = [f for f in findings if f.rule == "V22-VIPER-KEY-NO-ENV"]
        assert any("APP_DATABASE_HOST" in f.message for f in viper_findings)
        assert any("database.host" in f.message for f in viper_findings)

    def test_variant_files_skipped(self, validator, repo, ctx):
        # Only canonical app.yaml drives the check; .local / .docker are variants
        _write_env(
            repo / "server" / "config" / "app.local.yaml",
            "extra:\n  local_only: yes\n",
        )
        _write_env(repo / "server" / ".env.example", "APP_X=y\n")
        findings = validator.validate_project(ctx)
        viper_findings = [f for f in findings if f.rule == "V22-VIPER-KEY-NO-ENV"]
        assert all("local_only" not in f.message for f in viper_findings)

    def test_no_config_dir_no_findings(self, validator, repo, ctx):
        # server/config/ exists but is empty → no canonical files → no findings
        _write_env(repo / "server" / ".env.example", "APP_X=y\n")
        findings = validator.validate_project(ctx)
        assert all(f.rule != "V22-VIPER-KEY-NO-ENV" for f in findings)

    def test_dotted_to_underscore_translation(self, validator):
        from hooks.validators.multi_env import _viper_env_name

        assert _viper_env_name("database.password") == "APP_DATABASE_PASSWORD"
        assert _viper_env_name("auth.jwt.access_secret") == "APP_AUTH_JWT_ACCESS_SECRET"
        assert _viper_env_name("a-b.c") == "APP_A_B_C"
