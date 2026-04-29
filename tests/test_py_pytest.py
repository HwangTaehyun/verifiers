"""Tests for hooks/validators/py_pytest.py — V21 Pytest Runner (Phase28).

Covers:
  - V21-TEST-FAIL          (pytest failure parsing, mode-dispatch)
  - smart-mode trigger     (git diff heuristic; py-touched ⇒ run, md-only ⇒ skip)
  - stop.run_pytest config (always | never | smart) routing
  - mode gating            (post_tool_use returns early, stop runs)
  - graceful degradation   (no python project, pytest missing, timeout, git unavailable)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.py_pytest import PyPytestValidator, has_uncommitted_python_changes
from lib.config_loader import StopConfig, VerifiersConfig
from lib.project_context import ProjectContext


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> PyPytestValidator:
    return PyPytestValidator()


@pytest.fixture
def py_project(tmp_path: Path) -> Path:
    """Minimal Python project layout: .git + pyproject.toml."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    return tmp_path


def _ctx_with_run_pytest(project_root: Path, mode: str) -> ProjectContext:
    """ProjectContext with a manually-set stop.run_pytest mode."""
    ctx = ProjectContext(project_root)
    ctx.config = VerifiersConfig()
    ctx.config.stop = StopConfig(run_pytest=mode)
    return ctx


def _make_completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["pytest"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── 1. should_run — file-pattern matching ─────────────────────────────


class TestShouldRun:
    def test_py_file_matches(self, validator: PyPytestValidator) -> None:
        assert validator.should_run("src/handler.py") is True

    def test_pyproject_matches(self, validator: PyPytestValidator) -> None:
        # file_patterns uses '**/pyproject.toml' (fnmatch glob); a path
        # with at least one preceding segment is required to match.
        assert validator.should_run("project/pyproject.toml") is True

    def test_go_no_match(self, validator: PyPytestValidator) -> None:
        assert validator.should_run("main.go") is False


# ── 2. has_uncommitted_python_changes — smart-mode oracle ─────────────


class TestSmartTrigger:
    def test_py_change_returns_true(self, py_project: Path) -> None:
        with patch(
            "subprocess.run",
            return_value=_make_completed(0, stdout="src/foo.py\nREADME.md\n"),
        ):
            assert has_uncommitted_python_changes(py_project) is True

    def test_pyproject_change_returns_true(self, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(0, stdout="pyproject.toml\n")):
            assert has_uncommitted_python_changes(py_project) is True

    def test_md_only_returns_false(self, py_project: Path) -> None:
        with patch(
            "subprocess.run",
            return_value=_make_completed(0, stdout="README.md\ndocs/CONFIGURATION.md\n"),
        ):
            assert has_uncommitted_python_changes(py_project) is False

    def test_empty_diff_returns_false(self, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(0, stdout="")):
            assert has_uncommitted_python_changes(py_project) is False

    def test_git_failure_fails_open(self, py_project: Path) -> None:
        # Not a git repo (or git not on PATH) — must return True so we never
        # silently skip pytest.
        with patch("subprocess.run", return_value=_make_completed(128, stdout="", stderr="not a repo")):
            assert has_uncommitted_python_changes(py_project) is True

    def test_git_missing_fails_open(self, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert has_uncommitted_python_changes(py_project) is True

    def test_git_timeout_fails_open(self, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=2)):
            assert has_uncommitted_python_changes(py_project) is True


# ── 3. validate — mode + config gating ────────────────────────────────


class TestValidateGating:
    def test_post_tool_use_returns_empty(self, validator: PyPytestValidator, py_project: Path) -> None:
        # V21 is Stop-only — Tier 2 calls must return immediately.
        ctx = _ctx_with_run_pytest(py_project, "always")
        with patch.object(validator, "_check_pytest", return_value=[]) as check:
            result = validator.run(ctx, file_path="src/x.py", mode="post_tool_use")
        assert result.findings == []
        assert not check.called

    def test_never_skips_pytest(self, validator: PyPytestValidator, py_project: Path) -> None:
        ctx = _ctx_with_run_pytest(py_project, "never")
        with patch.object(validator, "_check_pytest", return_value=[]) as check:
            result = validator.run(ctx, file_path=None, mode="stop")
        assert result.findings == []
        assert not check.called

    def test_always_runs_pytest(self, validator: PyPytestValidator, py_project: Path) -> None:
        ctx = _ctx_with_run_pytest(py_project, "always")
        with patch.object(validator, "_check_pytest", return_value=[]) as check:
            validator.run(ctx, file_path=None, mode="stop")
        assert check.called

    def test_smart_runs_when_py_touched(self, validator: PyPytestValidator, py_project: Path) -> None:
        ctx = _ctx_with_run_pytest(py_project, "smart")
        with (
            patch(
                "hooks.validators.py_pytest.has_uncommitted_python_changes",
                return_value=True,
            ),
            patch.object(validator, "_check_pytest", return_value=[]) as check,
        ):
            validator.run(ctx, file_path=None, mode="stop")
        assert check.called

    def test_smart_skips_when_no_py_touched(self, validator: PyPytestValidator, py_project: Path) -> None:
        ctx = _ctx_with_run_pytest(py_project, "smart")
        with (
            patch(
                "hooks.validators.py_pytest.has_uncommitted_python_changes",
                return_value=False,
            ),
            patch.object(validator, "_check_pytest", return_value=[]) as check,
        ):
            validator.run(ctx, file_path=None, mode="stop")
        assert not check.called

    def test_no_python_project_returns_empty(self, validator: PyPytestValidator, tmp_path: Path) -> None:
        # No pyproject/setup.py — V21 has nothing to run against.
        (tmp_path / ".git").mkdir()
        ctx = _ctx_with_run_pytest(tmp_path, "always")
        result = validator.run(ctx, file_path=None, mode="stop")
        assert result.findings == []


# ── 4. _check_pytest — failure parsing (regression of V19 coverage) ───


class TestCheckPytest:
    def test_all_passing(self, validator: PyPytestValidator, py_project: Path) -> None:
        with patch("subprocess.run", return_value=_make_completed(returncode=0, stdout="5 passed\n")):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_failures_reported(self, validator: PyPytestValidator, py_project: Path) -> None:
        stdout = (
            "FAILED tests/test_a.py::test_one - AssertionError\n"
            "FAILED tests/test_b.py::test_two - ValueError\n"
            "2 failed\n"
        )
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout=stdout)):
            findings = validator._check_pytest(py_project)

        assert len(findings) == 1
        assert findings[0].rule == "V21-TEST-FAIL"
        assert findings[0].severity == "error"
        assert "2" in findings[0].message
        assert "tests/test_a.py::test_one" in findings[0].message

    def test_warnings_only_no_failure(self, validator: PyPytestValidator, py_project: Path) -> None:
        # Non-zero exit + "X passed" with no "X failed" — pytest plugin/deprecation noise.
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stdout="5 passed\n")):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_pytest_missing(self, validator: PyPytestValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_pytest(py_project)
        assert findings == []

    def test_pytest_timeout(self, validator: PyPytestValidator, py_project: Path) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=180)):
            findings = validator._check_pytest(py_project)
        assert findings == []
