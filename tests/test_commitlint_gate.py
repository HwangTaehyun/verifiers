"""Tests for V54 — commitlint-gate.

Covers:
  - No consumption signal → no findings
  - Consumption via devDependencies (conventional-changelog) + no enforcement → V54
  - Consumption via CHANGELOG.md Keep-a-Changelog format + no enforcement → V54
  - commitlint.config.js present → satisfied (no V54)
  - .husky/commit-msg present → satisfied (no V54)
  - lefthook.yml with commit-msg key → satisfied (no V54)
  - .pre-commit-config.yaml with conventional-pre-commit → satisfied (no V54)
  - .commitlintrc.json present → satisfied (no V54)
  - validate_file delegates to same check as validate_project
  - Malformed lefthook.yml → exception handled, no crash
"""

from __future__ import annotations

import json

import pytest

from hooks.validators.commitlint_gate import CommitlintGateValidator


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> CommitlintGateValidator:
    return CommitlintGateValidator()


# ── 1. No consumption signal → no findings ───────────────────────────────────


class TestNoConsumptionNoFindings:
    def test_no_consumption_no_findings(self, validator, tmp_project, project_ctx):
        """No changelog generator, no Keep-a-Changelog CHANGELOG.md → no V54."""
        # tmp_project has server/go.mod and basic structure but no conventional-changelog
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. Consumption via devDeps + no enforcement → V54 ────────────────────────


class TestConsumesViaDevDepsNoEnforcementWarns:
    def test_consumes_via_devDeps_no_enforcement_warns(self, validator, tmp_project, project_ctx):
        """package.json has conventional-changelog in devDependencies but no commitlint config → V54."""
        (tmp_project / "package.json").write_text(
            json.dumps(
                {
                    "name": "my-project",
                    "devDependencies": {
                        "conventional-changelog-cli": "^4.1.0",
                    },
                }
            )
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V54-COMMITLINT-NOT-ENFORCED" in rules
        finding = next(f for f in findings if f.rule == "V54-COMMITLINT-NOT-ENFORCED")
        assert finding.severity == "warning"
        assert "conventional commits" in finding.message


# ── 3. Consumption via CHANGELOG.md format + no enforcement → V54 ────────────


class TestConsumesViaChangelogFormatNoEnforcementWarns:
    def test_consumes_via_changelog_format_no_enforcement_warns(self, validator, tmp_project, project_ctx):
        """CHANGELOG.md with ## [Unreleased] header but no enforcement → V54."""
        (tmp_project / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [Unreleased]\n### Added\n- nothing yet\n\n## [1.0.0] - 2026-01-01\n"
        )

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V54-COMMITLINT-NOT-ENFORCED" in rules

    def test_consumes_via_versioned_changelog_header_warns(self, validator, tmp_project, project_ctx):
        """CHANGELOG.md with ## [N.N.N] header (no [Unreleased]) also triggers detection."""
        (tmp_project / "CHANGELOG.md").write_text("# Changelog\n\n## [2.3.1] - 2026-03-15\n### Fixed\n- a bug\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V54-COMMITLINT-NOT-ENFORCED" in rules


# ── 4. commitlint.config.js satisfies enforcement ────────────────────────────


class TestCommitlintConfigJsSatisfies:
    def test_commitlint_config_js_satisfies(self, validator, tmp_project, project_ctx):
        """commitlint.config.js at root satisfies enforcement → no V54."""
        (tmp_project / "package.json").write_text(
            json.dumps({"devDependencies": {"conventional-changelog-cli": "^4.1.0"}})
        )
        (tmp_project / "commitlint.config.js").write_text(
            "export default { extends: ['@commitlint/config-conventional'] };\n"
        )

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. .husky/commit-msg satisfies enforcement ───────────────────────────────


class TestHuskyCommitMsgSatisfies:
    def test_husky_commit_msg_satisfies(self, validator, tmp_project, project_ctx):
        """.husky/commit-msg file present → enforcement satisfied → no V54."""
        (tmp_project / "package.json").write_text(
            json.dumps({"devDependencies": {"conventional-changelog-cli": "^4.1.0"}})
        )
        husky_dir = tmp_project / ".husky"
        husky_dir.mkdir(exist_ok=True)
        (husky_dir / "commit-msg").write_text('#!/bin/sh\nbunx commitlint --edit "$1"\n')

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 6. lefthook.yml with commit-msg key satisfies enforcement ─────────────────


class TestLefthookCommitMsgSatisfies:
    def test_lefthook_commit_msg_satisfies(self, validator, tmp_project, project_ctx):
        """lefthook.yml with commit-msg: key → enforcement satisfied → no V54."""
        (tmp_project / "package.json").write_text(
            json.dumps({"devDependencies": {"conventional-changelog-cli": "^4.1.0"}})
        )
        (tmp_project / "lefthook.yml").write_text(
            "commit-msg:\n  commands:\n    commitlint:\n      run: bunx commitlint --edit {1}\n"
        )

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 7. .pre-commit-config.yaml with conventional-pre-commit satisfies ─────────


class TestPreCommitConventionalSatisfies:
    def test_pre_commit_conventional_satisfies(self, validator, tmp_project, project_ctx):
        """.pre-commit-config.yaml mentioning conventional-pre-commit → no V54."""
        (tmp_project / "CHANGELOG.md").write_text("## [Unreleased]\n")
        (tmp_project / ".pre-commit-config.yaml").write_text(
            "repos:\n"
            "  - repo: https://github.com/compilerla/conventional-pre-commit\n"
            "    rev: v3.4.0\n"
            "    hooks:\n"
            "      - id: conventional-pre-commit\n"
        )

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. .commitlintrc.json satisfies enforcement ──────────────────────────────


class TestDotCommitlintrcJsonSatisfies:
    def test_dotcommitlintrc_json_satisfies(self, validator, tmp_project, project_ctx):
        """.commitlintrc.json at root → enforcement satisfied → no V54."""
        (tmp_project / "package.json").write_text(
            json.dumps({"devDependencies": {"conventional-changelog-cli": "^4.1.0"}})
        )
        (tmp_project / ".commitlintrc.json").write_text(json.dumps({"extends": ["@commitlint/config-conventional"]}))

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 9. validate_file delegates to full check ─────────────────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, project_ctx):
        """Tier 2: validate_file returns same findings as validate_project."""
        (tmp_project / "package.json").write_text(
            json.dumps({"devDependencies": {"conventional-changelog-cli": "^4.1.0"}})
        )
        pkg_path = str(tmp_project / "package.json")

        findings_file = validator.validate_file(project_ctx, pkg_path)
        findings_project = validator.validate_project(project_ctx)

        assert len(findings_file) == len(findings_project)
        assert {f.rule for f in findings_file} == {f.rule for f in findings_project}


# ── 10. Malformed lefthook.yml handled gracefully ────────────────────────────


class TestInvalidYamlHandled:
    def test_invalid_yaml_handled(self, validator, tmp_project, project_ctx):
        """Malformed lefthook.yml → exception logged, validator does not crash."""
        (tmp_project / "CHANGELOG.md").write_text("## [Unreleased]\n")
        # Write deliberately invalid YAML
        (tmp_project / "lefthook.yml").write_text("commit-msg:\n  commands:\n    bad: [unclosed\n")

        # Must not raise; lefthook.yml parse failure means enforcement not satisfied
        findings = validator.validate_project(project_ctx)
        # CHANGELOG.md signals consumption; broken lefthook.yml → no enforcement → warn
        rules = [f.rule for f in findings]
        assert "V54-COMMITLINT-NOT-ENFORCED" in rules
