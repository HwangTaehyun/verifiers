"""Tests for hooks/validators/ts_quality.py — V07 TypeScript Quality Validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.base import ValidationResult
from hooks.validators.ts_quality import TsQualityValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> TsQualityValidator:
    return TsQualityValidator()


def _write_ts_file(tmp_path: Path, filename: str, content: str) -> str:
    """Helper to create a .ts/.tsx file and return its absolute path."""
    file = tmp_path / filename
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content)
    return str(file)


# ---------------------------------------------------------------------------
# 1. should_run — TypeScript file pattern matching
# ---------------------------------------------------------------------------


class TestShouldRun:
    """Verify should_run accepts TypeScript-related file patterns."""

    def test_ts_file_matches(self, validator: TsQualityValidator) -> None:
        # fnmatch("**/*.ts") requires at least one directory component
        assert validator.should_run("src/app.ts") is True

    def test_tsx_file_matches(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("src/Component.tsx") is True

    def test_bare_ts_file_no_match(self, validator: TsQualityValidator) -> None:
        # fnmatch with **/*.ts does NOT match bare "app.ts"
        assert validator.should_run("app.ts") is False

    def test_package_json_matches(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("web/package.json") is True

    def test_tsconfig_matches(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("web/tsconfig.json") is True

    def test_python_file_no_match(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("src/app.py") is False

    def test_go_file_no_match(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("server/main.go") is False

    def test_js_file_no_match(self, validator: TsQualityValidator) -> None:
        assert validator.should_run("src/script.js") is False


# ---------------------------------------------------------------------------
# 2. _check_any_type — detect `: any`, `as any`, `<any>`
# ---------------------------------------------------------------------------


class TestCheckAnyType:
    """Test _check_any_type detects explicit 'any' type usage (V07-NO-ANY)."""

    def test_colon_any_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "app.ts", "const data: any = fetchData();\n")
        findings = validator._check_any_type(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-NO-ANY"
        assert findings[0].severity == "error"
        assert findings[0].line == 1

    def test_as_any_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "cast.ts", "const x = value as any;\n")
        findings = validator._check_any_type(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-NO-ANY"

    def test_angle_bracket_any_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "generic.ts", "const x = <any>value;\n")
        findings = validator._check_any_type(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-NO-ANY"

    def test_multiple_any_on_different_lines(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = "const a: any = 1;\nconst b = x as any;\nconst c: string = 'ok';\nconst d = <any>y;\n"
        fp = _write_ts_file(tmp_path, "multi.ts", content)
        findings = validator._check_any_type(fp)
        assert len(findings) == 3
        assert {f.line for f in findings} == {1, 2, 4}

    def test_no_any_clean_file(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "clean.ts", "const x: string = 'hello';\n")
        findings = validator._check_any_type(fp)
        assert findings == []

    def test_comment_line_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = "// const data: any = old;\n* @param data: any\n/* const val as any */\n"
        fp = _write_ts_file(tmp_path, "comments.ts", content)
        findings = validator._check_any_type(fp)
        assert findings == []

    def test_inline_code_not_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        """Code with any that doesn't start with a comment prefix is still caught."""
        fp = _write_ts_file(tmp_path, "inline.ts", "const val: any = x; // TODO: fix type\n")
        findings = validator._check_any_type(fp)
        assert len(findings) == 1

    def test_any_in_interface_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = "interface Props {\n  data: any;\n}\n"
        fp = _write_ts_file(tmp_path, "iface.ts", content)
        findings = validator._check_any_type(fp)
        assert len(findings) == 1
        assert findings[0].line == 2

    def test_file_not_found_returns_empty(self, validator: TsQualityValidator) -> None:
        findings = validator._check_any_type("/nonexistent/path/file.ts")
        assert findings == []


# ---------------------------------------------------------------------------
# 3. _check_hardcoded_colors — detect hardcoded color values
# ---------------------------------------------------------------------------


class TestCheckHardcodedColors:
    """Test _check_hardcoded_colors detects hardcoded colors (V07-HARDCODED-COLOR)."""

    def test_hex_color_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "style.ts", "const style = { color: '#ff0000' };\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-HARDCODED-COLOR"
        assert findings[0].severity == "warning"

    def test_background_color_hex(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "bg.ts", "backgroundColor: '#333333'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1

    def test_rgb_color_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "rgb.ts", "color: 'rgb(255, 0, 0)'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1

    def test_rgba_color_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "rgba.ts", "backgroundColor: 'rgba(0, 0, 0, 0.5)'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1

    def test_hsl_color_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "hsl.ts", "fill: 'hsl(120, 100%, 50%)'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1

    def test_theme_palette_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "themed.ts", "color: theme.palette.primary.main\n")
        findings = validator._check_hardcoded_colors(fp)
        assert findings == []

    def test_comment_line_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "commented.ts", "// color: '#ff0000'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert findings == []

    def test_multiple_colors(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = "color: '#aaa'\nborderColor: '#bbb'\nstroke: '#ccc'\n"
        fp = _write_ts_file(tmp_path, "multi.ts", content)
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 3

    def test_file_not_found_returns_empty(self, validator: TsQualityValidator) -> None:
        findings = validator._check_hardcoded_colors("/nonexistent/file.ts")
        assert findings == []

    def test_shorthand_hex_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "short.ts", "color: '#fff'\n")
        findings = validator._check_hardcoded_colors(fp)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# 4. _check_console_log — detect console.log/debug/info
# ---------------------------------------------------------------------------


class TestCheckConsoleLog:
    """Test _check_console_log detects console.log in production code (V07-NO-CONSOLE)."""

    def test_console_log_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "app.ts", "console.log('debug info');\n")
        findings = validator._check_console_log(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-NO-CONSOLE"
        assert findings[0].severity == "warning"

    def test_console_debug_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "debug.ts", "console.debug(data);\n")
        findings = validator._check_console_log(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-NO-CONSOLE"

    def test_console_info_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "info.ts", "console.info('startup');\n")
        findings = validator._check_console_log(fp)
        assert len(findings) == 1

    def test_console_warn_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "warn.ts", "console.warn('problem');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_console_error_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "err.ts", "console.error('crash');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_comment_line_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "commented.ts", "// console.log('old debug');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_multiple_console_calls(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = "console.log('a');\ndoWork();\nconsole.debug('b');\nconsole.info('c');\n"
        fp = _write_ts_file(tmp_path, "multi.ts", content)
        findings = validator._check_console_log(fp)
        assert len(findings) == 3
        assert {f.line for f in findings} == {1, 3, 4}

    # -- Test file exclusions --

    def test_test_file_skipped_dot_test_ts(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "app.test.ts", "console.log('test output');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_test_file_skipped_dot_test_tsx(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "Component.test.tsx", "console.log('test');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_test_file_skipped_dunder_tests_dir(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "__tests__/helper.ts", "console.log('test helper');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_spec_file_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "util.spec.ts", "console.log('spec');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_stories_file_skipped(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "Button.stories.tsx", "console.log('story');\n")
        findings = validator._check_console_log(fp)
        assert findings == []

    def test_file_not_found_returns_empty(self, validator: TsQualityValidator) -> None:
        findings = validator._check_console_log("/nonexistent/file.ts")
        assert findings == []


# ---------------------------------------------------------------------------
# 5. _check_deprecated_mui — detect MUI v4 deprecated patterns
# ---------------------------------------------------------------------------


class TestCheckDeprecatedMui:
    """Test _check_deprecated_mui detects MUI v4 patterns (V07-DEPRECATED-MUI)."""

    def test_makeStyles_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "styles.ts", "const useStyles = makeStyles((theme) => ({}));\n")
        findings = validator._check_deprecated_mui(fp)
        assert len(findings) == 1
        assert findings[0].rule == "V07-DEPRECATED-MUI"
        assert findings[0].severity == "error"
        assert "makeStyles" in findings[0].message

    def test_withStyles_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "hoc.ts", "export default withStyles(styles)(MyComponent);\n")
        findings = validator._check_deprecated_mui(fp)
        assert len(findings) == 1
        assert "withStyles" in findings[0].message

    def test_material_ui_import_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "import.ts", "import Button from '@material-ui/core/Button';\n")
        findings = validator._check_deprecated_mui(fp)
        assert len(findings) == 1
        assert "@material-ui/" in findings[0].message

    def test_material_ui_lab_import_detected(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "lab.ts", "import { Alert } from '@material-ui/lab';\n")
        findings = validator._check_deprecated_mui(fp)
        assert len(findings) == 1

    def test_mui_v5_import_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "v5.ts", "import Button from '@mui/material/Button';\n")
        findings = validator._check_deprecated_mui(fp)
        assert findings == []

    def test_styled_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "styled.ts", "const StyledButton = styled(Button)({});\n")
        findings = validator._check_deprecated_mui(fp)
        assert findings == []

    def test_sx_prop_not_flagged(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        fp = _write_ts_file(tmp_path, "sx.tsx", "<Box sx={{ color: 'primary.main' }} />\n")
        findings = validator._check_deprecated_mui(fp)
        assert findings == []

    def test_multiple_deprecated_patterns(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        content = (
            "import { makeStyles } from '@material-ui/core/styles';\n"
            "import { withStyles } from '@material-ui/core/styles';\n"
            "const useStyles = makeStyles({});\n"
        )
        fp = _write_ts_file(tmp_path, "multi.ts", content)
        findings = validator._check_deprecated_mui(fp)
        # Line 1: makeStyles + @material-ui/ => 2 findings
        # Line 2: withStyles + @material-ui/ => 2 findings
        # Line 3: makeStyles => 1 finding
        assert len(findings) == 5
        assert all(f.rule == "V07-DEPRECATED-MUI" for f in findings)

    def test_file_not_found_returns_empty(self, validator: TsQualityValidator) -> None:
        findings = validator._check_deprecated_mui("/nonexistent/file.ts")
        assert findings == []


# ---------------------------------------------------------------------------
# 6. validate — web_dir does not exist (returns empty findings)
# ---------------------------------------------------------------------------


class TestValidateNoWebDir:
    """When web_dir is None or missing, validate returns empty findings."""

    def test_web_dir_is_none(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        # No web/ directory created
        ctx = ProjectContext(tmp_path)
        assert ctx.web_dir is None

        result = validator.run(ctx, file_path="app.ts", mode="post_tool_use")
        assert isinstance(result, ValidationResult)
        assert result.findings == []

    def test_web_dir_does_not_exist(self, validator: TsQualityValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        ctx.web_dir = tmp_path / "web_nonexistent"

        result = validator.run(ctx, file_path="app.ts", mode="post_tool_use")
        assert result.findings == []


# ---------------------------------------------------------------------------
# 7. validate — integration with real file content
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    """Test the full validate method with real files (no subprocess mocking needed
    for the fast checks that are pure file-based)."""

    def test_clean_ts_file_no_findings(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """A well-written TS file should produce no findings from fast checks."""
        fp = _write_ts_file(
            tmp_project,
            "web/src/clean.ts",
            "const greeting: string = 'hello';\nexport default greeting;\n",
        )
        from unittest.mock import patch

        # Patch subprocess to prevent ESLint from running
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = validator.run(project_ctx, file_path=fp, mode="post_tool_use")
        assert result.findings == []

    def test_file_with_any_and_console(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """A file with both any and console.log should produce findings for both."""
        content = "const data: any = fetch('/api');\nconsole.log(data);\n"
        fp = _write_ts_file(tmp_project, "web/src/bad.ts", content)
        from unittest.mock import patch

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = validator.run(project_ctx, file_path=fp, mode="post_tool_use")

        rules = {f.rule for f in result.findings}
        assert "V07-NO-ANY" in rules
        assert "V07-NO-CONSOLE" in rules

    def test_non_ts_file_skips_fast_checks(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """package.json should not trigger any file-based checks."""
        fp = _write_ts_file(tmp_project, "web/package.json", '{"name": "app"}\n')
        result = validator.run(project_ctx, file_path=fp, mode="post_tool_use")
        assert result.findings == []
