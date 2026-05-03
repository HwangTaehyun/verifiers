"""Tests for V71 — react-hooks-plugin (Phase 73)."""

from __future__ import annotations

import pytest

from hooks.validators.react_hooks_plugin import ReactHooksPluginValidator


@pytest.fixture
def validator() -> ReactHooksPluginValidator:
    return ReactHooksPluginValidator()


def _add_tsx(tmp_project):
    f = tmp_project / "web" / "src" / "App.tsx"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("export const x = 1;\n")


# ── 1. Non-React project (no .tsx) → no findings ─────────────────────────────


class TestNonReactNoOp:
    def test_no_tsx_files_silent(self, validator, tmp_project, project_ctx):
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. React project + no ESLint config → V71-NO-ESLINT-CONFIG ───────────────


class TestNoEslintConfig:
    def test_no_config_warns(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V71-NO-ESLINT-CONFIG"
        assert findings[0].severity == "warning"


# ── 3. Both rules at 'error' level → no findings ─────────────────────────────


class TestBothRulesEnforcedPasses:
    def test_flat_config_both_error_passes(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / "eslint.config.js").write_text(
            "import reactHooks from 'eslint-plugin-react-hooks';\n"
            "export default [{\n"
            "  plugins: { 'react-hooks': reactHooks },\n"
            "  rules: {\n"
            "    'react-hooks/rules-of-hooks': 'error',\n"
            "    'react-hooks/exhaustive-deps': 'error',\n"
            "  }\n"
            "}];\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_eslintrc_json_both_error_passes(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / ".eslintrc.json").write_text(
            '{\n  "rules": {\n'
            '    "react-hooks/rules-of-hooks": "error",\n'
            '    "react-hooks/exhaustive-deps": "error"\n'
            "  }\n}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. One rule at warn / off → V71-HOOKS-RULE-NOT-ENFORCED ──────────────────


class TestRuleNotEnforced:
    def test_rule_at_warn_flagged(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / "eslint.config.js").write_text(
            "export default [{\n"
            "  rules: {\n"
            "    'react-hooks/rules-of-hooks': 'error',\n"
            "    'react-hooks/exhaustive-deps': 'warn',\n"
            "  }\n}];\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V71-HOOKS-RULE-NOT-ENFORCED"
        assert "exhaustive-deps" in findings[0].message
        assert "warn" in findings[0].message

    def test_rule_at_off_flagged(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / "eslint.config.js").write_text(
            "export default [{\n"
            "  rules: {\n"
            "    'react-hooks/rules-of-hooks': 'off',\n"
            "    'react-hooks/exhaustive-deps': 'error',\n"
            "  }\n}];\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V71-HOOKS-RULE-NOT-ENFORCED"
        assert "rules-of-hooks" in findings[0].message

    def test_numeric_level_1_recognized_as_warn(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / ".eslintrc.json").write_text(
            '{\n  "rules": {\n'
            '    "react-hooks/rules-of-hooks": 2,\n'
            '    "react-hooks/exhaustive-deps": 1\n'
            "  }\n}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert "exhaustive-deps" in findings[0].message
        assert "warn" in findings[0].message  # numeric 1 → "warn"


# ── 5. Rule completely missing → V71-HOOKS-RULE-MISSING ──────────────────────


class TestRuleMissing:
    def test_one_rule_missing_flagged(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / ".eslintrc.json").write_text(
            '{\n  "rules": {\n'
            '    "react-hooks/rules-of-hooks": "error"\n'
            "  }\n}\n"
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 1
        assert findings[0].rule == "V71-HOOKS-RULE-MISSING"
        assert "exhaustive-deps" in findings[0].message

    def test_both_rules_missing(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / ".eslintrc.json").write_text(
            '{\n  "rules": {}\n}\n'
        )
        findings = validator.validate_project(project_ctx)
        assert len(findings) == 2
        assert all(f.rule == "V71-HOOKS-RULE-MISSING" for f in findings)


# ── 6. Config in web/ subdir also detected ───────────────────────────────────


class TestConfigInWebSubdir:
    def test_web_subdir_config_detected(self, validator, tmp_project, project_ctx):
        _add_tsx(tmp_project)
        (tmp_project / "web" / "eslint.config.js").write_text(
            "export default [{ rules: {\n"
            "  'react-hooks/rules-of-hooks': 'error',\n"
            "  'react-hooks/exhaustive-deps': 'error',\n"
            "} }];\n"
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []
