"""Tests for hooks/validators/py_quality.py — V19 Python Quality (ruff + pytest).

Covers:
  - V19-RUFF-CHECK   (post_tool_use, single file)
  - V19-RUFF-FORMAT  (post_tool_use, single file)
  - V19-RUFF-ALL     (stop mode, project-wide; truncation at 20 findings)
  - V19-TEST-FAIL    (stop mode, pytest)

External commands (ruff, pytest) are mocked via subprocess.run patching
so the test suite never invokes the real binaries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.py_quality import PyQualityValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> PyQualityValidator:
    return PyQualityValidator()


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    """Minimal Python project layout: .git + pyproject.toml + src tree."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    (tmp_path / "src").mkdir()
    return tmp_path


@pytest.fixture
def py_ctx(py_project: Path) -> ProjectContext:
    return ProjectContext(py_project)


def _make_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["ruff"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# 1. should_run — file-pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    def test_py_file_matches(self, validator: PyQualityValidator) -> None:
        assert validator.should_run("src/handler.py") is True

    def test_pyproject_matches(self, validator: PyQualityValidator) -> None:
        # file_patterns uses '**/pyproject.toml' (fnmatch glob), so a path
        # with at least one preceding segment is required to match.
        assert validator.should_run("repo/pyproject.toml") is True

    def test_ruff_toml_matches(self, validator: PyQualityValidator) -> None:
        assert validator.should_run("repo/ruff.toml") is True

    def test_go_no_match(self, validator: PyQualityValidator) -> None:
        assert validator.should_run("main.go") is False

    def test_ts_no_match(self, validator: PyQualityValidator) -> None:
        assert validator.should_run("App.tsx") is False


# ---------------------------------------------------------------------------
# 2. _find_python_root — project detection
# ---------------------------------------------------------------------------


class TestFindPythonRoot:
    def test_pyproject_present(self, validator: PyQualityValidator, py_ctx: ProjectContext) -> None:
        assert validator._find_python_root(py_ctx) is not None

    def test_no_python_indicators(self, validator: PyQualityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert validator._find_python_root(ctx) is None


# ---------------------------------------------------------------------------
# 3. _check_ruff_lint — per-file (post_tool_use)
# ---------------------------------------------------------------------------


class TestCheckRuffLint:
    def test_clean_file_no_findings(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=0)):
            findings = validator._check_ruff_lint(py_project, str(py_project / "src" / "x.py"))
        assert findings == []

    def test_lint_error_parsed(self, validator: PyQualityValidator, py_project: Path) -> None:
        # Format: <file>:<line>:<col>: <code> <message>
        stdout = "src/x.py:3:5: E501 Line too long\nsrc/x.py:7:1: F401 Unused import\n"
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout=stdout)):
            findings = validator._check_ruff_lint(py_project, str(py_project / "src" / "x.py"))

        assert len(findings) == 2
        rules = {f.rule for f in findings}
        assert rules == {"V19-RUFF-E501", "V19-RUFF-F401"}
        # Severity should be 'error' for ruff lint matches
        assert all(f.severity == "error" for f in findings)
        # Line number must be parsed
        assert findings[0].line == 3

    def test_ruff_not_installed(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_ruff_lint(py_project, str(py_project / "src" / "x.py"))
        assert findings == []

    def test_ruff_timeout(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=15)):
            findings = validator._check_ruff_lint(py_project, str(py_project / "src" / "x.py"))
        assert findings == []


# ---------------------------------------------------------------------------
# 4. _check_ruff_format — per-file (post_tool_use)
# ---------------------------------------------------------------------------


class TestCheckRuffFormat:
    def test_formatted_no_finding(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=0)):
            findings = validator._check_ruff_format(py_project, str(py_project / "src" / "x.py"))
        assert findings == []

    def test_unformatted_warning(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=1)):
            findings = validator._check_ruff_format(py_project, str(py_project / "src" / "x.py"))
        assert len(findings) == 1
        assert findings[0].rule == "V19-RUFF-FORMAT"
        assert findings[0].severity == "warning"

    def test_format_tool_missing(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_ruff_format(py_project, str(py_project / "src" / "x.py"))
        assert findings == []


# ---------------------------------------------------------------------------
# 5. _check_ruff_all — full project (stop mode)
# ---------------------------------------------------------------------------


class TestCheckRuffAll:
    def test_under_truncation_threshold(self, validator: PyQualityValidator, py_project: Path) -> None:
        # 5 issues, all should be reported individually (no summary).
        stdout = "\n".join(f"src/file{i}.py:{i + 1}:1: F401 unused import {i}" for i in range(5))
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout=stdout)):
            findings = validator._check_ruff_all(py_project)
        assert len(findings) == 5
        assert all(f.severity == "warning" for f in findings)
        assert all("V19-RUFF-F401" == f.rule for f in findings)

    def test_truncates_above_20(self, validator: PyQualityValidator, py_project: Path) -> None:
        # 25 issues — should yield 20 individual + 1 summary finding.
        stdout = "\n".join(f"src/file{i}.py:{i + 1}:1: E501 line too long {i}" for i in range(25))
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout=stdout)):
            findings = validator._check_ruff_all(py_project)

        rule_counts: dict[str, int] = {}
        for f in findings:
            rule_counts[f.rule] = rule_counts.get(f.rule, 0) + 1

        assert rule_counts.get("V19-RUFF-E501", 0) == 20
        assert rule_counts.get("V19-RUFF-SUMMARY", 0) == 1

        summary = next(f for f in findings if f.rule == "V19-RUFF-SUMMARY")
        assert "25" in summary.message
        assert "5 not shown" in summary.message

    def test_clean_project_no_findings(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=0)):
            findings = validator._check_ruff_all(py_project)
        assert findings == []


# ---------------------------------------------------------------------------
# 6. _check_pytest — full project (stop mode)
# ---------------------------------------------------------------------------


class TestCheckPytest:
    def test_all_passing(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=0, stdout="5 passed\n")):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_failures_reported(self, validator: PyQualityValidator, py_project: Path) -> None:
        stdout = (
            "FAILED tests/test_a.py::test_one - AssertionError\n"
            "FAILED tests/test_b.py::test_two - ValueError\n"
            "2 failed\n"
        )
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout=stdout)):
            findings = validator._check_pytest(py_project)

        assert len(findings) == 1
        assert findings[0].rule == "V19-TEST-FAIL"
        assert findings[0].severity == "error"
        assert "2" in findings[0].message
        assert "tests/test_a.py::test_one" in findings[0].message

    def test_warnings_only_no_failure(self, validator: PyQualityValidator, py_project: Path) -> None:
        # Non-zero exit but tests passed — sometimes pytest plugins / deprecations
        # cause that. The validator should not flag a failure.
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout="5 passed\n")):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_pytest_missing(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_pytest_timeout(self, validator: PyQualityValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=180)):
            findings = validator._check_pytest(py_project)
        assert findings == []


# ---------------------------------------------------------------------------
# 7. validate — mode dispatch
# ---------------------------------------------------------------------------


class TestValidateDispatch:
    def test_post_tool_use_only_runs_per_file(
        self, validator: PyQualityValidator, py_ctx: ProjectContext, py_project: Path
    ) -> None:
        # post_tool_use should call _check_ruff_lint and _check_ruff_format,
        # but NOT _check_ruff_all or _check_pytest.
        with (
            patch.object(validator, "_check_ruff_lint", return_value=[]) as lint,
            patch.object(validator, "_check_ruff_format", return_value=[]) as fmt,
            patch.object(validator, "_check_ruff_all", return_value=[]) as ruff_all,
            patch.object(validator, "_check_pytest", return_value=[]) as test_run,
        ):
            validator.validate(
                py_ctx,
                file_path=str(py_project / "src" / "x.py"),
                mode="post_tool_use",
            )

        assert lint.called
        assert fmt.called
        assert not ruff_all.called
        assert not test_run.called

    def test_stop_runs_full_suite(self, validator: PyQualityValidator, py_ctx: ProjectContext) -> None:
        with (
            patch.object(validator, "_check_ruff_all", return_value=[]) as ruff_all,
            patch.object(validator, "_check_pytest", return_value=[]) as test_run,
        ):
            validator.validate(py_ctx, file_path=None, mode="stop")

        assert ruff_all.called
        assert test_run.called

    def test_no_python_project_returns_empty(self, validator: PyQualityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path="x.py", mode="post_tool_use")
        assert result.findings == []
