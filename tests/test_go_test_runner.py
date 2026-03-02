"""Tests for hooks/validators/go_test_runner.py — V09 Go Test Runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.base import ValidationResult
from hooks.validators.go_test_runner import GoTestRunnerValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> GoTestRunnerValidator:
    return GoTestRunnerValidator()


@pytest.fixture(autouse=True)
def clean_tracker(tmp_path: Path) -> None:
    """Reset the failure tracker for each test."""
    import hooks.validators.go_test_runner as mod

    mod.FAILURE_TRACKER = tmp_path / "test-failure-tracker.json"


# ---------------------------------------------------------------------------
# 1. should_run — Go file pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    """Verify should_run accepts Go-related file patterns."""

    def test_go_file_matches(self, validator: GoTestRunnerValidator) -> None:
        assert validator.should_run("server/main.go") is True

    def test_nested_go_file_matches(self, validator: GoTestRunnerValidator) -> None:
        assert validator.should_run("server/internal/handler.go") is True

    def test_go_mod_matches(self, validator: GoTestRunnerValidator) -> None:
        assert validator.should_run("server/go.mod") is True

    def test_python_file_no_match(self, validator: GoTestRunnerValidator) -> None:
        assert validator.should_run("app.py") is False

    def test_ts_file_no_match(self, validator: GoTestRunnerValidator) -> None:
        assert validator.should_run("component.tsx") is False


# ---------------------------------------------------------------------------
# 2. validate — server_dir does not exist
# ---------------------------------------------------------------------------


class TestValidateNoServerDir:
    """When server_dir is None or missing, validate returns empty findings."""

    def test_server_dir_is_none(self, validator: GoTestRunnerValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)
        ctx.server_dir = None  # type: ignore[assignment]
        result = validator.validate(ctx, file_path="main.go", mode="post_tool_use")
        assert isinstance(result, ValidationResult)
        assert result.findings == []

    def test_server_dir_does_not_exist(self, validator: GoTestRunnerValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)
        ctx.server_dir = tmp_path / "server_nonexistent"
        result = validator.validate(ctx, file_path="main.go", mode="post_tool_use")
        assert result.findings == []


# ---------------------------------------------------------------------------
# 3. _resolve_test_package — test file existence
# ---------------------------------------------------------------------------


class TestResolveTestPackage:
    """Test Go package resolution and test file detection."""

    def test_with_test_file_present(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        pkg_dir = tmp_project / "server" / "internal"
        (pkg_dir / "handler.go").write_text("package internal\n")
        (pkg_dir / "handler_test.go").write_text("package internal\n")

        result = validator._resolve_test_package(
            project_ctx, str(pkg_dir / "handler.go")
        )
        assert result is not None
        assert "internal" in result

    def test_without_test_file(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        pkg_dir = tmp_project / "server" / "internal"
        (pkg_dir / "handler.go").write_text("package internal\n")
        # No _test.go file

        result = validator._resolve_test_package(
            project_ctx, str(pkg_dir / "handler.go")
        )
        assert result is None


# ---------------------------------------------------------------------------
# 4. _run_package_tests — mocked subprocess
# ---------------------------------------------------------------------------


class TestRunPackageTests:
    """Test go test execution with JSON output parsing."""

    def test_all_tests_pass(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        json_output = (
            '{"Test":"TestFoo","Action":"pass","Package":"mypackage"}\n'
            '{"Test":"TestBar","Action":"pass","Package":"mypackage"}\n'
        )
        mock_result = subprocess.CompletedProcess(
            args=["go", "test"],
            returncode=0,
            stdout=json_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")
        assert findings == []

    def test_test_failure(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        json_output = (
            '{"Test":"TestCreateUser","Action":"fail","Package":"mypackage"}\n'
        )
        mock_result = subprocess.CompletedProcess(
            args=["go", "test"],
            returncode=1,
            stdout=json_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V09-TEST-FAIL"
        assert f.severity == "error"
        assert "TestCreateUser" in f.message

    def test_multiple_failures(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        json_output = (
            '{"Test":"TestA","Action":"fail","Package":"mypackage"}\n'
            '{"Test":"TestB","Action":"fail","Package":"mypackage"}\n'
        )
        mock_result = subprocess.CompletedProcess(
            args=["go", "test"],
            returncode=1,
            stdout=json_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")

        # 2 failures → 1 V09-TEST-FAIL finding (aggregated)
        test_fail_findings = [f for f in findings if f.rule == "V09-TEST-FAIL"]
        assert len(test_fail_findings) == 1
        assert "TestA" in test_fail_findings[0].message
        assert "TestB" in test_fail_findings[0].message

    def test_go_not_installed(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")
        assert findings == []

    def test_timeout(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="go", timeout=60)):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")
        assert findings == []


# ---------------------------------------------------------------------------
# 5. _check_test_exists — warning for missing tests
# ---------------------------------------------------------------------------


class TestCheckTestExists:
    """Test warning generation for missing test files."""

    def test_no_go_files_no_warning(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        # Empty package directory — no warning needed
        empty_dir = tmp_project / "server" / "empty_pkg"
        empty_dir.mkdir(parents=True)

        findings = validator._check_test_exists(project_ctx, str(empty_dir / "nope.go"))
        # The file doesn't exist, but the directory check should still work
        # Since go_files will be empty (nope.go doesn't exist on disk), no warning
        assert findings == []

    def test_go_files_without_tests_warns(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        pkg_dir = tmp_project / "server" / "internal"
        (pkg_dir / "handler.go").write_text("package internal\n")

        findings = validator._check_test_exists(project_ctx, str(pkg_dir / "handler.go"))
        assert len(findings) == 1
        assert findings[0].rule == "V09-NO-TEST"
        assert findings[0].severity == "warning"


# ---------------------------------------------------------------------------
# 6. _track_failure — repeated failure detection
# ---------------------------------------------------------------------------


class TestTrackFailure:
    """Test consecutive failure tracking."""

    def test_first_failure_returns_1(self, validator: GoTestRunnerValidator) -> None:
        count = validator._track_failure("TestFoo", passed=False)
        assert count == 1

    def test_consecutive_failures_increment(self, validator: GoTestRunnerValidator) -> None:
        validator._track_failure("TestFoo", passed=False)
        validator._track_failure("TestFoo", passed=False)
        count = validator._track_failure("TestFoo", passed=False)
        assert count == 3

    def test_pass_resets_counter(self, validator: GoTestRunnerValidator) -> None:
        validator._track_failure("TestFoo", passed=False)
        validator._track_failure("TestFoo", passed=False)
        count = validator._track_failure("TestFoo", passed=True)
        assert count == 0
        # Next failure starts from 1
        count = validator._track_failure("TestFoo", passed=False)
        assert count == 1

    def test_different_tests_tracked_independently(self, validator: GoTestRunnerValidator) -> None:
        validator._track_failure("TestA", passed=False)
        validator._track_failure("TestA", passed=False)
        validator._track_failure("TestB", passed=False)
        assert validator._track_failure("TestA", passed=False) == 3
        assert validator._track_failure("TestB", passed=False) == 2


# ---------------------------------------------------------------------------
# 7. Repeated failure warning generation
# ---------------------------------------------------------------------------


class TestRepeatedFailWarning:
    """Test that V09-REPEATED-FAIL is generated after threshold."""

    def test_repeated_fail_after_threshold(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        # Pre-seed 2 failures
        validator._track_failure("TestCreateUser", passed=False)
        validator._track_failure("TestCreateUser", passed=False)

        # Third failure via _run_package_tests
        json_output = '{"Test":"TestCreateUser","Action":"fail","Package":"mypackage"}\n'
        mock_result = subprocess.CompletedProcess(
            args=["go", "test"],
            returncode=1,
            stdout=json_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_package_tests(project_ctx, "./internal", "handler.go")

        repeated_findings = [f for f in findings if f.rule == "V09-REPEATED-FAIL"]
        assert len(repeated_findings) == 1
        assert "3 consecutive times" in repeated_findings[0].message
        assert "/tdd-update" in repeated_findings[0].fix


# ---------------------------------------------------------------------------
# 8. validate — integration with mode="post_tool_use"
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Test the full validate method."""

    def test_post_tool_use_with_test_file(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        """Modifying a _test.go file should run tests directly."""
        pkg_dir = tmp_project / "server" / "internal"
        (pkg_dir / "handler_test.go").write_text("package internal\n")

        json_output = '{"Test":"TestFoo","Action":"pass","Package":"internal"}\n'
        mock_result = subprocess.CompletedProcess(
            args=["go", "test"], returncode=0, stdout=json_output, stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            result = validator.validate(
                project_ctx,
                file_path=str(pkg_dir / "handler_test.go"),
                mode="post_tool_use",
            )

        assert isinstance(result, ValidationResult)

    def test_post_tool_use_excluded_dir(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        """Files in excluded dirs should produce no findings."""
        result = validator.validate(
            project_ctx,
            file_path=str(tmp_project / "server" / "vendor" / "lib.go"),
            mode="post_tool_use",
        )
        assert result.findings == []

    def test_stop_mode_skipped(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        """Stop mode should not run any tests (V06 handles that)."""
        result = validator.validate(project_ctx, file_path="handler.go", mode="stop")
        assert result.findings == []

    def test_non_go_file_skipped(
        self, validator: GoTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        """Non-.go files should produce no findings."""
        result = validator.validate(
            project_ctx, file_path="README.md", mode="post_tool_use"
        )
        assert result.findings == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path):
    """Create a minimal ProjectContext for testing."""
    from lib.project_context import ProjectContext

    return ProjectContext(tmp_path)
