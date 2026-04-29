"""Tests for V01: Environment & Config Validator (EnvConfigValidator).

Covers:
  - _check_secret_in_config: hardcoded passwords/secrets/tokens/API keys
  - _check_secret_in_config: skip comments and ${VAR} references
  - _check_env_example_completeness: ${VAR} in docker-compose not in .env.example
  - _check_env_example_completeness: skip ${VAR:-default} (has default)
  - _check_env_example_completeness: os.Getenv("APP_*") in Go code not in .env.example
  - _check_config_consistency: missing keys across config variants
  - _check_vite_env_sync: import.meta.env.VITE_* not defined in web/env/
"""

from __future__ import annotations

from pathlib import Path


from hooks.validators.env_config import EnvConfigValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _findings_by_rule(findings, rule: str):
    """Filter findings by rule id."""
    return [f for f in findings if f.rule == rule]


# ===========================================================================
# 1. _check_secret_in_config
# ===========================================================================


class TestCheckSecretInConfig:
    """V01-SECRET-IN-CONFIG: detect hardcoded secrets in config/*.yaml."""

    def test_detects_hardcoded_password(self, tmp_project: Path, project_ctx: ProjectContext):
        """A plain password value should be flagged as an error."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("database:\n  password: SuperSecret123\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1
        assert matched[0].severity == "error"
        assert "password" in matched[0].message.lower() or "Secret" in matched[0].message

    def test_detects_hardcoded_secret(self, tmp_project: Path, project_ctx: ProjectContext):
        """A plain secret value should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("app:\n  secret: myverysecretvalue\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1
        assert matched[0].severity == "error"

    def test_detects_hardcoded_api_key(self, tmp_project: Path, project_ctx: ProjectContext):
        """A hardcoded api_key should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("service:\n  api_key: sk-abcdefghij1234567890abcd\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1

    def test_detects_hardcoded_token(self, tmp_project: Path, project_ctx: ProjectContext):
        """A hardcoded token should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("auth:\n  token: ghp_ABCDEFGHIJKLMNOPQRSTuvwx\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1

    def test_detects_openai_key(self, tmp_project: Path, project_ctx: ProjectContext):
        """An OpenAI API key pattern (sk-...) should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("openai:\n  key: sk-ABCDEFGHIJKLMNOPQRST1234\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1

    def test_detects_github_token(self, tmp_project: Path, project_ctx: ProjectContext):
        """A GitHub personal access token (ghp_...) should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("github:\n  access: ghp_ABCDEFGHIJKLMNOPQRSTuvwx\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1

    def test_detects_aws_access_key(self, tmp_project: Path, project_ctx: ProjectContext):
        """An AWS access key ID (AKIA...) should be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("aws:\n  access_key_id: AKIAIOSFODNN7EXAMPLE\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1

    def test_detects_multiple_secrets_in_one_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """Multiple secrets in the same file should each produce a finding."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("database:\n  password: SuperSecret123\n  secret: AnotherSecret456\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 2

    def test_finding_includes_line_number(self, tmp_project: Path, project_ctx: ProjectContext):
        """Findings should contain the correct line number."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("app:\n  name: myapp\n  password: HardcodedVal123\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 1
        assert matched[0].line == 3

    def test_finding_includes_fix_suggestion(self, tmp_project: Path, project_ctx: ProjectContext):
        """Findings should include a fix suggestion referencing the env var."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("db:\n  password: HardcodedVal123\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) >= 1
        assert "APP_" in matched[0].fix
        assert ".env" in matched[0].fix

    # --- Cases that should NOT trigger ---

    def test_skips_comment_lines(self, tmp_project: Path, project_ctx: ProjectContext):
        """Lines starting with # should be ignored."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("# password: SuperSecret123\n# secret: MyOtherSecret456\nport: 8080\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 0

    def test_skips_env_var_references(self, tmp_project: Path, project_ctx: ProjectContext):
        """Values using ${VAR} substitution should be skipped (not hardcoded)."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text(
            "database:\n"
            "  password: ${APP_DB_PASSWORD}\n"
            "  secret: ${APP_SECRET}\n"
            "  api_key: ${APP_API_KEY}\n"
            "  token: ${APP_TOKEN}\n"
        )

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 0

    def test_skips_short_values(self, tmp_project: Path, project_ctx: ProjectContext):
        """Short password values (< 8 chars) should not be flagged by the pattern."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("database:\n  password: short\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 0

    def test_skips_empty_values(self, tmp_project: Path, project_ctx: ProjectContext):
        """Empty password values should not be flagged."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text('database:\n  password: ""\n')

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 0

    def test_skips_blank_lines(self, tmp_project: Path, project_ctx: ProjectContext):
        """Blank lines should be ignored."""
        config = tmp_project / "server" / "config" / "testproject.local.yaml"
        config.write_text("port: 8080\n\n\nname: myapp\n")

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(project_ctx)

        matched = _findings_by_rule(findings, "V01-SECRET-IN-CONFIG")
        assert len(matched) == 0

    def test_no_server_dir_returns_empty(self, tmp_path: Path):
        """When server_dir is None, no findings should be produced."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert ctx.server_dir is None

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(ctx)
        assert findings == []

    def test_no_config_dir_returns_empty(self, tmp_path: Path):
        """When config/ does not exist, no findings should be produced."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "server").mkdir()
        # No config/ directory
        ctx = ProjectContext(tmp_path)
        assert ctx.server_dir is not None

        validator = EnvConfigValidator()
        findings = validator._check_secret_in_config(ctx)
        assert findings == []


# ===========================================================================
# 2. _check_env_example_completeness
# ===========================================================================


class TestCheckEnvExampleCompleteness:
    """V01-ENV-MISSING: variables referenced but not in .env.example."""

    # --- docker-compose ${VAR} checks ---

    def test_detects_missing_docker_compose_var(self, tmp_project: Path, project_ctx: ProjectContext):
        """${VAR} in docker-compose not in .env.example should produce a warning."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\n"
            "services:\n"
            "  app:\n"
            "    image: myapp:${APP_VERSION}\n"
            "    environment:\n"
            "      - DB_HOST=${DB_HOST}\n"
        )
        # .env.example is empty
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        var_names = {f.message for f in matched}
        assert any("APP_VERSION" in m for m in var_names)
        assert any("DB_HOST" in m for m in var_names)
        assert all(f.severity == "warning" for f in matched)

    def test_no_finding_when_var_in_env_example(self, tmp_project: Path, project_ctx: ProjectContext):
        """${VAR} already listed in .env.example should not produce a finding."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\nservices:\n  app:\n    image: myapp:${APP_VERSION}\n"
        )
        (tmp_project / ".env.example").write_text("APP_VERSION=latest\n")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert not any("APP_VERSION" in f.message for f in matched)

    def test_skips_var_with_default(self, tmp_project: Path, project_ctx: ProjectContext):
        """${VAR:-default} should be skipped (has a default value)."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\nservices:\n  app:\n    image: myapp:${APP_VERSION:-latest}\n"
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert not any("APP_VERSION" in f.message for f in matched)

    def test_detects_var_without_default_but_skips_with_default(self, tmp_project: Path, project_ctx: ProjectContext):
        """Mix of ${VAR} and ${VAR:-default}: only the one without default is flagged."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\n"
            "services:\n"
            "  app:\n"
            "    environment:\n"
            "      - PORT=${PORT:-3000}\n"
            "      - SECRET=${SECRET_KEY}\n"
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        var_messages = [f.message for f in matched]
        assert any("SECRET_KEY" in m for m in var_messages)
        assert not any("PORT" in m for m in var_messages)

    def test_includes_line_number_for_docker_compose(self, tmp_project: Path, project_ctx: ProjectContext):
        """Findings from docker-compose should include correct line numbers."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\n"  # line 1
            "services:\n"  # line 2
            "  app:\n"  # line 3
            "    image: ${MY_IMAGE}\n"  # line 4
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert len(matched) >= 1
        assert matched[0].line == 4

    def test_includes_fix_suggestion(self, tmp_project: Path, project_ctx: ProjectContext):
        """Findings should include a fix suggestion pointing to .env.example."""
        (tmp_project / "docker-compose.yaml").write_text("version: '3'\nservices:\n  app:\n    image: ${MISSING_VAR}\n")
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert len(matched) >= 1
        assert "MISSING_VAR" in matched[0].fix
        assert ".env.example" in matched[0].fix

    # --- Go os.Getenv("APP_*") checks ---

    def test_detects_go_getenv_missing(self, tmp_project: Path, project_ctx: ProjectContext):
        """os.Getenv("APP_*") in Go code not in .env.example should be flagged."""
        go_file = tmp_project / "server" / "main.go"
        go_file.write_text(
            "package main\n\n"
            'import "os"\n\n'
            "func main() {\n"
            '    host := os.Getenv("APP_DB_HOST")\n'
            '    port := os.Getenv("APP_DB_PORT")\n'
            "    _ = host + port\n"
            "}\n"
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        var_messages = [f.message for f in matched]
        assert any("APP_DB_HOST" in m for m in var_messages)
        assert any("APP_DB_PORT" in m for m in var_messages)

    def test_go_getenv_not_flagged_when_in_env_example(self, tmp_project: Path, project_ctx: ProjectContext):
        """os.Getenv("APP_*") already in .env.example should not be flagged."""
        go_file = tmp_project / "server" / "main.go"
        go_file.write_text('package main\n\nimport "os"\n\nfunc main() {\n    host := os.Getenv("APP_DB_HOST")\n}\n')
        (tmp_project / ".env.example").write_text("APP_DB_HOST=localhost\n")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert not any("APP_DB_HOST" in f.message for f in matched)

    def test_go_getenv_non_app_prefix_skipped(self, tmp_project: Path, project_ctx: ProjectContext):
        """os.Getenv("HOME") (no APP_ prefix) should NOT be flagged."""
        go_file = tmp_project / "server" / "main.go"
        go_file.write_text(
            "package main\n\n"
            'import "os"\n\n'
            "func main() {\n"
            '    home := os.Getenv("HOME")\n'
            '    path := os.Getenv("PATH")\n'
            "    _ = home + path\n"
            "}\n"
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert not any("HOME" in f.message for f in matched)
        assert not any("PATH" in f.message for f in matched)

    def test_go_test_files_skipped(self, tmp_project: Path, project_ctx: ProjectContext):
        """_test.go files should be skipped entirely."""
        go_test = tmp_project / "server" / "main_test.go"
        go_test.write_text(
            'package main\n\nimport "os"\n\nfunc TestSomething() {\n    _ = os.Getenv("APP_TEST_SECRET")\n}\n'
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert not any("APP_TEST_SECRET" in f.message for f in matched)

    def test_go_getenv_includes_line_number(self, tmp_project: Path, project_ctx: ProjectContext):
        """Go getenv findings should include the correct line number."""
        go_file = tmp_project / "server" / "main.go"
        go_file.write_text(
            "package main\n"  # line 1
            "\n"  # line 2
            'import "os"\n'  # line 3
            "\n"  # line 4
            "func main() {\n"  # line 5
            '    _ = os.Getenv("APP_MY_VAR")\n'  # line 6
            "}\n"  # line 7
        )
        (tmp_project / ".env.example").write_text("")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        go_findings = [f for f in matched if "APP_MY_VAR" in f.message]
        assert len(go_findings) == 1
        assert go_findings[0].line == 6

    def test_env_example_comments_ignored(self, tmp_project: Path, project_ctx: ProjectContext):
        """Comments in .env.example should not count as defined variables."""
        (tmp_project / "docker-compose.yaml").write_text(
            "version: '3'\nservices:\n  app:\n    image: ${COMMENTED_VAR}\n"
        )
        (tmp_project / ".env.example").write_text("# COMMENTED_VAR=this_is_a_comment\nREAL_VAR=value\n")

        validator = EnvConfigValidator()
        findings = validator._check_env_example_completeness(project_ctx)

        matched = _findings_by_rule(findings, "V01-ENV-MISSING")
        assert any("COMMENTED_VAR" in f.message for f in matched)


# ===========================================================================
# 3. _check_config_consistency
# ===========================================================================


class TestCheckConfigConsistency:
    """V01-CONFIG-KEY-MISSING: detect missing keys across config variants."""

    def test_detects_missing_key_in_variant(self, tmp_project: Path, project_ctx: ProjectContext):
        """A key present in docker.yaml but missing in local.yaml should be flagged."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.docker.yaml").write_text("database:\n  host: db\n  port: 5432\n  ssl: true\n")
        (config_dir / "testproject.local.yaml").write_text("database:\n  host: localhost\n  port: 5432\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        assert len(matched) >= 1
        messages = [f.message for f in matched]
        assert any("database.ssl" in m for m in messages)
        # The finding should point to the local.yaml file
        assert any("testproject.local.yaml" in f.file for f in matched)

    def test_detects_missing_key_in_other_direction(self, tmp_project: Path, project_ctx: ProjectContext):
        """Keys in local.yaml but missing in docker.yaml should also be flagged."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.docker.yaml").write_text("database:\n  host: db\n")
        (config_dir / "testproject.local.yaml").write_text("database:\n  host: localhost\n  extra_setting: value\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        docker_findings = [f for f in matched if "testproject.docker.yaml" in f.file]
        assert any("database.extra_setting" in f.message for f in docker_findings)

    def test_no_findings_when_keys_consistent(self, tmp_project: Path, project_ctx: ProjectContext):
        """If both variants have the same keys, no findings should be produced."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.docker.yaml").write_text("database:\n  host: db\n  port: 5432\n")
        (config_dir / "testproject.local.yaml").write_text("database:\n  host: localhost\n  port: 5433\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        assert len(matched) == 0

    def test_no_findings_with_single_variant(self, tmp_project: Path, project_ctx: ProjectContext):
        """With only one config variant, no comparison is possible."""
        config_dir = tmp_project / "server" / "config"
        # Only testproject.local.yaml exists (created by fixture)
        # Remove any other variant files that might exist
        for f in config_dir.glob("testproject.*.yaml"):
            if f.name != "testproject.local.yaml":
                f.unlink()

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        assert len(matched) == 0

    def test_three_variants_mutual_comparison(self, tmp_project: Path, project_ctx: ProjectContext):
        """With 3 variants, keys unique to one should be flagged in the other two."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.local.yaml").write_text("port: 8080\nlocal_only: true\n")
        (config_dir / "testproject.docker.yaml").write_text("port: 8080\ndocker_only: true\n")
        (config_dir / "testproject.production.yaml").write_text("port: 8080\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        # local_only should be missing from docker and production
        # docker_only should be missing from local and production
        assert len(matched) >= 4  # 2 keys missing from 2 files each

    def test_nested_keys_flatten_correctly(self, tmp_project: Path, project_ctx: ProjectContext):
        """Nested YAML keys should be compared using dot-separated paths."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.docker.yaml").write_text(
            "server:\n  database:\n    host: db\n    pool:\n      max: 10\n"
        )
        (config_dir / "testproject.local.yaml").write_text("server:\n  database:\n    host: localhost\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        messages = [f.message for f in matched]
        assert any("server.database.pool.max" in m for m in messages)

    def test_severity_is_info(self, tmp_project: Path, project_ctx: ProjectContext):
        """Config consistency findings should have 'info' severity."""
        config_dir = tmp_project / "server" / "config"

        (config_dir / "testproject.docker.yaml").write_text("port: 8080\nextra: value\n")
        (config_dir / "testproject.local.yaml").write_text("port: 8080\n")

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(project_ctx)

        matched = _findings_by_rule(findings, "V01-CONFIG-KEY-MISSING")
        assert all(f.severity == "info" for f in matched)

    def test_no_project_name_returns_empty(self, tmp_path: Path):
        """When project_name cannot be detected, no findings should be produced."""
        (tmp_path / ".git").mkdir()
        # No server/config at all => project_name falls back to dir name
        # But no config dir => _check_config_consistency returns early
        (tmp_path / "server").mkdir()
        ctx = ProjectContext(tmp_path)

        validator = EnvConfigValidator()
        findings = validator._check_config_consistency(ctx)
        assert findings == []


# ===========================================================================
# 4. _check_vite_env_sync
# ===========================================================================


class TestCheckViteEnvSync:
    """V01-VITE-ENV-MISSING: import.meta.env.VITE_* not defined in web/env/."""

    def test_detects_missing_vite_var(self, tmp_project: Path, project_ctx: ProjectContext):
        """VITE_* used in code but not in web/env/ should be flagged."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text(
            "export const apiUrl = import.meta.env.VITE_API_URL;\n"
            "export const appName = import.meta.env.VITE_APP_NAME;\n"
        )
        # web/env/ is empty (no .env files)

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        var_messages = [f.message for f in matched]
        assert any("VITE_API_URL" in m for m in var_messages)
        assert any("VITE_APP_NAME" in m for m in var_messages)
        assert all(f.severity == "warning" for f in matched)

    def test_no_finding_when_var_defined_in_env(self, tmp_project: Path, project_ctx: ProjectContext):
        """VITE_* defined in web/env/.env.local should not be flagged."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("export const apiUrl = import.meta.env.VITE_API_URL;\n")
        env_file = tmp_project / "web" / "env" / ".env.local"
        env_file.write_text("VITE_API_URL=http://localhost:3000\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert not any("VITE_API_URL" in f.message for f in matched)

    def test_var_defined_in_any_env_file(self, tmp_project: Path, project_ctx: ProjectContext):
        """VITE_* defined in any web/env/.env* file should not be flagged."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text(
            "export const apiUrl = import.meta.env.VITE_API_URL;\nexport const mode = import.meta.env.VITE_MODE;\n"
        )
        (tmp_project / "web" / "env" / ".env").write_text("VITE_API_URL=http://api\n")
        (tmp_project / "web" / "env" / ".env.production").write_text("VITE_MODE=production\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert not any("VITE_API_URL" in f.message for f in matched)
        assert not any("VITE_MODE" in f.message for f in matched)

    def test_non_vite_prefix_ignored(self, tmp_project: Path, project_ctx: ProjectContext):
        """import.meta.env.MODE (no VITE_ prefix) should not be flagged."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("export const mode = import.meta.env.MODE;\nexport const dev = import.meta.env.DEV;\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert len(matched) == 0

    def test_scans_tsx_files(self, tmp_project: Path, project_ctx: ProjectContext):
        """Should also scan .tsx files for import.meta.env references."""
        tsx_file = tmp_project / "web" / "src" / "App.tsx"
        tsx_file.write_text(
            "const url = import.meta.env.VITE_BACKEND_URL;\n"
            "export default function App() { return <div>{url}</div>; }\n"
        )

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert any("VITE_BACKEND_URL" in f.message for f in matched)

    def test_scans_nested_directories(self, tmp_project: Path, project_ctx: ProjectContext):
        """Should recursively scan subdirectories of web/src/."""
        nested_dir = tmp_project / "web" / "src" / "components" / "deep"
        nested_dir.mkdir(parents=True)
        ts_file = nested_dir / "DeepComponent.tsx"
        ts_file.write_text("const key = import.meta.env.VITE_DEEP_KEY;\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert any("VITE_DEEP_KEY" in f.message for f in matched)

    def test_fix_suggestion_points_to_env_local(self, tmp_project: Path, project_ctx: ProjectContext):
        """The fix suggestion should point to web/env/.env.local."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("const x = import.meta.env.VITE_SOMETHING;\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert len(matched) >= 1
        assert "env/.env.local" in matched[0].fix

    def test_no_web_dir_returns_empty(self, tmp_path: Path):
        """When web_dir is None, no findings should be produced."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert ctx.web_dir is None

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(ctx)
        assert findings == []

    def test_no_src_dir_returns_empty(self, tmp_project: Path, project_ctx: ProjectContext):
        """When web/src/ does not exist, no findings should be produced."""
        import shutil

        shutil.rmtree(tmp_project / "web" / "src")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)
        assert findings == []

    def test_comments_in_env_files_ignored(self, tmp_project: Path, project_ctx: ProjectContext):
        """Commented-out definitions in env files should not count."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("const x = import.meta.env.VITE_COMMENTED;\n")
        env_file = tmp_project / "web" / "env" / ".env.local"
        env_file.write_text("# VITE_COMMENTED=some_value\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert any("VITE_COMMENTED" in f.message for f in matched)

    def test_partial_coverage(self, tmp_project: Path, project_ctx: ProjectContext):
        """Some vars defined, some not: only undefined ones should be flagged."""
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("const a = import.meta.env.VITE_DEFINED;\nconst b = import.meta.env.VITE_UNDEFINED;\n")
        env_file = tmp_project / "web" / "env" / ".env.local"
        env_file.write_text("VITE_DEFINED=yes\n")

        validator = EnvConfigValidator()
        findings = validator._check_vite_env_sync(project_ctx)

        matched = _findings_by_rule(findings, "V01-VITE-ENV-MISSING")
        assert not any("VITE_DEFINED" in f.message for f in matched)
        assert any("VITE_UNDEFINED" in f.message for f in matched)


# ===========================================================================
# 5. Full validate() integration
# ===========================================================================


class TestValidateIntegration:
    """Test the top-level validate() method produces a ValidationResult."""

    def test_clean_project_no_findings(self, tmp_project: Path, project_ctx: ProjectContext):
        """A clean project with no secrets/missing vars should have zero findings."""
        # The fixture creates a minimal clean project
        validator = EnvConfigValidator()
        result = validator.validate(project_ctx)

        assert result.validator_id == "V01-env-config"
        assert len(result.findings) == 0
        assert not result.has_errors
        assert not result.has_warnings

    def test_multiple_issues_combined(self, tmp_project: Path, project_ctx: ProjectContext):
        """Multiple types of issues should all appear in one validate() call."""
        config_dir = tmp_project / "server" / "config"

        # Secret in config
        (config_dir / "testproject.local.yaml").write_text(
            "database:\n  password: HardcodedVal123\n  host: localhost\n"
        )
        # Config inconsistency
        (config_dir / "testproject.docker.yaml").write_text("database:\n  host: db\n")

        # Missing docker-compose var
        (tmp_project / "docker-compose.yaml").write_text("version: '3'\nservices:\n  app:\n    image: ${MISSING_IMG}\n")

        # Missing VITE var
        ts_file = tmp_project / "web" / "src" / "config.ts"
        ts_file.write_text("const x = import.meta.env.VITE_MISSING;\n")

        validator = EnvConfigValidator()
        # Phase29+ API: project-level checks live in validate_project. The
        # legacy `validator.validate(ctx)` call no longer triggers the
        # full battery because the base dispatch needs an explicit mode
        # ("stop") or file_path (PostToolUse) to know which lane to fire.
        findings = validator.validate_project(project_ctx)

        rules_found = {f.rule for f in findings}
        assert "V01-SECRET-IN-CONFIG" in rules_found
        assert "V01-ENV-MISSING" in rules_found
        assert "V01-CONFIG-KEY-MISSING" in rules_found
        assert "V01-VITE-ENV-MISSING" in rules_found
        assert any(f.severity == "error" for f in findings)  # SECRET-IN-CONFIG is an error
        assert any(f.severity == "warning" for f in findings)  # ENV-MISSING and VITE-ENV-MISSING are warnings


# ===========================================================================
# 6. _flatten_keys helper
# ===========================================================================


class TestFlattenKeys:
    """Unit tests for the _flatten_keys static method."""

    def test_flat_dict(self):
        data = {"a": 1, "b": 2, "c": 3}
        keys = EnvConfigValidator._flatten_keys(data)
        assert sorted(keys) == ["a", "b", "c"]

    def test_nested_dict(self):
        data = {"server": {"host": "localhost", "port": 8080}}
        keys = EnvConfigValidator._flatten_keys(data)
        assert sorted(keys) == ["server.host", "server.port"]

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": {"d": "value"}}}}
        keys = EnvConfigValidator._flatten_keys(data)
        assert keys == ["a.b.c.d"]

    def test_mixed_nesting(self):
        data = {
            "top": "val",
            "nested": {"inner": "val2"},
            "deep": {"mid": {"bottom": "val3"}},
        }
        keys = EnvConfigValidator._flatten_keys(data)
        assert sorted(keys) == ["deep.mid.bottom", "nested.inner", "top"]

    def test_empty_dict(self):
        data: dict = {}
        keys = EnvConfigValidator._flatten_keys(data)
        assert keys == []
