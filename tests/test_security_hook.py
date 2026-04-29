"""Tests for hooks/security_hook.py — Tier 1 PostToolUse secret detector.

Focuses on the changes introduced by P2-2 (template-placeholder false
positive) and P2-3 (substring-matched EXCLUDE_PATHS pruning).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.security_hook import SECRET_REGEXES, _is_excluded_path, check_secrets


# ---------------------------------------------------------------------------
# 1. P2-3 — _is_excluded_path uses path components, not substring
# ---------------------------------------------------------------------------


class TestIsExcludedPath:
    @pytest.mark.parametrize(
        "path,expected",
        [
            # Exact .env names — allowed to contain secrets
            ("/proj/.env", True),
            ("/proj/.env.development", True),
            ("/proj/.env.production", True),
            # .env.example must still be checked (NOT excluded)
            ("/proj/.env.example", False),
            ("/proj/sub/.env.example", False),
            # Test file suffix / prefix
            ("/proj/server/handler_test.go", True),
            ("/proj/tests/test_handler.py", True),
            # Directory components
            ("/proj/server/fixtures/data.yaml", True),
            ("/proj/server/testdata/sample.go", True),
            ("/proj/web/src/__tests__/App.test.tsx", True),
            ("/proj/server/internal/mocks/repo.go", True),
            # Substring 'mock' in filename — must NOT be excluded (P2-3 fix)
            ("/proj/server/mockingbird/Real.go", False),
            ("/proj/server/mocker.go", False),
            # Real source code
            ("/proj/server/internal/handler.go", False),
            ("/proj/web/src/App.tsx", False),
        ],
    )
    def test_classification(self, path: str, expected: bool) -> None:
        assert _is_excluded_path(path) is expected


# ---------------------------------------------------------------------------
# 2. P2-2 — password regex skips template placeholders
# ---------------------------------------------------------------------------


def _password_pattern() -> str:
    """Return the password-detection regex string from SECRET_REGEXES."""
    for pattern, desc in SECRET_REGEXES:
        if "password" in pattern:
            return pattern
    raise AssertionError("password pattern not found in SECRET_REGEXES")


class TestPasswordRegex:
    @pytest.mark.parametrize(
        "line",
        [
            'password = "supersecret123"',  # bare hardcoded
            "password: 'verylongpassword'",  # yaml-ish
        ],
    )
    def test_real_secret_matches(self, line: str) -> None:
        import re

        assert re.search(_password_pattern(), line) is not None

    @pytest.mark.parametrize(
        "line",
        [
            'password = "${DB_PASSWORD}"',  # shell-style
            'password: "{{ env.DB_PASSWORD }}"',  # Go/Helm template
            'password: "{{ .Values.db.password }}"',  # Helm
            "password = '${DB_PASSWORD}'",
        ],
    )
    def test_template_placeholders_skipped(self, line: str) -> None:
        import re

        # P2-2: ${...} and {{...}} both excluded — neither should match.
        assert re.search(_password_pattern(), line) is None


# ---------------------------------------------------------------------------
# 3. End-to-end check_secrets behavior
# ---------------------------------------------------------------------------


class TestCheckSecrets:
    def test_excluded_path_returns_empty(self, tmp_path: Path) -> None:
        # File with a clear AWS key inside an exempt directory.
        excluded = tmp_path / "fixtures" / "creds.go"
        excluded.parent.mkdir(parents=True, exist_ok=True)
        excluded.write_text('var k = "AKIAIOSFODNN7EXAMPLE"\n')
        assert check_secrets(str(excluded)) == []

    def test_mockingbird_real_file_is_scanned(self, tmp_path: Path) -> None:
        # P2-3: substring 'mock' in filename should NOT exempt
        # the file from secret scanning (was a bug in the substring matcher).
        mockingbird = tmp_path / "mockingbird" / "Real.go"
        mockingbird.parent.mkdir(parents=True, exist_ok=True)
        mockingbird.write_text('var k = "AKIAIOSFODNN7EXAMPLE"\n')
        # Wait — directory is named 'mockingbird' which is NOT in
        # _EXCLUDE_DIRS, so it should be scanned. Confirm an AWS key is
        # detected.
        findings = check_secrets(str(mockingbird))
        assert any(f["rule"] == "V08-HARDCODED-SECRET" for f in findings)

    def test_template_placeholder_no_finding(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text('password: "{{ env.DB_PASSWORD }}"\n')
        assert check_secrets(str(cfg)) == []

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        assert check_secrets(str(tmp_path / "ghost.go")) == []
