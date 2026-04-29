"""Tests for V12: CommitDisciplineValidator — commit hygiene verification.

Covers:
  - _is_test_file: test file detection
  - _is_source_file: source file detection
  - _check_mixed_changes: structural vs behavioral change detection
  - _check_test_coverage: feature changes without test changes
  - V12-LARGE-DIFF: many files modified
  - V12-UNSTAGED-CHANGES: uncommitted changes
  - validate: stop mode only (skip in post_tool_use)
  - main(): standalone execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.commit_discipline import (
    CONVENTIONAL_COMMIT_PATTERN,
    CommitDisciplineValidator,
    _is_source_file,
    _is_test_file,
)
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> CommitDisciplineValidator:
    return CommitDisciplineValidator()


def _make_git_project(tmp_path: Path) -> Path:
    """Create a minimal git project."""
    (tmp_path / ".git").mkdir()
    return tmp_path


# ============================================================================
# 1. File classification
# ============================================================================


class TestIsTestFile:
    def test_go_test(self) -> None:
        assert _is_test_file("handler_test.go") is True

    def test_python_test_prefix(self) -> None:
        assert _is_test_file("test_handler.py") is True

    def test_python_test_suffix(self) -> None:
        assert _is_test_file("handler_test.py") is True

    def test_ts_test(self) -> None:
        assert _is_test_file("handler.test.ts") is True

    def test_spec_file(self) -> None:
        assert _is_test_file("handler.spec.tsx") is True

    def test_tests_dir(self) -> None:
        assert _is_test_file("src/__tests__/handler.ts") is True

    def test_non_test(self) -> None:
        assert _is_test_file("handler.go") is False

    def test_non_test_python(self) -> None:
        assert _is_test_file("handler.py") is False


class TestIsSourceFile:
    def test_go_source(self) -> None:
        assert _is_source_file("internal/handler.go") is True

    def test_python_source(self) -> None:
        assert _is_source_file("app/handler.py") is True

    def test_ts_source(self) -> None:
        assert _is_source_file("src/handler.ts") is True

    def test_test_file_excluded(self) -> None:
        assert _is_source_file("handler_test.go") is False

    def test_config_file_excluded(self) -> None:
        assert _is_source_file("package.json") is False

    def test_yaml_excluded(self) -> None:
        assert _is_source_file("config.yaml") is False

    def test_markdown_excluded(self) -> None:
        assert _is_source_file("README.md") is False


# ============================================================================
# 2. Validate — stop mode only
# ============================================================================


class TestValidateStopModeOnly:
    def test_skip_in_post_tool_use_mode(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, mode="post_tool_use")
        assert not result.findings

    def test_runs_in_stop_mode(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)
        # With a real git repo, no changes → no findings
        # (git status returns empty for a fresh .git dir)
        result = validator.run(ctx, mode="stop")
        # No real git repo, so _run_git returns empty → no findings
        assert len(result.findings) == 0


# ============================================================================
# 3. V12-UNSTAGED-CHANGES
# ============================================================================


class TestUnstagedChanges:
    def test_detects_modified_files(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = " M handler.go\n?? new_file.py"
            result = validator.run(ctx, mode="stop")
            assert any(f.rule == "V12-UNSTAGED-CHANGES" for f in result.findings)

    def test_no_changes_no_finding(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = ""
            result = validator.run(ctx, mode="stop")
            assert not result.findings


# ============================================================================
# 4. V12-LARGE-DIFF
# ============================================================================


class TestLargeDiff:
    def test_many_files_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        # Create status with 16 modified files
        status_lines = "\n".join(f" M file{i}.go" for i in range(16))
        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = status_lines
            result = validator.run(ctx, mode="stop")
            assert any(f.rule == "V12-LARGE-DIFF" for f in result.findings)

    def test_few_files_no_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        status_lines = "\n".join(f" M file{i}.go" for i in range(5))
        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = status_lines
            result = validator.run(ctx, mode="stop")
            assert not any(f.rule == "V12-LARGE-DIFF" for f in result.findings)


# ============================================================================
# 5. V12-NO-TEST-IN-FEATURE
# ============================================================================


class TestNoTestInFeature:
    def test_source_without_test_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = " M handler.go\n M service.go"
            result = validator.run(ctx, mode="stop")
            assert any(f.rule == "V12-NO-TEST-IN-FEATURE" for f in result.findings)

    def test_source_with_test_no_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = " M handler.go\n M handler_test.go"
            result = validator.run(ctx, mode="stop")
            assert not any(f.rule == "V12-NO-TEST-IN-FEATURE" for f in result.findings)

    def test_config_only_no_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        with mock.patch("hooks.validators.commit_discipline._run_git") as mock_git:
            mock_git.return_value = " M config.yaml\n M package.json"
            result = validator.run(ctx, mode="stop")
            assert not any(f.rule == "V12-NO-TEST-IN-FEATURE" for f in result.findings)


# ============================================================================
# 6. V12-MIXED-CHANGE
# ============================================================================


class TestMixedChange:
    def test_rename_and_modify_warning(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        def mock_git_side_effect(args, _cwd):
            if args[0] == "status":
                return " M handler.go\nR  old.go -> new.go"
            elif args[0] == "diff" and "--name-status" in args:
                return "R100\told.go\tnew.go\nM\thandler.go"
            return ""

        with mock.patch(
            "hooks.validators.commit_discipline._run_git",
            side_effect=mock_git_side_effect,
        ):
            result = validator.run(ctx, mode="stop")
            assert any(f.rule == "V12-MIXED-CHANGE" for f in result.findings)


# ============================================================================
# 7. V12-COMMIT-MSG-FORMAT
# ============================================================================


class TestCommitMsgFormat:
    def test_conventional_commit_no_finding(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        def mock_git_side_effect(args, _cwd):
            if args[0] == "status":
                return " M handler.go"
            elif args[0] == "log":
                return "feat: add user authentication"
            return ""

        with mock.patch(
            "hooks.validators.commit_discipline._run_git",
            side_effect=mock_git_side_effect,
        ):
            result = validator.run(ctx, mode="stop")
            assert not any(f.rule == "V12-COMMIT-MSG-FORMAT" for f in result.findings)

    def test_non_conventional_commit_info(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        def mock_git_side_effect(args, _cwd):
            if args[0] == "status":
                return " M handler.go"
            elif args[0] == "log":
                return "updated the handler to fix stuff"
            return ""

        with mock.patch(
            "hooks.validators.commit_discipline._run_git",
            side_effect=mock_git_side_effect,
        ):
            result = validator.run(ctx, mode="stop")
            assert any(f.rule == "V12-COMMIT-MSG-FORMAT" for f in result.findings)
            finding = next(f for f in result.findings if f.rule == "V12-COMMIT-MSG-FORMAT")
            assert finding.severity == "info"

    def test_merge_commit_skipped(self, validator: CommitDisciplineValidator, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        ctx = ProjectContext(tmp_path)

        def mock_git_side_effect(args, _cwd):
            if args[0] == "status":
                return " M handler.go"
            elif args[0] == "log":
                return "Merge branch 'feature/auth'"
            return ""

        with mock.patch(
            "hooks.validators.commit_discipline._run_git",
            side_effect=mock_git_side_effect,
        ):
            result = validator.run(ctx, mode="stop")
            assert not any(f.rule == "V12-COMMIT-MSG-FORMAT" for f in result.findings)

    def test_conventional_pattern_variants(self) -> None:
        """Test various valid Conventional Commit formats."""
        valid_msgs = [
            "feat: add new feature",
            "fix: resolve crash on startup",
            "refactor: extract helper function",
            "docs: update API documentation",
            "test: add unit tests for auth",
            "chore: update dependencies",
            "style: fix formatting",
            "perf: optimize query performance",
            "ci: add GitHub Actions workflow",
            "build: update Dockerfile",
            "revert: revert merge of feature branch",
            "feat(auth): add OAuth support",
            "fix(api)!: breaking change in response format",
        ]
        for msg in valid_msgs:
            assert CONVENTIONAL_COMMIT_PATTERN.match(msg), f"Should match: {msg}"

    def test_conventional_pattern_invalid(self) -> None:
        """Test invalid Conventional Commit formats."""
        invalid_msgs = [
            "updated handler",
            "fix the bug",
            "Added new feature",
            "WIP: work in progress",
            "feat - add feature",
            "FEAT: uppercase not valid",
        ]
        for msg in invalid_msgs:
            assert not CONVENTIONAL_COMMIT_PATTERN.match(msg), f"Should NOT match: {msg}"


# ============================================================================
# 8. Standalone main()
# ============================================================================


class TestMain:
    def test_main_stop_mode(self, tmp_path: Path) -> None:
        _make_git_project(tmp_path)
        input_data = {"cwd": str(tmp_path)}

        stdout = _run_main(input_data)
        output = json.loads(stdout)
        # Should return approve (no real git changes)
        assert output.get("decision") == "approve"

    def test_main_empty_input(self) -> None:
        stdout = _run_main(None)
        output = json.loads(stdout)
        assert output.get("decision") == "approve"


# ── Module-level helpers ─────────────────────────────────────────────────────


def _run_main(input_data: dict | None) -> str:
    from hooks.validators.commit_discipline import main

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
