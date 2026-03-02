"""Tests for hooks/validators/ts_test_runner.py — V10 TypeScript Test Runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.base import ValidationResult
from hooks.validators.ts_test_runner import TsTestRunnerValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> TsTestRunnerValidator:
    return TsTestRunnerValidator()


@pytest.fixture(autouse=True)
def clean_tracker(tmp_path: Path) -> None:
    """Reset the failure tracker for each test."""
    import hooks.validators.ts_test_runner as mod

    mod.FAILURE_TRACKER = tmp_path / "test-failure-tracker.json"


# ---------------------------------------------------------------------------
# 1. should_run — TypeScript file pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    """Verify should_run accepts TypeScript file patterns."""

    def test_ts_file_matches(self, validator: TsTestRunnerValidator) -> None:
        assert validator.should_run("web/src/utils.ts") is True

    def test_tsx_file_matches(self, validator: TsTestRunnerValidator) -> None:
        assert validator.should_run("web/src/Button.tsx") is True

    def test_go_file_no_match(self, validator: TsTestRunnerValidator) -> None:
        assert validator.should_run("server/main.go") is False

    def test_python_file_no_match(self, validator: TsTestRunnerValidator) -> None:
        assert validator.should_run("app.py") is False


# ---------------------------------------------------------------------------
# 2. validate — web_dir does not exist
# ---------------------------------------------------------------------------


class TestValidateNoWebDir:
    """When web_dir is None or missing, validate returns empty findings."""

    def test_web_dir_is_none(self, validator: TsTestRunnerValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = _make_ctx(tmp_path)
        ctx.web_dir = None  # type: ignore[assignment]
        result = validator.validate(ctx, file_path="src/App.tsx", mode="post_tool_use")
        assert isinstance(result, ValidationResult)
        assert result.findings == []


# ---------------------------------------------------------------------------
# 3. _is_test_file — test file detection
# ---------------------------------------------------------------------------


class TestIsTestFile:
    """Test TypeScript test file detection."""

    def test_test_ts_file(self, validator: TsTestRunnerValidator) -> None:
        assert validator._is_test_file("Button.test.tsx") is True

    def test_spec_ts_file(self, validator: TsTestRunnerValidator) -> None:
        assert validator._is_test_file("utils.spec.ts") is True

    def test_tests_dir_file(self, validator: TsTestRunnerValidator) -> None:
        assert validator._is_test_file("__tests__/Button.tsx") is True

    def test_source_file_not_test(self, validator: TsTestRunnerValidator) -> None:
        assert validator._is_test_file("Button.tsx") is False

    def test_utils_not_test(self, validator: TsTestRunnerValidator) -> None:
        assert validator._is_test_file("src/utils.ts") is False


# ---------------------------------------------------------------------------
# 4. _resolve_test_file — test file resolution
# ---------------------------------------------------------------------------


class TestResolveTestFile:
    """Test source → test file resolution."""

    def test_same_dir_test_file(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        src_dir = tmp_project / "web" / "src"
        (src_dir / "Button.tsx").write_text("export default function Button() {}")
        (src_dir / "Button.test.tsx").write_text("test('works', () => {})")

        result = validator._resolve_test_file(
            project_ctx, str(src_dir / "Button.tsx")
        )
        assert result is not None
        assert "Button.test.tsx" in result

    def test_same_dir_spec_file(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        src_dir = tmp_project / "web" / "src"
        (src_dir / "utils.ts").write_text("export function foo() {}")
        (src_dir / "utils.spec.ts").write_text("test('works', () => {})")

        result = validator._resolve_test_file(
            project_ctx, str(src_dir / "utils.ts")
        )
        assert result is not None
        assert "utils.spec.ts" in result

    def test_tests_subdir(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        src_dir = tmp_project / "web" / "src"
        tests_dir = src_dir / "__tests__"
        tests_dir.mkdir(parents=True)
        (src_dir / "Modal.tsx").write_text("export default function Modal() {}")
        (tests_dir / "Modal.test.tsx").write_text("test('works', () => {})")

        result = validator._resolve_test_file(
            project_ctx, str(src_dir / "Modal.tsx")
        )
        assert result is not None
        assert "Modal.test.tsx" in result

    def test_no_test_found(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        src_dir = tmp_project / "web" / "src"
        (src_dir / "Orphan.tsx").write_text("export default function Orphan() {}")

        result = validator._resolve_test_file(
            project_ctx, str(src_dir / "Orphan.tsx")
        )
        assert result is None


# ---------------------------------------------------------------------------
# 5. _detect_test_runner — framework detection
# ---------------------------------------------------------------------------


class TestDetectTestRunner:
    """Test test runner detection."""

    def test_vitest_config(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        (tmp_project / "web" / "vitest.config.ts").write_text("export default {}")
        cmd, name = validator._detect_test_runner(project_ctx)
        assert name == "vitest"
        assert "vitest" in cmd

    def test_jest_config(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        (tmp_project / "web" / "jest.config.js").write_text("module.exports = {}")
        cmd, name = validator._detect_test_runner(project_ctx)
        assert name == "jest"
        assert "jest" in cmd

    def test_jest_in_package_json(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        (tmp_project / "web" / "package.json").write_text(
            json.dumps({"devDependencies": {"jest": "^29.0.0"}})
        )
        cmd, name = validator._detect_test_runner(project_ctx)
        assert name == "jest"

    def test_vitest_in_package_json(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        (tmp_project / "web" / "package.json").write_text(
            json.dumps({"devDependencies": {"vitest": "^1.0.0"}})
        )
        cmd, name = validator._detect_test_runner(project_ctx)
        assert name == "vitest"

    def test_default_bun_test(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        # No config files → bun test
        cmd, name = validator._detect_test_runner(project_ctx)
        assert name == "bun"
        assert cmd == ["bun", "test"]


# ---------------------------------------------------------------------------
# 6. _run_test_file — mocked subprocess
# ---------------------------------------------------------------------------


class TestRunTestFile:
    """Test test execution with mocked subprocess."""

    def test_tests_pass_no_findings(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["bun", "test"], returncode=0, stdout="✓ works (2ms)\n", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_test_file(project_ctx, "Button.test.tsx")
        assert findings == []

    def test_test_failure(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["bun", "test"],
            returncode=1,
            stdout="✕ renders correctly (5ms)\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._run_test_file(project_ctx, "Button.test.tsx")

        test_fail_findings = [f for f in findings if f.rule == "V10-TEST-FAIL"]
        assert len(test_fail_findings) == 1
        assert test_fail_findings[0].severity == "error"

    def test_not_installed(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._run_test_file(project_ctx, "Button.test.tsx")
        assert findings == []

    def test_timeout(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bun", timeout=60)):
            findings = validator._run_test_file(project_ctx, "Button.test.tsx")
        assert findings == []


# ---------------------------------------------------------------------------
# 7. _check_test_exists — warning for missing tests
# ---------------------------------------------------------------------------


class TestCheckTestExists:
    """Test warning generation for missing test files."""

    def test_source_file_warns(self, validator: TsTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/Button.tsx")
        assert len(findings) == 1
        assert findings[0].rule == "V10-NO-TEST"
        assert findings[0].severity == "warning"

    def test_index_ts_skipped(self, validator: TsTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/index.ts")
        assert findings == []

    def test_types_ts_skipped(self, validator: TsTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/types.ts")
        assert findings == []

    def test_declaration_file_skipped(self, validator: TsTestRunnerValidator) -> None:
        findings = validator._check_test_exists("src/globals.d.ts")
        assert findings == []


# ---------------------------------------------------------------------------
# 8. _track_failure — repeated failure detection
# ---------------------------------------------------------------------------


class TestTrackFailure:
    """Test consecutive failure tracking."""

    def test_first_failure_returns_1(self, validator: TsTestRunnerValidator) -> None:
        count = validator._track_failure("test renders", passed=False)
        assert count == 1

    def test_consecutive_failures_increment(self, validator: TsTestRunnerValidator) -> None:
        validator._track_failure("test renders", passed=False)
        validator._track_failure("test renders", passed=False)
        count = validator._track_failure("test renders", passed=False)
        assert count == 3

    def test_pass_resets_counter(self, validator: TsTestRunnerValidator) -> None:
        validator._track_failure("test renders", passed=False)
        validator._track_failure("test renders", passed=False)
        count = validator._track_failure("test renders", passed=True)
        assert count == 0


# ---------------------------------------------------------------------------
# 9. validate — integration
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Test the full validate method."""

    def test_excluded_dir_skipped(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        result = validator.validate(
            project_ctx,
            file_path=str(tmp_project / "web" / "node_modules" / "lib.ts"),
            mode="post_tool_use",
        )
        assert result.findings == []

    def test_stop_mode_skipped(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
        result = validator.validate(project_ctx, file_path="App.tsx", mode="stop")
        assert result.findings == []

    def test_non_ts_file_skipped(
        self, validator: TsTestRunnerValidator, tmp_project: Path, project_ctx
    ) -> None:
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
