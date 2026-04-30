"""Tests for V38 — golangci-strictness.

Covers:
  - V38-NO-WRAPCHECK      — wrapcheck absent from linters.enable
  - V38-WEAK-NOLINTLINT   — require-specific or require-explanation false/absent
  - V38-NO-GOFUMPT        — gofumpt absent from linters.enable (warning)
  - Full strict config passes with no findings
  - Missing golangci config file produces no findings
  - Invalid YAML handled gracefully (no crash)
  - v2 schema (version: "2") supported
  - validate_file (Tier 2) path
  - Multiple findings in one file
"""

from __future__ import annotations


import pytest

from hooks.validators.golangci_strictness import GolangciStrictnessValidator


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GolangciStrictnessValidator:
    return GolangciStrictnessValidator()


STRICT_CONFIG = """\
linters:
  enable:
    - errcheck
    - govet
    - wrapcheck
    - gofumpt
    - nolintlint

linters-settings:
  nolintlint:
    require-specific: true
    require-explanation: true
"""

STRICT_CONFIG_V2 = """\
version: "2"
linters:
  default: standard
  enable:
    - wrapcheck
    - gofumpt
    - nolintlint

settings:
  nolintlint:
    require-specific: true
    require-explanation: true
"""


# ── 1. Full strict config passes ──────────────────────────────────────


class TestFullStrictConfigPasses:
    def test_full_strict_config_passes(self, validator, tmp_project, project_ctx):
        """wrapcheck + nolintlint (both flags) + gofumpt all set → no findings."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(STRICT_CONFIG)

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. No wrapcheck → V38-NO-WRAPCHECK ────────────────────────────────


class TestNoWrapcheckErrors:
    def test_no_wrapcheck_errors(self, validator, tmp_project, project_ctx):
        """wrapcheck absent from linters.enable → V38-NO-WRAPCHECK error."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n"
            "  enable:\n"
            "    - errcheck\n"
            "    - govet\n"
            "    - gofumpt\n"
            "    - nolintlint\n"
            "linters-settings:\n"
            "  nolintlint:\n"
            "    require-specific: true\n"
            "    require-explanation: true\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-NO-WRAPCHECK" in rules
        wrapcheck_finding = next(f for f in findings if f.rule == "V38-NO-WRAPCHECK")
        assert wrapcheck_finding.severity == "error"


# ── 3. Weak nolintlint with explicit false ─────────────────────────────


class TestWeakNolintlintRequireSpecificFalseErrors:
    def test_weak_nolintlint_require_specific_false_errors(self, validator, tmp_project, project_ctx):
        """nolintlint with require-specific: false → V38-WEAK-NOLINTLINT error."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n"
            "  enable:\n"
            "    - wrapcheck\n"
            "    - gofumpt\n"
            "    - nolintlint\n"
            "linters-settings:\n"
            "  nolintlint:\n"
            "    require-specific: false\n"
            "    require-explanation: true\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-WEAK-NOLINTLINT" in rules
        weak = next(f for f in findings if f.rule == "V38-WEAK-NOLINTLINT")
        assert weak.severity == "error"
        assert "require-specific" in weak.message


# ── 4. Weak nolintlint with missing keys ──────────────────────────────


class TestWeakNolintlintMissingKeysErrors:
    def test_weak_nolintlint_missing_keys_errors(self, validator, tmp_project, project_ctx):
        """nolintlint section present but both keys absent → V38-WEAK-NOLINTLINT."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n"
            "  enable:\n"
            "    - wrapcheck\n"
            "    - gofumpt\n"
            "    - nolintlint\n"
            "linters-settings:\n"
            "  nolintlint:\n"
            "    allow-unused: false\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-WEAK-NOLINTLINT" in rules
        weak = next(f for f in findings if f.rule == "V38-WEAK-NOLINTLINT")
        assert weak.severity == "error"


# ── 5. No gofumpt → V38-NO-GOFUMPT warning ────────────────────────────


class TestNoGofumptWarns:
    def test_no_gofumpt_warns(self, validator, tmp_project, project_ctx):
        """gofumpt absent → V38-NO-GOFUMPT warning (not error)."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n"
            "  enable:\n"
            "    - wrapcheck\n"
            "    - nolintlint\n"
            "linters-settings:\n"
            "  nolintlint:\n"
            "    require-specific: true\n"
            "    require-explanation: true\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-NO-GOFUMPT" in rules
        gofumpt = next(f for f in findings if f.rule == "V38-NO-GOFUMPT")
        assert gofumpt.severity == "warning"


# ── 6. No golangci yaml → no findings ─────────────────────────────────


class TestNoGolangciYamlNoFindings:
    def test_no_golangci_yaml_no_findings(self, validator, tmp_project, project_ctx):
        """No .golangci.yaml in project → no findings (validator skips gracefully)."""
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. Invalid YAML handled gracefully ────────────────────────────────


class TestInvalidYamlHandledGracefully:
    def test_invalid_yaml_handled_gracefully(self, validator, tmp_project, project_ctx):
        """Malformed YAML → no crash, no findings."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text("linters:\n  enable:\n    - [unclosed bracket\n  invalid: yaml:\n")

        # Must not raise
        findings = validator.validate_project(project_ctx)
        # Either no findings or some — just no crash
        assert isinstance(findings, list)


# ── 8. v2 schema supported ────────────────────────────────────────────


class TestV2SchemaSupported:
    def test_v2_schema_supported(self, validator, tmp_project, project_ctx):
        """version: "2" config schema with same checks passes when strict."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(STRICT_CONFIG_V2)

        findings = validator.validate_project(project_ctx)
        assert findings == []

    def test_v2_schema_missing_wrapcheck_errors(self, validator, tmp_project, project_ctx):
        """v2 schema without wrapcheck → V38-NO-WRAPCHECK."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            'version: "2"\n'
            "linters:\n"
            "  default: standard\n"
            "  enable:\n"
            "    - gofumpt\n"
            "    - nolintlint\n"
            "settings:\n"
            "  nolintlint:\n"
            "    require-specific: true\n"
            "    require-explanation: true\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-NO-WRAPCHECK" in rules


# ── 9. validate_file (Tier 2) path ────────────────────────────────────


class TestValidateFileSingleConfig:
    def test_validate_file_single_config(self, validator, tmp_project, project_ctx):
        """Tier 2: validate_file runs checks on the given config file."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n  enable:\n    - errcheck\n"
            # wrapcheck absent → V38-NO-WRAPCHECK
        )

        findings = validator.validate_file(project_ctx, str(config_path))
        rules = [f.rule for f in findings]
        assert "V38-NO-WRAPCHECK" in rules

    def test_validate_file_nonexistent_path_no_findings(self, validator, tmp_project, project_ctx):
        """validate_file on a nonexistent path → no findings, no crash."""
        findings = validator.validate_file(project_ctx, str(tmp_project / "server" / ".golangci.yaml"))
        assert findings == []


# ── 10. Multiple findings in one file ─────────────────────────────────


class TestMultipleFindingsInOneFile:
    def test_multiple_findings_in_one_file(self, validator, tmp_project, project_ctx):
        """wrapcheck absent + weak nolintlint + no gofumpt → 3 findings."""
        config_path = tmp_project / "server" / ".golangci.yaml"
        config_path.write_text(
            "linters:\n"
            "  enable:\n"
            "    - errcheck\n"
            "    - nolintlint\n"
            # wrapcheck absent → V38-NO-WRAPCHECK
            # gofumpt absent  → V38-NO-GOFUMPT
            "linters-settings:\n"
            "  nolintlint:\n"
            "    require-specific: false\n"
            "    require-explanation: false\n"
            # → V38-WEAK-NOLINTLINT
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V38-NO-WRAPCHECK" in rules
        assert "V38-WEAK-NOLINTLINT" in rules
        assert "V38-NO-GOFUMPT" in rules
        assert len(findings) == 3
