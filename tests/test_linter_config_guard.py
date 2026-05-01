"""Tests for V16: LinterConfigGuardValidator — enforce linter configurations.

Covers:
  - Go: golangci-lint config detection and rule checking
  - Python: ruff config detection and rule checking
  - TypeScript: ESLint config detection and rule checking
  - Missing config detection
  - should_run / validate integration
  - main(): standalone execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.linter_config_guard import (
    LinterConfigGuardValidator,
    _check_eslint_rules,
    _check_golangci_rules,
    _check_ruff_rules,
    _find_eslint_config,
    _find_golangci_config,
    _find_ruff_config,
    _has_go_files,
    _has_python_files,
    _has_ts_files,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> LinterConfigGuardValidator:
    return LinterConfigGuardValidator()


def _write_file(base: Path, name: str, content: str) -> str:
    fp = base / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    return str(fp)


# ============================================================================
# 1. Language detection
# ============================================================================


class TestLanguageDetection:
    """Phase 71: helpers now take a ProjectContext (queries ctx.file_index)
    instead of walking the filesystem directly. The DEFAULT_PRUNE_NAMES set
    in ``lib/file_index.py`` already filters vendor/node_modules/.venv at
    walk-time, so the dedicated "excluded" tests still pass — the exclusion
    just happens at a different layer."""

    def _ctx(self, root: Path):
        from lib.project_context import ProjectContext

        return ProjectContext(str(root))

    def test_has_go_files(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "main.go", "package main")
        assert _has_go_files(self._ctx(tmp_path)) is True

    def test_has_go_files_empty(self, tmp_path: Path) -> None:
        assert _has_go_files(self._ctx(tmp_path)) is False

    def test_has_go_files_vendor_excluded(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "vendor/lib.go", "package vendor")
        assert _has_go_files(self._ctx(tmp_path)) is False

    def test_has_python_files(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "app.py", "print('hello')")
        assert _has_python_files(self._ctx(tmp_path)) is True

    def test_has_python_files_venv_excluded(self, tmp_path: Path) -> None:
        _write_file(tmp_path, ".venv/lib.py", "pass")
        assert _has_python_files(self._ctx(tmp_path)) is False

    def test_has_ts_files(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "src/App.tsx", "export default App")
        assert _has_ts_files(self._ctx(tmp_path)) is True

    def test_has_ts_files_node_modules_excluded(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "node_modules/lib.js", "module.exports = {}")
        assert _has_ts_files(self._ctx(tmp_path)) is False


# ============================================================================
# 2. Config file detection
# ============================================================================


class TestConfigDetection:
    def test_find_golangci_yml(self, tmp_path: Path) -> None:
        _write_file(tmp_path, ".golangci.yml", "linters:")
        assert _find_golangci_config(tmp_path) is not None

    def test_find_golangci_yaml(self, tmp_path: Path) -> None:
        _write_file(tmp_path, ".golangci.yaml", "linters:")
        assert _find_golangci_config(tmp_path) is not None

    def test_find_golangci_in_server(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "server", ".golangci.yml", "linters:")
        assert _find_golangci_config(tmp_path) is not None

    def test_find_golangci_missing(self, tmp_path: Path) -> None:
        assert _find_golangci_config(tmp_path) is None

    def test_find_ruff_toml(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "ruff.toml", "[lint]\nselect = ['E']")
        config, config_type = _find_ruff_config(tmp_path)
        assert config is not None
        assert config_type == "ruff.toml"

    def test_find_ruff_in_pyproject(self, tmp_path: Path) -> None:
        _write_file(
            tmp_path,
            "pyproject.toml",
            "[project]\nname = 'test'\n\n[tool.ruff]\nline-length = 88\n",
        )
        config, config_type = _find_ruff_config(tmp_path)
        assert config is not None
        assert config_type == "pyproject.toml"

    def test_find_ruff_missing(self, tmp_path: Path) -> None:
        config, config_type = _find_ruff_config(tmp_path)
        assert config is None

    def test_find_eslint_config_js(self, tmp_path: Path) -> None:
        _write_file(tmp_path, "eslint.config.js", "export default []")
        assert _find_eslint_config(tmp_path) is not None

    def test_find_eslintrc_json(self, tmp_path: Path) -> None:
        _write_file(tmp_path, ".eslintrc.json", "{}")
        assert _find_eslint_config(tmp_path) is not None

    def test_find_eslint_in_web(self, tmp_path: Path) -> None:
        _write_file(tmp_path / "web", "eslint.config.js", "export default []")
        assert _find_eslint_config(tmp_path) is not None

    def test_find_eslint_missing(self, tmp_path: Path) -> None:
        assert _find_eslint_config(tmp_path) is None


# ============================================================================
# 3. Go config rule checks
# ============================================================================


class TestGolangciRules:
    def test_errcheck_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".golangci.yml",
                "linters:\n  disable:\n    - errcheck\n",
            )
        )
        findings = _check_golangci_rules(fp)
        assert any(f.rule == "V16-MISSING-ERROR-RULES" for f in findings)

    def test_unused_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".golangci.yml",
                "linters:\n  disable:\n    - unused\n",
            )
        )
        findings = _check_golangci_rules(fp)
        assert any(f.rule == "V16-MISSING-UNUSED-RULES" for f in findings)

    def test_gosec_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".golangci.yml",
                "linters:\n  disable:\n    - gosec\n",
            )
        )
        findings = _check_golangci_rules(fp)
        assert any(f.rule == "V16-MISSING-SECURITY-RULES" for f in findings)

    def test_clean_config_no_findings(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".golangci.yml",
                "linters:\n  enable:\n    - errcheck\n    - gosec\n    - unused\n",
            )
        )
        findings = _check_golangci_rules(fp)
        assert len(findings) == 0

    def test_empty_config_no_findings(self, tmp_path: Path) -> None:
        fp = Path(_write_file(tmp_path, ".golangci.yml", ""))
        findings = _check_golangci_rules(fp)
        assert len(findings) == 0


# ============================================================================
# 4. Python ruff rule checks
# ============================================================================


class TestRuffRules:
    def test_e722_ignored(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nignore = ["E722", "W291"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert any(f.rule == "V16-MISSING-ERROR-RULES" for f in findings)

    def test_f401_ignored(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nignore = ["F401"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert any(f.rule == "V16-MISSING-UNUSED-RULES" for f in findings)

    def test_no_security_rules_selected(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nselect = ["E", "F", "W"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert any(f.rule == "V16-MISSING-SECURITY-RULES" for f in findings)

    def test_security_rules_selected(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nselect = ["E", "F", "S"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert not any(f.rule == "V16-MISSING-SECURITY-RULES" for f in findings)

    def test_all_selected_includes_security(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nselect = ["ALL"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert not any(f.rule == "V16-MISSING-SECURITY-RULES" for f in findings)

    def test_clean_config_no_findings(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                "ruff.toml",
                '[lint]\nselect = ["E", "F", "S"]\n',
            )
        )
        findings = _check_ruff_rules(fp, "ruff.toml")
        assert len(findings) == 0


# ============================================================================
# 5. TypeScript ESLint rule checks
# ============================================================================


class TestEslintRules:
    def test_no_empty_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"no-empty": "off"}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert any(f.rule == "V16-MISSING-ERROR-RULES" for f in findings)

    def test_no_empty_disabled_numeric(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"no-empty": 0}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert any(f.rule == "V16-MISSING-ERROR-RULES" for f in findings)

    def test_no_unused_vars_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"no-unused-vars": "off"}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert any(f.rule == "V16-MISSING-UNUSED-RULES" for f in findings)

    def test_ts_no_unused_vars_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"@typescript-eslint/no-unused-vars": "off"}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert any(f.rule == "V16-MISSING-UNUSED-RULES" for f in findings)

    def test_no_eval_disabled(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"no-eval": "off"}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert any(f.rule == "V16-MISSING-SECURITY-RULES" for f in findings)

    def test_clean_config_no_findings(self, tmp_path: Path) -> None:
        fp = Path(
            _write_file(
                tmp_path,
                ".eslintrc.json",
                '{"rules": {"no-empty": "error", "no-unused-vars": "warn"}}',
            )
        )
        findings = _check_eslint_rules(fp)
        assert len(findings) == 0


# ============================================================================
# 6. Integration tests — validate()
# ============================================================================


class TestValidateIntegration:
    def test_go_project_no_config(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path, "main.go", "package main")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=None, mode="stop")
        assert any(f.rule == "V16-NO-LINTER-CONFIG" for f in result.findings)

    def test_python_project_no_config(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path, "app.py", "print('hello')")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=None, mode="stop")
        assert any(f.rule == "V16-NO-LINTER-CONFIG" for f in result.findings)

    def test_ts_project_no_config(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path / "src", "App.tsx", "export default App")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=None, mode="stop")
        assert any(f.rule == "V16-NO-LINTER-CONFIG" for f in result.findings)

    def test_go_project_with_config_no_findings(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path, "main.go", "package main")
        _write_file(
            tmp_path,
            ".golangci.yml",
            "linters:\n  enable:\n    - errcheck\n    - unused\n    - gosec\n",
        )

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=None, mode="stop")
        assert not any(f.rule == "V16-NO-LINTER-CONFIG" for f in result.findings)

    def test_skip_in_post_tool_use_mode(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path, "main.go", "package main")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path="main.go", mode="post_tool_use")
        assert len(result.findings) == 0

    def test_empty_project_no_findings(self, validator: LinterConfigGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=None, mode="stop")
        assert len(result.findings) == 0


# ============================================================================
# 7. Standalone main()
# ============================================================================


class TestMain:
    def test_main_with_missing_config(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        _write_file(tmp_path, "main.go", "package main")

        input_data = {"cwd": str(tmp_path)}
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert "additionalContext" in output
        assert "V16-NO-LINTER-CONFIG" in output["additionalContext"]

    def test_main_empty_input(self) -> None:
        stdout = _run_main(None)
        output = json.loads(stdout)
        assert output.get("decision") == "approve"


# ── Module-level helpers ─────────────────────────────────────────────────────


def _run_main(input_data: dict | None) -> str:
    from hooks.validators.linter_config_guard import main

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
