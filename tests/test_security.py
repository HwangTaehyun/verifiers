"""Tests for V08: SecurityValidator — secrets, CORS, PHI logging, .gitignore.

Covers:
  - _check_secrets: AWS keys, GitHub tokens, OpenAI keys, Stripe keys,
    Slack tokens, hardcoded passwords; comment skipping; excluded paths.
  - _check_cors: AllowAllOrigins, Access-Control-Allow-Origin *, cors.Config wildcard.
  - _check_phi_logging: PHI fields in Go log statements and JS console statements.
  - _check_gitignore: missing .gitignore, missing required patterns.
  - Standalone main() function with mocked stdin/stdout.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.security import SecurityValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> SecurityValidator:
    """Create a SecurityValidator instance."""
    return SecurityValidator()


@pytest.fixture
def safe_tmp(tmp_path: Path) -> Path:
    """Create a temporary directory whose full path does NOT contain any EXCLUDE_PATHS substrings.

    Pytest names tmp_path directories after the test function (e.g. 'test_detect_aws...'),
    which contains 'test_' and triggers the SecurityValidator exclusion check since it
    does a substring match on the full file path.  This fixture creates a directory
    with a safe name to avoid false exclusions.
    """
    d = Path(tempfile.mkdtemp(prefix="secval_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write_file(base: Path, name: str, content: str) -> str:
    """Write a temp file and return its absolute path as a string."""
    fp = base / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    return str(fp)


# ============================================================================
# 1. _check_secrets
# ============================================================================


class TestCheckSecrets:
    """Tests for SecurityValidator._check_secrets."""

    # ── Detection tests ──────────────────────────────────────────────────

    def test_detect_aws_access_key(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "config.go", 'awsKey := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V08-HARDCODED-SECRET"
        assert "AWS Access Key" in findings[0].message
        assert findings[0].severity == "error"
        assert findings[0].line == 1

    def test_detect_github_personal_access_token(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        token = "ghp_" + "a" * 36
        fp = _write_file(safe_tmp, "deploy.sh", f'GITHUB_TOKEN="{token}"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "GitHub Personal Access Token" in findings[0].message

    def test_detect_github_oauth_token(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        token = "gho_" + "B" * 36
        fp = _write_file(safe_tmp, "auth.go", f'oauthToken := "{token}"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "GitHub OAuth Token" in findings[0].message

    def test_detect_openai_api_key(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "client.py", 'api_key = "sk-abcdefghij1234567890abcdefghij"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "OpenAI/Anthropic API Key" in findings[0].message

    def test_detect_stripe_live_key(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "billing.go", 'stripe.Key = "sk_live_abc123def456ghi789jkl"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "Stripe Live Key" in findings[0].message

    def test_detect_slack_bot_token(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "notify.go", 'token := "xoxb-1234-5678-abcde"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "Slack Bot Token" in findings[0].message

    def test_detect_hardcoded_password(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "db.go", 'password = "supersecretpassword123"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "Hardcoded password" in findings[0].message

    def test_detect_hardcoded_password_colon_syntax(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "config.yaml", 'password: "my_database_pass"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert "Hardcoded password" in findings[0].message

    def test_multiple_secrets_on_different_lines(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        content = 'awsKey := "AKIAIOSFODNN7EXAMPLE"\ntoken := "xoxb-1234-5678-abcde"\n'
        fp = _write_file(safe_tmp, "multi.go", content)
        findings = validator._check_secrets(fp)
        assert len(findings) == 2
        assert findings[0].line == 1
        assert findings[1].line == 2

    def test_one_finding_per_line(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """Even if a line matches multiple patterns, only one finding per line."""
        # sk_live_ matches both "sk-" (OpenAI) and "sk_live_" (Stripe)
        fp = _write_file(safe_tmp, "combo.go", 'key := "sk_live_abcdefghij1234567890"\n')
        findings = validator._check_secrets(fp)
        # Should only produce one finding (the first matching pattern, then break)
        assert len(findings) == 1

    # ── Clean file (no secrets) ──────────────────────────────────────────

    def test_clean_file_no_findings(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "clean.go", 'func main() {\n\tfmt.Println("Hello")\n}\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    # ── Comment skipping ─────────────────────────────────────────────────

    def test_skip_double_slash_comment(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "code.go", '// awsKey := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_skip_hash_comment(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "code.py", '# api_key = "sk-abcdefghij1234567890abcdefghij"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_skip_asterisk_comment(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "code.go", ' * token := "xoxb-1234-5678-abcde"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_skip_block_comment_start(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "code.go", '/* password = "supersecretpassword123" */\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_skip_html_comment(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "page.html", '<!-- token := "xoxb-1234-5678-abcde" -->\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_non_comment_line_still_detected(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """A comment on one line should not suppress a finding on another line."""
        content = '// This is a comment with AKIAIOSFODNN7EXAMPLE\nawsKey := "AKIAIOSFODNN7EXAMPLE"\n'
        fp = _write_file(safe_tmp, "mixed.go", content)
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert findings[0].line == 2

    # ── Excluded paths ───────────────────────────────────────────────────

    def test_exclude_env_example(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, ".env.example", "AWS_KEY=AKIAIOSFODNN7EXAMPLE\n")
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_go_test_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "auth_test.go", 'token := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_python_test_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "test_auth.py", 'token = "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_fixtures_directory(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp / "fixtures", "data.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_testdata_directory(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp / "testdata", "sample.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_mock_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "mock_service.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_tests_directory(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp / "__tests__", "auth.ts", 'const key = "AKIAIOSFODNN7EXAMPLE";\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_generated_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "schema.gen.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_vendor_directory(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp / "vendor" / "lib", "dep.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_exclude_node_modules(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(
            safe_tmp / "node_modules" / "pkg",
            "index.js",
            'const key = "AKIAIOSFODNN7EXAMPLE";\n',
        )
        findings = validator._check_secrets(fp)
        assert findings == []

    # ── Edge cases ───────────────────────────────────────────────────────

    def test_nonexistent_file_returns_empty(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        findings = validator._check_secrets(str(safe_tmp / "nonexistent.go"))
        assert findings == []

    def test_finding_includes_fix_suggestion(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "app.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
        findings = validator._check_secrets(fp)
        assert len(findings) == 1
        assert ".env" in findings[0].fix
        assert "os.Getenv()" in findings[0].fix

    def test_password_with_env_var_not_flagged(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """Password values containing ${VAR} should not match (regex excludes ${ in value)."""
        fp = _write_file(safe_tmp, "config.go", 'password = "${DB_PASSWORD}"\n')
        findings = validator._check_secrets(fp)
        assert findings == []

    def test_short_password_not_flagged(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """Passwords shorter than 8 characters should not match."""
        fp = _write_file(safe_tmp, "config.go", 'password = "short"\n')
        findings = validator._check_secrets(fp)
        assert findings == []


# ============================================================================
# 2. _check_cors
# ============================================================================


class TestCheckCors:
    """Tests for SecurityValidator._check_cors."""

    def test_detect_allow_all_origins_true(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "router.go", "c := cors.New(cors.Options{AllowAllOrigins: true})\n")
        findings = validator._check_cors(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V08-CORS-WILDCARD"
        assert "allows all origins" in findings[0].message
        assert findings[0].severity == "error"

    def test_detect_access_control_allow_origin_wildcard(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "handler.go", 'w.Header().Set("Access-Control-Allow-Origin", "*")\n')
        findings = validator._check_cors(fp)
        assert len(findings) == 1
        assert "wildcard origin" in findings[0].message

    def test_detect_cors_config_wildcard(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        content = 'cors.Config{AllowOrigins: ["*"]}\n'
        fp = _write_file(safe_tmp, "server.go", content)
        findings = validator._check_cors(fp)
        assert len(findings) == 1
        assert "wildcard in config" in findings[0].message

    def test_cors_with_specific_origin_no_finding(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(
            safe_tmp,
            "router.go",
            'c := cors.New(cors.Options{AllowedOrigins: []string{"https://example.com"}})\n',
        )
        findings = validator._check_cors(fp)
        assert findings == []

    def test_cors_finding_includes_fix(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "server.go", "AllowAllOrigins: true\n")
        findings = validator._check_cors(fp)
        assert len(findings) == 1
        assert "APP_CORS_ORIGINS" in findings[0].fix

    def test_cors_yaml_file_supported(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "cors.yaml", "Access-Control-Allow-Origin: *\n")
        findings = validator._check_cors(fp)
        assert len(findings) == 1

    def test_cors_yml_file_supported(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "cors.yml", "Access-Control-Allow-Origin: *\n")
        findings = validator._check_cors(fp)
        assert len(findings) == 1

    def test_cors_ts_file_supported(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "server.ts", 'res.setHeader("Access-Control-Allow-Origin", "*");\n')
        findings = validator._check_cors(fp)
        assert len(findings) == 1

    def test_cors_tsx_file_supported(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "app.tsx", 'headers["Access-Control-Allow-Origin"] = "*";\n')
        findings = validator._check_cors(fp)
        assert len(findings) == 1

    def test_cors_js_file_supported(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "server.js", 'res.setHeader("Access-Control-Allow-Origin", "*");\n')
        findings = validator._check_cors(fp)
        assert len(findings) == 1

    def test_cors_ignores_unsupported_extensions(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """CORS check should skip files with unsupported extensions like .py, .txt."""
        fp = _write_file(safe_tmp, "cors.py", "AllowAllOrigins: true\n")
        findings = validator._check_cors(fp)
        assert findings == []

    def test_cors_nonexistent_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        findings = validator._check_cors(str(safe_tmp / "nonexistent.go"))
        assert findings == []

    def test_cors_multiple_findings_on_different_lines(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        content = 'AllowAllOrigins: true\nw.Header().Set("Access-Control-Allow-Origin", "*")\n'
        fp = _write_file(safe_tmp, "server.go", content)
        findings = validator._check_cors(fp)
        assert len(findings) == 2
        assert findings[0].line == 1
        assert findings[1].line == 2


# ============================================================================
# 3. _check_phi_logging
# ============================================================================


class TestCheckPhiLogging:
    """Tests for SecurityValidator._check_phi_logging."""

    # ── Go log patterns ──────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "log_func",
        ["log.Info", "log.Debug", "log.Warn", "log.Print", "log.Printf", "log.Error"],
    )
    def test_detect_phi_in_go_log_functions(
        self,
        validator: SecurityValidator,
        safe_tmp: Path,
        log_func: str,
    ) -> None:
        fp = _write_file(safe_tmp, "handler.go", f'{log_func}("patient_name: %s", name)\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V08-PHI-LOGGING"
        assert "patient_name" in findings[0].message
        assert findings[0].severity == "error"

    @pytest.mark.parametrize(
        "phi_field",
        [
            "patient_name",
            "patient_id",
            "ssn",
            "date_of_birth",
            "medical_record",
            "diagnosis",
            "phone_number",
            "email",
        ],
    )
    def test_detect_all_phi_fields_in_go_log(
        self,
        validator: SecurityValidator,
        safe_tmp: Path,
        phi_field: str,
    ) -> None:
        fp = _write_file(safe_tmp, "service.go", f'log.Info("data: {phi_field}")\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert phi_field in findings[0].message

    # ── JS/TS console patterns ───────────────────────────────────────────

    @pytest.mark.parametrize(
        "console_func",
        ["console.log", "console.debug", "console.info", "console.warn", "console.error"],
    )
    def test_detect_phi_in_js_console_functions(
        self,
        validator: SecurityValidator,
        safe_tmp: Path,
        console_func: str,
    ) -> None:
        fp = _write_file(safe_tmp, "component.ts", f'{console_func}("patient_id:", id);\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert "patient_id" in findings[0].message

    def test_detect_phi_in_tsx_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "page.tsx", 'console.log("ssn: " + user.ssn);\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert "ssn" in findings[0].message

    def test_detect_phi_in_js_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "app.js", 'console.warn("email:", user.email);\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert "email" in findings[0].message

    # ── Clean cases ──────────────────────────────────────────────────────

    def test_log_without_phi_no_finding(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "handler.go", 'log.Info("server started on port %d", port)\n')
        findings = validator._check_phi_logging(fp)
        assert findings == []

    def test_console_without_phi_no_finding(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "app.ts", 'console.log("Application initialized");\n')
        findings = validator._check_phi_logging(fp)
        assert findings == []

    def test_phi_field_outside_log_no_finding(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """PHI field in non-log code should not trigger a finding."""
        fp = _write_file(safe_tmp, "model.go", "patientName := record.patient_name\n")
        findings = validator._check_phi_logging(fp)
        assert findings == []

    def test_phi_ignores_unsupported_extensions(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "data.py", 'log.Info("patient_name: test")\n')
        findings = validator._check_phi_logging(fp)
        assert findings == []

    def test_phi_nonexistent_file(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        findings = validator._check_phi_logging(str(safe_tmp / "nonexistent.go"))
        assert findings == []

    def test_one_phi_finding_per_line(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        """Even if multiple PHI fields are on one line, only one finding per line."""
        fp = _write_file(
            safe_tmp,
            "handler.go",
            'log.Info("patient_name: %s, ssn: %s", name, ssn)\n',
        )
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1

    def test_phi_fix_suggestion_go(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "handler.go", 'log.Info("email: test@example.com")\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert "log.Debug" in findings[0].fix or "masking" in findings[0].fix

    def test_phi_fix_suggestion_js(self, validator: SecurityValidator, safe_tmp: Path) -> None:
        fp = _write_file(safe_tmp, "app.ts", 'console.log("email: test@example.com");\n')
        findings = validator._check_phi_logging(fp)
        assert len(findings) == 1
        assert "Mask" in findings[0].fix or "sensitive" in findings[0].fix.lower()


# ============================================================================
# 4. _check_gitignore
# ============================================================================


class TestCheckGitignore:
    """Tests for SecurityValidator._check_gitignore."""

    def test_missing_gitignore_file(self, validator: SecurityValidator, tmp_path: Path) -> None:
        """Project with no .gitignore should produce a V08-NO-GITIGNORE error."""
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V08-NO-GITIGNORE"
        assert findings[0].severity == "error"
        assert ".gitignore" in findings[0].message

    def test_complete_gitignore_no_findings(self, validator: SecurityValidator, tmp_project: Path) -> None:
        """The tmp_project fixture has a complete .gitignore, so no findings."""
        ctx = _make_ctx(tmp_project)
        findings = validator._check_gitignore(ctx)
        assert findings == []

    def test_missing_env_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        """Remove .env entirely (not even as substring) to ensure it is flagged."""
        (tmp_path / ".git").mkdir()
        # Omit all patterns containing '.env' to test that both .env and .env.local are flagged
        (tmp_path / ".gitignore").write_text("*.pem\n*.key\n*.p12\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        # Should flag both .env and .env.local (both missing)
        messages = [f.message for f in findings]
        assert any("'.env'" in m for m in messages)
        assert any("'.env.local'" in m for m in messages)
        assert len(findings) == 2
        assert all(f.rule == "V08-GITIGNORE-MISSING" for f in findings)
        assert all(f.severity == "warning" for f in findings)

    def test_missing_pem_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".env\n*.key\n.env.local\n*.p12\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert "*.pem" in findings[0].message

    def test_missing_key_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".env\n*.pem\n.env.local\n*.p12\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert "*.key" in findings[0].message

    def test_missing_multiple_patterns(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("# just a comment\nnode_modules/\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        # All 5 required patterns are missing: .env, *.pem, *.key, .env.local, *.p12
        assert len(findings) == 5
        rules = {f.rule for f in findings}
        assert rules == {"V08-GITIGNORE-MISSING"}

    def test_empty_gitignore_all_missing(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 5

    def test_gitignore_fix_includes_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        # Only *.pem missing
        (tmp_path / ".gitignore").write_text(".env\n*.key\n.env.local\n*.p12\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert "*.pem" in findings[0].fix

    def test_no_gitignore_fix_lists_required_patterns(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert ".env" in findings[0].fix
        assert "*.pem" in findings[0].fix

    def test_missing_p12_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text(".env\n*.pem\n*.key\n.env.local\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert "*.p12" in findings[0].message

    def test_missing_env_local_pattern(self, validator: SecurityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        # .env is present but .env.local is not
        # Note: the check is `pattern not in content` (substring), so .env.local as a
        # substring would satisfy .env. We write .env on its own line without .env.local.
        (tmp_path / ".gitignore").write_text(".env\n*.pem\n*.key\n*.p12\n")
        ctx = _make_ctx(tmp_path)

        findings = validator._check_gitignore(ctx)
        assert len(findings) == 1
        assert ".env.local" in findings[0].message


# ============================================================================
# 5. validate() integration
# ============================================================================


class TestValidateIntegration:
    """Tests for the top-level validate() method."""

    def test_validate_with_file_path_runs_per_file_checks(
        self,
        validator: SecurityValidator,
    ) -> None:
        """Use safe_tmp to create a project where file paths do not match EXCLUDE_PATHS."""
        project = Path(tempfile.mkdtemp(prefix="secval_proj_"))
        try:
            (project / ".git").mkdir()
            (project / "server" / "config").mkdir(parents=True)
            (project / "server" / "config" / "app.local.yaml").write_text("port: 8080\n")
            (project / ".gitignore").write_text(".env\n*.pem\n*.key\n.env.local\n*.p12\n")

            fp = _write_file(project / "server", "handler.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
            ctx = _make_ctx(project)
            result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
            assert result.validator_id == "V08-security"
            assert result.has_errors
            assert any(f.rule == "V08-HARDCODED-SECRET" for f in result.findings)
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_validate_without_file_path_runs_project_checks(
        self,
        validator: SecurityValidator,
        tmp_path: Path,
    ) -> None:
        """Without file_path, validate should run project-wide checks (_check_gitignore)."""
        (tmp_path / ".git").mkdir()
        # No .gitignore
        ctx = _make_ctx(tmp_path)
        result = validator.validate(ctx, file_path=None, mode="stop")
        assert any(f.rule == "V08-NO-GITIGNORE" for f in result.findings)

    def test_validate_clean_project(self, validator: SecurityValidator, tmp_project: Path) -> None:
        ctx = _make_ctx(tmp_project)
        result = validator.validate(ctx, file_path=None, mode="stop")
        # The tmp_project has a complete .gitignore and no source files with secrets
        assert not result.has_errors

    def test_validate_combined_findings(self, validator: SecurityValidator) -> None:
        """A Go file with both a secret and a CORS issue should produce multiple findings."""
        project = Path(tempfile.mkdtemp(prefix="secval_combo_"))
        try:
            (project / ".git").mkdir()
            (project / "server" / "config").mkdir(parents=True)
            (project / "server" / "config" / "app.local.yaml").write_text("port: 8080\n")
            (project / ".gitignore").write_text(".env\n*.pem\n*.key\n.env.local\n*.p12\n")

            content = 'key := "AKIAIOSFODNN7EXAMPLE"\nAllowAllOrigins: true\nlog.Info("patient_name: %s", name)\n'
            fp = _write_file(project / "server", "bad.go", content)
            ctx = _make_ctx(project)
            result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
            rules = {f.rule for f in result.findings}
            assert "V08-HARDCODED-SECRET" in rules
            assert "V08-CORS-WILDCARD" in rules
            assert "V08-PHI-LOGGING" in rules
        finally:
            shutil.rmtree(project, ignore_errors=True)


# ============================================================================
# 6. Standalone main() function
# ============================================================================


class TestMain:
    """Tests for the standalone main() entry point."""

    def _make_project(self) -> Path:
        """Create a temporary project directory with safe path for main() tests."""
        project = Path(tempfile.mkdtemp(prefix="secval_main_"))
        (project / ".git").mkdir()
        (project / "server" / "config").mkdir(parents=True)
        (project / "server" / "config" / "app.local.yaml").write_text("port: 8080\n")
        (project / ".gitignore").write_text(".env\n*.pem\n*.key\n.env.local\n*.p12\n")
        return project

    def test_main_with_edit_tool_and_secret(self) -> None:
        project = self._make_project()
        try:
            fp = _write_file(project / "server", "config.go", 'key := "AKIAIOSFODNN7EXAMPLE"\n')
            input_data = {
                "tool_name": "Edit",
                "tool_input": {"file_path": fp},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            assert "additionalContext" in output
            assert "V08-HARDCODED-SECRET" in output["additionalContext"]
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_main_with_write_tool_and_secret(self) -> None:
        project = self._make_project()
        try:
            fp = _write_file(project / "server", "config.go", 'token := "xoxb-1234-5678-abcde"\n')
            input_data = {
                "tool_name": "Write",
                "tool_input": {"file_path": fp},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            assert "additionalContext" in output
            assert "V08-HARDCODED-SECRET" in output["additionalContext"]
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_main_with_multiedit_tool(self) -> None:
        project = self._make_project()
        try:
            fp = _write_file(project / "server", "cors.go", "AllowAllOrigins: true\n")
            input_data = {
                "tool_name": "MultiEdit",
                "tool_input": {"file_path": fp},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            assert "additionalContext" in output
            assert "V08-CORS-WILDCARD" in output["additionalContext"]
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_main_ignores_non_edit_tools(self) -> None:
        project = self._make_project()
        try:
            input_data = {
                "tool_name": "Read",
                "tool_input": {"file_path": "/some/file.go"},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            assert output == {}
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_main_empty_input(self) -> None:
        stdout = _run_main(None)
        output = json.loads(stdout)
        assert output == {}

    def test_main_missing_file_path(self) -> None:
        project = self._make_project()
        try:
            input_data = {
                "tool_name": "Edit",
                "tool_input": {},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            assert output == {}
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_main_clean_file_no_context(self) -> None:
        project = self._make_project()
        try:
            fp = _write_file(project / "server", "clean.go", 'func main() { fmt.Println("ok") }\n')
            input_data = {
                "tool_name": "Edit",
                "tool_input": {"file_path": fp},
                "cwd": str(project),
            }
            stdout = _run_main(input_data)
            output = json.loads(stdout)
            # No findings means empty output (no additionalContext)
            assert output == {}
        finally:
            shutil.rmtree(project, ignore_errors=True)


# ============================================================================
# Helpers (module-level)
# ============================================================================


def _make_ctx(project_root: Path) -> ProjectContext:
    """Create a ProjectContext from a project root, using the fallback .git detection."""
    return ProjectContext(project_root)


def _run_main(input_data: dict | None) -> str:
    """Run the security validator main() with mocked stdin/stdout.

    Returns the captured stdout string.
    """
    from hooks.validators.security import main

    stdin_data = json.dumps(input_data) if input_data else ""

    captured: list[str] = []

    with mock.patch("sys.stdin", mock.Mock(read=mock.Mock(return_value=stdin_data))):
        with mock.patch(
            "builtins.print",
            side_effect=lambda *args, **kwargs: captured.append(
                " ".join(str(a) for a in args) + kwargs.get("end", "\n"),
            ),
        ):
            main()

    return "".join(captured).strip()
