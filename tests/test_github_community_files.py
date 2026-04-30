"""Tests for V53 — github-community-files.

Covers:
  - All three files present → pass
  - V53-NO-PR-TEMPLATE — no PR template found
  - Lowercase PR template accepted
  - V53-NO-ISSUE-TEMPLATE — no issue templates found
  - Legacy single-file ISSUE_TEMPLATE.md accepted
  - Empty ISSUE_TEMPLATE/ directory still flags
  - V53-NO-CODEOWNERS — no CODEOWNERS found
  - CODEOWNERS at project root accepted
  - CODEOWNERS under docs/ accepted
  - No .github/ directory → no findings
  - Not a git repo → no findings
  - validate_file (Tier 2) triggers full check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.github_community_files import GithubCommunityFilesValidator


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> GithubCommunityFilesValidator:
    return GithubCommunityFilesValidator()


@pytest.fixture
def github_dir(tmp_project: Path) -> Path:
    """Create and return .github/ under tmp_project."""
    d = tmp_project / ".github"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. All three present → pass ──────────────────────────────────────────


class TestAllThreePresentPasses:
    def test_all_three_present_passes(self, validator, tmp_project, github_dir, project_ctx):
        """PR template + ISSUE_TEMPLATE dir with file + CODEOWNERS → no findings."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. No PR template → V53-NO-PR-TEMPLATE ──────────────────────────────


class TestNoPrTemplateWarns:
    def test_no_pr_template_warns(self, validator, tmp_project, github_dir, project_ctx):
        """Absent PR template → V53-NO-PR-TEMPLATE warning."""
        # Add issue template and CODEOWNERS so only PR template is missing
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V53-NO-PR-TEMPLATE" in rules
        finding = next(f for f in findings if f.rule == "V53-NO-PR-TEMPLATE")
        assert finding.severity == "warning"


# ── 3. Lowercase PR template accepted ───────────────────────────────────


class TestPrTemplateLowercaseFormPasses:
    def test_pr_template_lowercase_form_passes(self, validator, tmp_project, github_dir, project_ctx):
        """.github/pull_request_template.md (lowercase) is accepted."""
        (github_dir / "pull_request_template.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        pr_findings = [f for f in findings if f.rule == "V53-NO-PR-TEMPLATE"]
        assert pr_findings == []


# ── 4. No issue template → V53-NO-ISSUE-TEMPLATE ────────────────────────


class TestNoIssueTemplateWarns:
    def test_no_issue_template_warns(self, validator, tmp_project, github_dir, project_ctx):
        """Absent ISSUE_TEMPLATE dir and no legacy file → V53-NO-ISSUE-TEMPLATE warning."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V53-NO-ISSUE-TEMPLATE" in rules
        finding = next(f for f in findings if f.rule == "V53-NO-ISSUE-TEMPLATE")
        assert finding.severity == "warning"


# ── 5. Legacy single-file ISSUE_TEMPLATE.md accepted ────────────────────


class TestLegacySingleIssueTemplatePasses:
    def test_legacy_single_issue_template_passes(self, validator, tmp_project, github_dir, project_ctx):
        """.github/ISSUE_TEMPLATE.md (no directory) is accepted."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        (github_dir / "ISSUE_TEMPLATE.md").write_text("## Bug\n")
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        issue_findings = [f for f in findings if f.rule == "V53-NO-ISSUE-TEMPLATE"]
        assert issue_findings == []


# ── 6. Empty ISSUE_TEMPLATE dir still flags ──────────────────────────────


class TestEmptyIssueTemplateDirWarns:
    def test_empty_issue_template_dir_warns(self, validator, tmp_project, github_dir, project_ctx):
        """ISSUE_TEMPLATE/ directory with no .md/.yml files → still flag."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        # No files inside → empty directory
        (github_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V53-NO-ISSUE-TEMPLATE" in rules


# ── 7. No CODEOWNERS → V53-NO-CODEOWNERS ────────────────────────────────


class TestNoCodeownersWarns:
    def test_no_codeowners_warns(self, validator, tmp_project, github_dir, project_ctx):
        """Absent CODEOWNERS in all locations → V53-NO-CODEOWNERS warning."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")

        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V53-NO-CODEOWNERS" in rules
        finding = next(f for f in findings if f.rule == "V53-NO-CODEOWNERS")
        assert finding.severity == "warning"


# ── 8. CODEOWNERS at project root accepted ───────────────────────────────


class TestCodeownersInRootPasses:
    def test_codeowners_in_root_passes(self, validator, tmp_project, github_dir, project_ctx):
        """<root>/CODEOWNERS (without .github/) is accepted."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")
        (tmp_project / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        co_findings = [f for f in findings if f.rule == "V53-NO-CODEOWNERS"]
        assert co_findings == []


# ── 9. CODEOWNERS under docs/ accepted ──────────────────────────────────


class TestCodeownersInDocsPasses:
    def test_codeowners_in_docs_passes(self, validator, tmp_project, github_dir, project_ctx):
        """<root>/docs/CODEOWNERS is accepted."""
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")
        issue_dir = github_dir / "ISSUE_TEMPLATE"
        issue_dir.mkdir()
        (issue_dir / "bug_report.md").write_text("---\nname: Bug\n---\n")
        docs_dir = tmp_project / "docs"
        docs_dir.mkdir(exist_ok=True)
        (docs_dir / "CODEOWNERS").write_text("* @owner\n")

        findings = validator.validate_project(project_ctx)
        co_findings = [f for f in findings if f.rule == "V53-NO-CODEOWNERS"]
        assert co_findings == []


# ── 10. No .github/ dir → no findings ───────────────────────────────────


class TestNoDotGithubDirReturnsEmpty:
    def test_no_dot_github_dir_returns_empty(self, validator, tmp_project, project_ctx):
        """Project has .git but no .github/ → not applicable, no findings."""
        # tmp_project has .git/ but no .github/ by default
        assert not (tmp_project / ".github").exists()

        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 11. Not a git repo → no findings ────────────────────────────────────


class TestNoGitDirReturnsEmpty:
    def test_no_git_dir_returns_empty(self, validator, tmp_path):
        """No .git directory → not a repo, return empty findings."""
        # Create .github but no .git
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "PULL_REQUEST_TEMPLATE.md").write_text("## Summary\n")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 12. validate_file runs full check ────────────────────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, github_dir, project_ctx):
        """Tier 2: editing a .github file triggers same check as validate_project."""
        # Only PR template present — issue template and CODEOWNERS missing
        pr_path = github_dir / "PULL_REQUEST_TEMPLATE.md"
        pr_path.write_text("## Summary\n")

        findings_file = validator.validate_file(project_ctx, str(pr_path))
        findings_project = validator.validate_project(project_ctx)

        assert len(findings_file) == len(findings_project)
        rules_file = {f.rule for f in findings_file}
        rules_project = {f.rule for f in findings_project}
        assert rules_file == rules_project
