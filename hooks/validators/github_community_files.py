"""V53: GitHub Community Files validator.

Checks that a repository has the three essential community health files:
  1. A PR template (.github/PULL_REQUEST_TEMPLATE.md or lowercase variant)
  2. Issue templates (.github/ISSUE_TEMPLATE/ directory with at least one file,
     or legacy .github/ISSUE_TEMPLATE.md)
  3. A CODEOWNERS file (.github/CODEOWNERS, docs/CODEOWNERS, or root CODEOWNERS)

Rules:
  - V53-NO-PR-TEMPLATE      — no PR template found
  - V53-NO-ISSUE-TEMPLATE   — no issue templates found
  - V53-NO-CODEOWNERS       — no CODEOWNERS file found
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402


class GithubCommunityFilesValidator(BaseValidator):
    """V53: GitHub Community Files (PR/ISSUE templates + CODEOWNERS)."""

    id = "V53-github-community-files"
    name = "GitHub Community Files (PR/ISSUE templates + CODEOWNERS)"
    file_patterns: list[str] = [".github/**", "docs/**"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: a .github or docs file was just edited — run the full check."""
        return self._check_files(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: project-level check for community health files."""
        return self._check_files(ctx)

    # ── Internals ──────────────────────────────────────────────────────

    def _check_files(self, ctx: ProjectContext) -> list[Finding]:
        """Run all three community file checks and return combined findings."""
        root = Path(ctx.project_root)

        # Not a git repo → not applicable
        if not (root / ".git").exists():
            return []

        github_dir = root / ".github"

        # No .github directory → not applicable
        if not github_dir.is_dir():
            return []

        findings: list[Finding] = []

        # Rule 1: PR template
        pr_missing = not any(
            p.is_file()
            for p in [
                github_dir / "PULL_REQUEST_TEMPLATE.md",
                github_dir / "pull_request_template.md",
                root / "docs" / "PULL_REQUEST_TEMPLATE.md",
            ]
        )
        if pr_missing:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(github_dir),
                    rule="V53-NO-PR-TEMPLATE",
                    message=(
                        "No PR template found at .github/PULL_REQUEST_TEMPLATE.md or .github/pull_request_template.md. "
                        "PR descriptions are inconsistent across contributors; reviewers can't establish review checklist."
                    ),
                    fix=(
                        "Create .github/PULL_REQUEST_TEMPLATE.md with sections like:\n"
                        "  ## Summary\n  ## Changes\n  ## Test plan\n  ## Risk\n"
                        "See https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests"
                    ),
                )
            )

        # Rule 2: Issue templates
        issue_template_dir = github_dir / "ISSUE_TEMPLATE"
        legacy_issue_template = github_dir / "ISSUE_TEMPLATE.md"

        has_issue_template_dir = issue_template_dir.is_dir() and any(
            f.suffix in (".md", ".yml") for f in issue_template_dir.iterdir() if f.is_file()
        )
        has_legacy_issue_template = legacy_issue_template.is_file()

        if not has_issue_template_dir and not has_legacy_issue_template:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(github_dir),
                    rule="V53-NO-ISSUE-TEMPLATE",
                    message=(
                        "No issue templates at .github/ISSUE_TEMPLATE/. Bug reports lack repro steps; "
                        "feature requests lack scope/acceptance criteria."
                    ),
                    fix=(
                        "Create .github/ISSUE_TEMPLATE/bug_report.md and .github/ISSUE_TEMPLATE/feature_request.md "
                        "with structured frontmatter (name, about, labels, assignees) and body fields."
                    ),
                )
            )

        # Rule 3: CODEOWNERS
        codeowners_missing = not any(
            p.is_file()
            for p in [
                github_dir / "CODEOWNERS",
                root / "docs" / "CODEOWNERS",
                root / "CODEOWNERS",
            ]
        )
        if codeowners_missing:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(github_dir),
                    rule="V53-NO-CODEOWNERS",
                    message=(
                        "No CODEOWNERS file. High-blast-radius paths (server/internal/auth/, hasura/metadata/) "
                        "have no required-reviewer mapping; sensitive changes can land without domain-owner sign-off."
                    ),
                    fix=(
                        "Create .github/CODEOWNERS:\n"
                        "  /server/internal/auth/   @security-team\n"
                        "  /hasura/                 @data-team\n"
                        "  *.go                     @backend-team\n"
                        "  /web/                    @frontend-team\n"
                        "Then enable branch protection rule 'Require review from Code Owners'."
                    ),
                )
            )

        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GithubCommunityFilesValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
