"""Tests for V42 — dependabot-config.

Covers:
  - V42-NO-DEPENDABOT — neither dependabot.yml nor renovate.json exists
  - V42-DEPENDABOT-MISSING-ECOSYSTEM — dependabot.yml present but missing required ecosystem
  - .yaml extension recognised as well as .yml
  - Renovate config (any form) counts as satisfied — no ecosystem checks
  - Ecosystem requirement is conditional on project files
  - Invalid YAML treated as absent (V42-NO-DEPENDABOT)
  - validate_file (Tier 2) triggers full project check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.dependabot_config import DependabotConfigValidator


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> DependabotConfigValidator:
    return DependabotConfigValidator()


@pytest.fixture
def github_dir(tmp_project: Path) -> Path:
    """Create and return .github/ under tmp_project."""
    d = tmp_project / ".github"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Helper ───────────────────────────────────────────────────────────────

FULL_DEPENDABOT_YML = """\
version: 2
updates:
  - package-ecosystem: gomod
    directory: /server
    schedule:
      interval: weekly
  - package-ecosystem: npm
    directory: /web
    schedule:
      interval: weekly
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: monthly
"""


# ── 1. All required ecosystems present → pass ────────────────────────────


class TestDependabotYmlWithAllEcosystemsPasses:
    def test_dependabot_yml_with_all_ecosystems_passes(self, validator, tmp_project, github_dir, project_ctx):
        """dependabot.yml with gomod, npm, github-actions passes."""
        # tmp_project has server/go.mod; add web/package.json
        (tmp_project / "web" / "package.json").write_text('{"name": "web"}')
        (github_dir / "dependabot.yml").write_text(FULL_DEPENDABOT_YML)

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. .yaml extension recognised ────────────────────────────────────────


class TestDependabotYamlExtensionRecognised:
    def test_dependabot_yaml_extension_also_recognized(self, validator, tmp_project, github_dir, project_ctx):
        """.yaml extension is accepted as well as .yml."""
        (tmp_project / "web" / "package.json").write_text('{"name": "web"}')
        (github_dir / "dependabot.yaml").write_text(FULL_DEPENDABOT_YML)

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 3. Renovate json in .github/ passes ──────────────────────────────────


class TestRenovateJsonPasses:
    def test_renovate_json_passes(self, validator, tmp_project, github_dir, project_ctx):
        """.github/renovate.json counts as valid config — no findings."""
        (github_dir / "renovate.json").write_text('{"$schema": "https://docs.renovatebot.com/renovate-schema.json"}')

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 4. Root-level renovate.json passes ───────────────────────────────────


class TestRenovateRootPasses:
    def test_renovate_root_passes(self, validator, tmp_project, project_ctx):
        """root renovate.json counts as valid config — no findings."""
        (tmp_project / "renovate.json").write_text('{"$schema": "https://docs.renovatebot.com/renovate-schema.json"}')

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. No config file → V42-NO-DEPENDABOT ────────────────────────────────


class TestNoConfigFileWarns:
    def test_no_config_file_warns(self, validator, tmp_project, project_ctx):
        """No dependabot.yml or renovate.json → V42-NO-DEPENDABOT warning."""
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V42-NO-DEPENDABOT" in rules
        warning = next(f for f in findings if f.rule == "V42-NO-DEPENDABOT")
        assert warning.severity == "warning"


# ── 6. Missing gomod when server/go.mod present ──────────────────────────


class TestDependabotMissingGomodWhenGoModPresent:
    def test_dependabot_missing_gomod_when_go_mod_present(self, validator, tmp_project, github_dir, project_ctx):
        """server/go.mod exists but gomod ecosystem absent → flag it."""
        # tmp_project already has server/go.mod
        (github_dir / "dependabot.yml").write_text(
            "version: 2\n"
            "updates:\n"
            "  - package-ecosystem: npm\n"
            "    directory: /web\n"
            "    schedule:\n"
            "      interval: weekly\n"
            "  - package-ecosystem: github-actions\n"
            "    directory: /\n"
            "    schedule:\n"
            "      interval: monthly\n"
        )

        findings = validator.validate_project(project_ctx)
        missing_rules = [f.rule for f in findings if f.rule == "V42-DEPENDABOT-MISSING-ECOSYSTEM"]
        assert len(missing_rules) >= 1
        gomod_findings = [f for f in findings if f.rule == "V42-DEPENDABOT-MISSING-ECOSYSTEM" and "gomod" in f.message]
        assert len(gomod_findings) == 1
        assert gomod_findings[0].severity == "warning"


# ── 7. Missing github-actions ecosystem warns ─────────────────────────────


class TestDependabotMissingGithubActionsWarns:
    def test_dependabot_missing_github_actions_warns(self, validator, tmp_project, github_dir, project_ctx):
        """github-actions ecosystem is always required; absence flagged."""
        (github_dir / "dependabot.yml").write_text(
            "version: 2\n"
            "updates:\n"
            "  - package-ecosystem: gomod\n"
            "    directory: /server\n"
            "    schedule:\n"
            "      interval: weekly\n"
        )

        findings = validator.validate_project(project_ctx)
        ga_findings = [
            f for f in findings if f.rule == "V42-DEPENDABOT-MISSING-ECOSYSTEM" and "github-actions" in f.message
        ]
        assert len(ga_findings) == 1
        assert ga_findings[0].severity == "warning"


# ── 8. npm not required when web/package.json absent ─────────────────────


class TestDependabotNpmOptionalWhenNoPackageJson:
    def test_dependabot_npm_optional_when_no_package_json(self, validator, tmp_project, github_dir, project_ctx):
        """No web/package.json → npm ecosystem is not required."""
        # tmp_project does NOT have web/package.json by default
        (github_dir / "dependabot.yml").write_text(
            "version: 2\n"
            "updates:\n"
            "  - package-ecosystem: gomod\n"
            "    directory: /server\n"
            "    schedule:\n"
            "      interval: weekly\n"
            "  - package-ecosystem: github-actions\n"
            "    directory: /\n"
            "    schedule:\n"
            "      interval: monthly\n"
        )

        findings = validator.validate_project(project_ctx)
        npm_findings = [f for f in findings if f.rule == "V42-DEPENDABOT-MISSING-ECOSYSTEM" and "npm" in f.message]
        assert npm_findings == []
        assert findings == []


# ── 9. Invalid YAML handled gracefully → V42-NO-DEPENDABOT ───────────────


class TestInvalidYamlHandledGracefully:
    def test_invalid_yaml_handled_gracefully(self, validator, tmp_project, github_dir, project_ctx):
        """Malformed dependabot.yml is treated as absent — V42-NO-DEPENDABOT."""
        (github_dir / "dependabot.yml").write_text("version: 2\n  invalid: yaml:\n    - [unclosed\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V42-NO-DEPENDABOT" in rules
        # Must not raise


# ── 10. validate_file (Tier 2) triggers the full check ───────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, github_dir, project_ctx):
        """Tier 2: editing the dependabot.yml file triggers full project check."""
        dependabot_path = github_dir / "dependabot.yml"
        dependabot_path.write_text(
            "version: 2\n"
            "updates:\n"
            "  - package-ecosystem: gomod\n"
            "    directory: /server\n"
            "    schedule:\n"
            "      interval: weekly\n"
            # github-actions missing → should flag
        )

        findings_file = validator.validate_file(project_ctx, str(dependabot_path))
        findings_project = validator.validate_project(project_ctx)

        # Tier 2 must return same findings as Tier 3
        assert len(findings_file) == len(findings_project)
        rules_file = {f.rule for f in findings_file}
        rules_project = {f.rule for f in findings_project}
        assert rules_file == rules_project
