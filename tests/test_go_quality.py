"""Tests for hooks/validators/go_quality.py — V06 Go Quality Validator."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.base import ValidationResult
from hooks.validators.go_quality import GoQualityValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> GoQualityValidator:
    return GoQualityValidator()


# ---------------------------------------------------------------------------
# 1. should_run — Go file pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    """Verify should_run accepts Go-related file patterns."""

    def test_go_file_matches(self, validator: GoQualityValidator) -> None:
        # fnmatch("**/*.go") requires at least one directory component
        assert validator.should_run("server/main.go") is True

    def test_nested_go_file_matches(self, validator: GoQualityValidator) -> None:
        assert validator.should_run("server/internal/handler.go") is True

    def test_bare_go_file_no_match(self, validator: GoQualityValidator) -> None:
        # fnmatch with **/*.go does NOT match bare "main.go" (no directory prefix)
        assert validator.should_run("main.go") is False

    def test_go_mod_matches(self, validator: GoQualityValidator) -> None:
        # fnmatch("**/go.mod") requires at least one directory component
        assert validator.should_run("server/go.mod") is True

    def test_go_sum_matches(self, validator: GoQualityValidator) -> None:
        assert validator.should_run("server/go.sum") is True

    def test_bare_go_mod_no_match(self, validator: GoQualityValidator) -> None:
        # fnmatch with **/go.mod does NOT match bare "go.mod"
        assert validator.should_run("go.mod") is False

    def test_python_file_no_match(self, validator: GoQualityValidator) -> None:
        assert validator.should_run("app.py") is False

    def test_ts_file_no_match(self, validator: GoQualityValidator) -> None:
        assert validator.should_run("component.tsx") is False

    def test_yaml_file_no_match(self, validator: GoQualityValidator) -> None:
        assert validator.should_run("config.yaml") is False


# ---------------------------------------------------------------------------
# 2. validate — server_dir does not exist (returns empty findings)
# ---------------------------------------------------------------------------


class TestValidateNoServerDir:
    """When server_dir is None or missing, validate returns empty findings."""

    def test_server_dir_is_none(self, validator: GoQualityValidator, tmp_path: Path) -> None:
        """ProjectContext with no server/ dir should yield no findings."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        assert ctx.server_dir is None

        result = validator.run(ctx, file_path="main.go", mode="post_tool_use")
        assert isinstance(result, ValidationResult)
        assert result.findings == []

    def test_server_dir_does_not_exist(self, validator: GoQualityValidator, tmp_path: Path) -> None:
        """Even if we manually set server_dir to a non-existent path, no findings."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        ctx.server_dir = tmp_path / "server_nonexistent"

        result = validator.run(ctx, file_path="main.go", mode="post_tool_use")
        assert result.findings == []


# ---------------------------------------------------------------------------
# 3. _check_go_vet — mocked subprocess
# ---------------------------------------------------------------------------


class TestCheckGoVet:
    """Test _check_go_vet with mocked subprocess.run."""

    def test_go_vet_success_no_findings(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["go", "vet", "./..."],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_vet(project_ctx)
        assert findings == []

    def test_go_vet_single_error(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stderr = "cmd/main.go:15:2: unreachable code after return statement\n"
        mock_result = subprocess.CompletedProcess(
            args=["go", "vet", "./..."],
            returncode=2,
            stdout="",
            stderr=stderr,
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_vet(project_ctx)

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V06-GO-VET"
        assert f.severity == "error"
        assert "unreachable code" in f.message
        assert f.line == 15
        assert f.file.endswith("cmd/main.go")

    def test_go_vet_multiple_errors(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stderr = (
            "pkg/handler.go:10:5: composite literal uses unkeyed fields\n"
            "pkg/handler.go:25:3: result of fmt.Sprintf call not used\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=["go", "vet", "./..."],
            returncode=2,
            stdout="",
            stderr=stderr,
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_vet(project_ctx)

        assert len(findings) == 2
        assert findings[0].line == 10
        assert findings[1].line == 25

    def test_go_vet_not_installed(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_go_vet(project_ctx)
        assert findings == []

    def test_go_vet_timeout(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="go", timeout=30)):
            findings = validator._check_go_vet(project_ctx)
        assert findings == []

    def test_go_vet_non_matching_stderr_lines_ignored(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stderr = "# command-line-arguments\nvet: some random text\n"
        mock_result = subprocess.CompletedProcess(
            args=["go", "vet", "./..."],
            returncode=2,
            stdout="",
            stderr=stderr,
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_vet(project_ctx)
        assert findings == []


# ---------------------------------------------------------------------------
# 4. _check_gofmt — mocked subprocess
# ---------------------------------------------------------------------------


class TestCheckGofmt:
    """Test _check_gofmt with mocked subprocess.run."""

    def test_gofmt_already_formatted(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["gofmt", "-l", "main.go"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_gofmt(project_ctx, "main.go")
        assert findings == []

    def test_gofmt_needs_formatting(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["gofmt", "-l", "main.go"],
            returncode=0,
            stdout="main.go\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_gofmt(project_ctx, "main.go")

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V06-GOFMT"
        assert f.severity == "error"
        assert f.file == "main.go"
        assert "gofmt" in f.message.lower() or "gofmt" in f.fix.lower()

    def test_gofmt_not_installed(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_gofmt(project_ctx, "main.go")
        assert findings == []

    def test_gofmt_timeout(self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gofmt", timeout=10)):
            findings = validator._check_gofmt(project_ctx, "main.go")
        assert findings == []


# ---------------------------------------------------------------------------
# 5. _check_go_build — mocked subprocess
# ---------------------------------------------------------------------------


class TestCheckGoBuild:
    """Test _check_go_build with mocked subprocess.run."""

    def test_build_success_no_findings(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["go", "build", "./..."],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_build(project_ctx)
        assert findings == []

    def test_build_failure_single_error(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stderr = "cmd/server.go:42:10: undefined: DoSomething\n"
        mock_result = subprocess.CompletedProcess(
            args=["go", "build", "./..."],
            returncode=2,
            stdout="",
            stderr=stderr,
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_build(project_ctx)

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V06-BUILD-FAIL"
        assert f.severity == "error"
        assert f.line == 42
        assert "undefined: DoSomething" in f.message

    def test_build_failure_multiple_errors(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stderr = 'pkg/db.go:5:2: imported and not used: "fmt"\npkg/db.go:20:8: cannot use x (type int) as type string\n'
        mock_result = subprocess.CompletedProcess(
            args=["go", "build", "./..."],
            returncode=2,
            stdout="",
            stderr=stderr,
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_build(project_ctx)

        assert len(findings) == 2
        assert findings[0].line == 5
        assert findings[1].line == 20
        assert all(f.rule == "V06-BUILD-FAIL" for f in findings)

    def test_build_not_installed(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_go_build(project_ctx)
        assert findings == []


# ---------------------------------------------------------------------------
# 6. _check_golangci_lint — mocked subprocess (stop mode)
# ---------------------------------------------------------------------------


class TestCheckGolangciLint:
    """Test _check_golangci_lint with mocked subprocess.run."""

    def test_lint_clean_no_findings(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["golangci-lint", "run"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_golangci_lint(project_ctx)
        assert findings == []

    def test_lint_with_issues(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        import json

        lint_output = json.dumps(
            {
                "Issues": [
                    {
                        "FromLinter": "errcheck",
                        "Text": "Error return value is not checked",
                        "Pos": {"Filename": "cmd/main.go", "Line": 33},
                    },
                    {
                        "FromLinter": "govet",
                        "Text": "composites: struct literal uses unkeyed fields",
                        "Pos": {"Filename": "pkg/handler.go", "Line": 12},
                    },
                ]
            }
        )
        mock_result = subprocess.CompletedProcess(
            args=["golangci-lint", "run"],
            returncode=1,
            stdout=lint_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_golangci_lint(project_ctx)

        assert len(findings) == 2
        assert findings[0].rule == "V06-LINT-errcheck"
        assert findings[0].severity == "warning"
        assert findings[0].line == 33
        assert findings[1].rule == "V06-LINT-govet"
        assert findings[1].line == 12

    def test_lint_invalid_json_stdout(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["golangci-lint", "run"],
            returncode=1,
            stdout="not-valid-json",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_golangci_lint(project_ctx)
        assert findings == []

    def test_lint_failure_no_stdout(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["golangci-lint", "run"],
            returncode=1,
            stdout="",
            stderr="some error",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_golangci_lint(project_ctx)
        assert findings == []

    def test_lint_not_installed(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_golangci_lint(project_ctx)
        assert findings == []

    def test_lint_null_issues(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """golangci-lint may return {"Issues": null}."""
        import json

        lint_output = json.dumps({"Issues": None})
        mock_result = subprocess.CompletedProcess(
            args=["golangci-lint", "run"],
            returncode=1,
            stdout=lint_output,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_golangci_lint(project_ctx)
        assert findings == []


# ---------------------------------------------------------------------------
# 7. _check_go_test — mocked subprocess (stop mode)
# ---------------------------------------------------------------------------


class TestCheckGoTest:
    """Test _check_go_test with mocked subprocess.run."""

    def test_tests_pass_no_findings(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        mock_result = subprocess.CompletedProcess(
            args=["go", "test", "./..."],
            returncode=0,
            stdout="ok  \tmypackage\t0.005s\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_test(project_ctx)
        assert findings == []

    def test_tests_fail(self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext) -> None:
        stdout = (
            "--- FAIL: TestCreateUser (0.01s)\n    user_test.go:15: expected 200 but got 400\nFAIL\tmypackage\t0.010s\n"
        )
        mock_result = subprocess.CompletedProcess(
            args=["go", "test", "./..."],
            returncode=1,
            stdout=stdout,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_test(project_ctx)

        assert len(findings) == 1
        f = findings[0]
        assert f.rule == "V06-TEST-FAIL"
        assert f.severity == "error"
        assert "TestCreateUser" in f.message

    def test_tests_multiple_failures(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        stdout = "--- FAIL: TestA (0.01s)\n--- FAIL: TestB (0.02s)\nFAIL\n"
        mock_result = subprocess.CompletedProcess(
            args=["go", "test", "./..."],
            returncode=1,
            stdout=stdout,
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result):
            findings = validator._check_go_test(project_ctx)

        assert len(findings) == 1
        assert "TestA" in findings[0].message
        assert "TestB" in findings[0].message

    def test_tests_not_installed(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            findings = validator._check_go_test(project_ctx)
        assert findings == []

    def test_tests_use_makefile_when_available(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """When a Makefile with 'test:' target exists, 'make test' should be used."""
        makefile = project_ctx.server_dir / "Makefile"
        makefile.write_text("test:\n\tgo test ./...\n")

        mock_result = subprocess.CompletedProcess(
            args=["make", "test"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            findings = validator._check_go_test(project_ctx)

        assert findings == []
        # Verify 'make test' was called
        call_args = mock_run.call_args[0][0]
        assert call_args == ["make", "test"]


# ---------------------------------------------------------------------------
# 8. validate — integration with mode="post_tool_use" and "stop"
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Test the full validate method with mocked subprocess calls."""

    def test_post_tool_use_runs_fast_checks_only(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """post_tool_use mode should NOT call golangci-lint or go test."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = validator.run(project_ctx, file_path="main.go", mode="post_tool_use")

        assert isinstance(result, ValidationResult)
        # Should call: go vet, gofmt, go build (3 calls total)
        assert mock_run.call_count == 3
        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert ["go", "vet", "./..."] in called_cmds
        assert ["gofmt", "-l", "main.go"] in called_cmds
        assert ["go", "build", "./..."] in called_cmds

    def test_stop_mode_runs_all_checks(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """stop mode should also call golangci-lint and go test."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = validator.run(project_ctx, file_path="main.go", mode="stop")

        assert isinstance(result, ValidationResult)
        # Phase29+: stop mode dispatches to validate_project only — the
        # file-specific gofmt is skipped because Tier 3 is project-wide.
        # validate_project calls: go vet, go build, golangci-lint, go test (4 calls).
        assert mock_run.call_count == 4

    def test_non_go_file_skips_gofmt(
        self, validator: GoQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """gofmt should not be run if file_path is not a .go file."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            validator.run(project_ctx, file_path="go.mod", mode="post_tool_use")

        # Should call: go vet, go build (no gofmt for go.mod)
        assert mock_run.call_count == 2
        called_cmds = [call[0][0] for call in mock_run.call_args_list]
        assert ["gofmt", "-l", "go.mod"] not in called_cmds
