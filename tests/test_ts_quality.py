"""Tests for hooks/validators/ts_quality.py — V07 TypeScript Quality Validator."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# 8. _check_vite_env_typed — Vite env.d.ts coverage (Phase48)
# ---------------------------------------------------------------------------


class TestViteEnvTyped:
    """Test _check_vite_env_typed enforces typed `import.meta.env.VITE_*`
    coverage in `vite-env.d.ts` / `env.d.ts` (V07-VITE-ENV-TYPED)."""

    def test_no_vite_refs_no_findings(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """A web/src/ with no VITE_* references should yield no findings."""
        _write_ts_file(tmp_project, "web/src/app.ts", "const x: string = 'hello';\n")
        findings = validator._check_vite_env_typed(project_ctx)
        assert findings == []

    def test_vite_ref_with_no_dts_warns(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """VITE_* references with no vite-env.d.ts → warn per unique key."""
        content = "const url = import.meta.env.VITE_API_URL;\nconst key = import.meta.env.VITE_KEY;\n"
        _write_ts_file(tmp_project, "web/src/app.ts", content)
        findings = validator._check_vite_env_typed(project_ctx)
        names = {f.message.split("`")[1].rsplit(".", 1)[1] for f in findings}
        assert names == {"VITE_API_URL", "VITE_KEY"}
        assert all(f.rule == "V07-VITE-ENV-TYPED" for f in findings)
        assert all(f.severity == "warning" for f in findings)

    def test_vite_ref_typed_in_dts_passes(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """When every VITE_* is declared in vite-env.d.ts → no findings."""
        _write_ts_file(
            tmp_project,
            "web/src/vite-env.d.ts",
            "interface ImportMetaEnv {\n  readonly VITE_API_URL: string;\n  readonly VITE_KEY: string;\n}\n",
        )
        _write_ts_file(
            tmp_project,
            "web/src/app.ts",
            "const url = import.meta.env.VITE_API_URL;\nconst key = import.meta.env.VITE_KEY;\n",
        )
        findings = validator._check_vite_env_typed(project_ctx)
        assert findings == []

    def test_partial_dts_coverage_flags_missing(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Only the missing key is flagged; declared keys pass."""
        _write_ts_file(
            tmp_project,
            "web/src/vite-env.d.ts",
            "interface ImportMetaEnv {\n  readonly VITE_API_URL: string;\n}\n",
        )
        _write_ts_file(
            tmp_project,
            "web/src/app.ts",
            "const url = import.meta.env.VITE_API_URL;\nconst fresh = import.meta.env.VITE_NEW;\n",
        )
        findings = validator._check_vite_env_typed(project_ctx)
        assert len(findings) == 1
        assert "VITE_NEW" in findings[0].message
        assert "VITE_API_URL" not in findings[0].message

    def test_env_d_ts_alternate_name(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """`env.d.ts` (Vite's secondary convention) is also accepted."""
        _write_ts_file(
            tmp_project,
            "web/src/env.d.ts",
            "interface ImportMetaEnv {\n  readonly VITE_TOKEN: string;\n}\n",
        )
        _write_ts_file(
            tmp_project,
            "web/src/app.ts",
            "const t = import.meta.env.VITE_TOKEN;\n",
        )
        findings = validator._check_vite_env_typed(project_ctx)
        assert findings == []

    def test_dts_self_reference_does_not_loop(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """References inside vite-env.d.ts itself (e.g. example comments)
        must not generate findings — the dts file is excluded from scanning."""
        _write_ts_file(
            tmp_project,
            "web/src/vite-env.d.ts",
            "// Example usage: import.meta.env.VITE_FAKE\n"
            "interface ImportMetaEnv {\n  readonly VITE_FAKE: string;\n}\n",
        )
        findings = validator._check_vite_env_typed(project_ctx)
        assert findings == []


# ---------------------------------------------------------------------------
# 9. Cache flags — ESLint cache dir, lock invalidation, tsc incremental
# ---------------------------------------------------------------------------


class TestCacheFlags:
    """Test tool-native cache flags for eslint and tsc."""

    def test_eslint_cache_dir_created(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """After Tier 2 eslint invocation, the cache dir should exist."""
        cache_dir = tmp_project / ".verifiers" / "cache" / "eslint"
        assert not cache_dir.exists()

        with patch("subprocess.run", side_effect=FileNotFoundError):
            validator._check_eslint_single(project_ctx, str(tmp_project / "web/src/app.ts"))

        assert cache_dir.exists()

    def test_eslint_lock_hash_invalidates_cache(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """Changing bun.lockb causes the eslint cache dir to be wiped.

        Phase 67: ``_invalidate_eslint_cache_if_lock_changed`` now
        returns the cache **file** path (``.eslintcache``) instead of
        the directory. The sentinel goes in the file's parent dir to
        verify the wipe.
        """
        web_dir = tmp_project / "web"
        lock_file = web_dir / "bun.lockb"
        lock_file.write_bytes(b"initial-lock-content")

        # First call — populates cache and stores hash
        cache_file = validator._invalidate_eslint_cache_if_lock_changed(project_ctx)
        cache_dir = cache_file.parent
        # Plant a sentinel file inside cache to verify it gets wiped
        sentinel = cache_dir / "some-cached-file.json"
        sentinel.write_text("{}")
        assert sentinel.exists()

        # Mutate the lockfile
        lock_file.write_bytes(b"updated-lock-content")

        # Second call — hash differs, cache should be wiped
        validator._invalidate_eslint_cache_if_lock_changed(project_ctx)
        assert not sentinel.exists()
        assert cache_dir.exists()  # re-created after wipe

    def test_tsc_incremental_flag_present_when_ts5(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """When TypeScript >= 5 is detected, --incremental and --tsBuildInfoFile are added."""
        ts5_version_output = MagicMock()
        ts5_version_output.stdout = "Version 5.5.3\n"
        ts5_version_output.returncode = 0

        tsc_result = MagicMock()
        tsc_result.returncode = 0
        tsc_result.stdout = ""

        call_args_list: list = []

        def fake_run(cmd, **kwargs):
            call_args_list.append(cmd)
            if "--version" in cmd:
                return ts5_version_output
            return tsc_result

        with patch("subprocess.run", side_effect=fake_run):
            validator._check_tsc(project_ctx)

        tsc_calls = [c for c in call_args_list if "tsc" in c and "--version" not in c]
        assert tsc_calls, "Expected a tsc --noEmit call"
        tsc_cmd = tsc_calls[0]
        assert "--incremental" in tsc_cmd
        assert "--tsBuildInfoFile" in tsc_cmd

    def test_tsc_incremental_skipped_when_ts4(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """When TypeScript < 5 is detected, --incremental is NOT added."""
        ts4_version_output = MagicMock()
        ts4_version_output.stdout = "Version 4.9.5\n"
        ts4_version_output.returncode = 0

        tsc_result = MagicMock()
        tsc_result.returncode = 0
        tsc_result.stdout = ""

        call_args_list: list = []

        def fake_run(cmd, **kwargs):
            call_args_list.append(cmd)
            if "--version" in cmd:
                return ts4_version_output
            return tsc_result

        with patch("subprocess.run", side_effect=fake_run):
            validator._check_tsc(project_ctx)

        tsc_calls = [c for c in call_args_list if "tsc" in c and "--version" not in c]
        assert tsc_calls, "Expected a tsc --noEmit call"
        tsc_cmd = tsc_calls[0]
        assert "--incremental" not in tsc_cmd
        assert "--tsBuildInfoFile" not in tsc_cmd

    def test_verifiers_no_cache_env_disables_flags(
        self, validator: TsQualityValidator, tmp_project: Path, project_ctx: ProjectContext
    ) -> None:
        """VERIFIERS_NO_CACHE=1 prevents --cache and --incremental flags."""
        call_args_list: list = []

        def fake_run(cmd, **kwargs):
            call_args_list.append(list(cmd))
            raise FileNotFoundError

        with patch.dict(os.environ, {"VERIFIERS_NO_CACHE": "1"}):
            with patch("subprocess.run", side_effect=fake_run):
                validator._check_eslint_single(project_ctx, str(tmp_project / "web/src/app.ts"))

        eslint_calls = [c for c in call_args_list if "eslint" in c]
        assert eslint_calls, "Expected an eslint call"
        eslint_cmd = eslint_calls[0]
        assert "--cache" not in eslint_cmd
        assert "--cache-strategy" not in eslint_cmd
        assert "--cache-location" not in eslint_cmd
