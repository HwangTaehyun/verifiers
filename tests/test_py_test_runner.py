"""Tests for hooks/validators/py_test_runner.py — V11 Python Test Runner."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.py_test_runner import PyTestRunnerValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> PyTestRunnerValidator:
    return PyTestRunnerValidator()


@pytest.fixture(autouse=True)
def clean_tracker(tmp_path: Path) -> None:
    """Reset the failure tracker for each test."""
    import hooks.validators.py_test_runner as mod

    mod.FAILURE_TRACKER = tmp_path / "test-failure-tracker.json"


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    """Create a Python project structure."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "auth").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def py_ctx(py_project: Path):
    """Create a ProjectContext for a Python project."""
    from lib.project_context import ProjectContext

    return ProjectContext(py_project)


# ---------------------------------------------------------------------------
# 1. should_run — Python file pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    """Verify should_run accepts Python file patterns."""

    def test_py_file_matches(self, validator: PyTestRunnerValidator) -> None:
        assert validator.should_run("src/handler.py") is True

    def test_test_file_matches(self, validator: PyTestRunnerValidator) -> None:
        assert validator.should_run("tests/test_handler.py") is True

    def test_go_file_no_match(self, validator: PyTestRunnerValidator) -> None:
        assert validator.should_run("server/main.go") is False

    def test_ts_file_no_match(self, validator: PyTestRunnerValidator) -> None:
        assert validator.should_run("web/src/App.tsx") is False


# ---------------------------------------------------------------------------
# 2. _find_python_root — project detection
# ---------------------------------------------------------------------------


class TestFindPythonRoot:
    """Test Python project root detection."""

    def test_pyproject_toml(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        root = validator._find_python_root(py_ctx)
        assert root is not None
        assert (root / "pyproject.toml").exists()

    def test_no_python_project(self, validator: PyTestRunnerValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)
        root = validator._find_python_root(ctx)
        assert root is None

    def test_requirements_txt(self, validator: PyTestRunnerValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "requirements.txt").write_text("pytest\n")
        ctx = _make_ctx(tmp_path)
        root = validator._find_python_root(ctx)
        assert root is not None


# ---------------------------------------------------------------------------
# 3. _is_test_file — test file detection
# ---------------------------------------------------------------------------


class TestIsTestFile:
    """Test Python test file detection."""

    def test_test_prefix(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("test_handler.py") is True

    def test_test_suffix(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("handler_test.py") is True

    def test_tests_dir(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("tests/test_handler.py") is True

    def test_source_file_not_test(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("src/handler.py") is False

    def test_conftest_not_test(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("tests/conftest.py") is False

    def test_init_not_test(self, validator: PyTestRunnerValidator) -> None:
        assert validator._is_test_file("tests/__init__.py") is False


# ---------------------------------------------------------------------------
# 4. _resolve_test_file — test file resolution
# ---------------------------------------------------------------------------


class TestResolveTestFile:
    """Test source → test file resolution."""

    def test_same_dir_test_prefix(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        (py_project / "src" / "handler.py").write_text("def handle(): pass\n")
        (py_project / "src" / "test_handler.py").write_text("def test_handle(): pass\n")

        result = validator._resolve_test_file(py_ctx.project_root, str(py_project / "src" / "handler.py"))
        assert result is not None
        assert "test_handler.py" in result

    def test_same_dir_test_suffix(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        (py_project / "src" / "handler.py").write_text("def handle(): pass\n")
        (py_project / "src" / "handler_test.py").write_text("def test_handle(): pass\n")

        result = validator._resolve_test_file(py_ctx.project_root, str(py_project / "src" / "handler.py"))
        assert result is not None
        assert "handler_test.py" in result

    def test_tests_dir(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        (py_project / "src" / "handler.py").write_text("def handle(): pass\n")
        (py_project / "tests" / "test_handler.py").write_text("def test_handle(): pass\n")

        result = validator._resolve_test_file(py_ctx.project_root, str(py_project / "src" / "handler.py"))
        assert result is not None
        assert "tests/test_handler.py" in result

    def test_no_test_found(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        (py_project / "src" / "orphan.py").write_text("def orphan(): pass\n")

        result = validator._resolve_test_file(py_ctx.project_root, str(py_project / "src" / "orphan.py"))
        assert result is None


# ---------------------------------------------------------------------------
# 5. _run_test_file — mocked subprocess
# ---------------------------------------------------------------------------


class TestRunTestFile:
    """Test pytest execution with mocked subprocess."""

    def test_tests_pass_no_findings(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["uv", "run", "pytest"],
            returncode=0,
            stdout="PASSED tests/test_handler.py::test_foo\n1 passed\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_test_file(py_project, "tests/test_handler.py")
        assert findings == []

    def test_test_failure(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["uv", "run", "pytest"],
            returncode=1,
            stdout="FAILED tests/test_handler.py::test_create_user - AssertionError\n1 failed\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_test_file(py_project, "tests/test_handler.py")

        test_fail_findings = [f for f in findings if f.rule == "V11-TEST-FAIL"]
        assert len(test_fail_findings) == 1
        assert test_fail_findings[0].severity == "error"
        assert "test_handler.py" in test_fail_findings[0].message

    def test_pytest_not_installed(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._run_test_file(py_project, "tests/test_handler.py")
        assert findings == []

    def test_timeout(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=60),
        ):
            findings = validator._run_test_file(py_project, "tests/test_handler.py")
        assert findings == []


# ---------------------------------------------------------------------------
# 6. _check_test_exists — warning for missing tests
# ---------------------------------------------------------------------------


class TestCheckTestExists:
    """Test warning generation for missing test files."""

    def test_source_file_warns(self, validator: PyTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/handler.py")
        assert len(findings) == 1
        assert findings[0].rule == "V11-NO-TEST"
        assert findings[0].severity == "warning"

    def test_init_file_skipped(self, validator: PyTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/__init__.py")
        assert findings == []

    def test_conftest_skipped(self, validator: PyTestRunnerValidator) -> None:
        findings = validator._check_test_exists("tests/conftest.py")
        assert findings == []

    def test_setup_py_skipped(self, validator: PyTestRunnerValidator) -> None:
        findings = validator._check_test_exists("setup.py")
        assert findings == []

    def test_private_file_skipped(self, validator: PyTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/_internal.py")
        assert findings == []


# ---------------------------------------------------------------------------
# 7. _track_failure — repeated failure detection
# ---------------------------------------------------------------------------


class TestTrackFailure:
    """Test consecutive failure tracking."""

    def test_first_failure_returns_1(self, validator: PyTestRunnerValidator) -> None:
        count = validator._track_failure("test_foo", passed=False)
        assert count == 1

    def test_consecutive_failures_increment(self, validator: PyTestRunnerValidator) -> None:
        validator._track_failure("test_foo", passed=False)
        validator._track_failure("test_foo", passed=False)
        count = validator._track_failure("test_foo", passed=False)
        assert count == 3

    def test_pass_resets_counter(self, validator: PyTestRunnerValidator) -> None:
        validator._track_failure("test_foo", passed=False)
        validator._track_failure("test_foo", passed=False)
        count = validator._track_failure("test_foo", passed=True)
        assert count == 0


# ---------------------------------------------------------------------------
# 8. Repeated failure warning generation
# ---------------------------------------------------------------------------


class TestRepeatedFailWarning:
    """Test that V11-REPEATED-FAIL is generated after threshold."""

    def test_repeated_fail_after_threshold(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        # Pre-seed 2 failures — use the parsed test name (without "FAILED " prefix)
        # _parse_test_failures extracts "tests/test_handler.py::test_create" from "FAILED tests/..."
        validator._track_failure("tests/test_handler.py::test_create", passed=False)
        validator._track_failure("tests/test_handler.py::test_create", passed=False)

        # Third failure via _run_test_file
        mock_result = subprocess.CompletedProcess(
            args=["uv", "run", "pytest"],
            returncode=1,
            stdout="FAILED tests/test_handler.py::test_create - AssertionError\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_test_file(py_project, "tests/test_handler.py")

        repeated_findings = [f for f in findings if f.rule == "V11-REPEATED-FAIL"]
        assert len(repeated_findings) == 1
        assert "3 consecutive times" in repeated_findings[0].message
        assert "/tdd-update" in repeated_findings[0].fix


# ---------------------------------------------------------------------------
# 9. validate — integration
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Test the full validate method."""

    def test_no_python_project(self, validator: PyTestRunnerValidator, tmp_path: Path) -> None:
        """Non-Python project should return empty findings."""
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)
        result = validator.validate(ctx, file_path="src/foo.py", mode="post_tool_use")
        assert result.findings == []

    def test_excluded_dir_skipped(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        result = validator.validate(
            py_ctx,
            file_path=str(py_project / "__pycache__" / "handler.py"),
            mode="post_tool_use",
        )
        assert result.findings == []

    def test_stop_mode_skipped(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        result = validator.validate(py_ctx, file_path="handler.py", mode="stop")
        assert result.findings == []

    def test_non_py_file_skipped(self, validator: PyTestRunnerValidator, py_project: Path, py_ctx) -> None:
        result = validator.validate(py_ctx, file_path="README.md", mode="post_tool_use")
        assert result.findings == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path):
    """Create a minimal ProjectContext for testing."""
    from lib.project_context import ProjectContext

    return ProjectContext(tmp_path)
