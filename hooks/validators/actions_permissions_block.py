"""V41: GitHub Actions Permissions Block validator.

Enforces that every workflow file declares an explicit ``permissions:``
block — either at the top-level (workflow-wide) or on every individual
job — so the GITHUB_TOKEN scope is never left to org-wide defaults.

Rules:
  - V41-NO-PERMISSIONS-BLOCK — no top-level permissions AND at least one
    job without job-level permissions (warning)
"""

from __future__ import annotations

import sys
from pathlib import Path

from lib.workflow_loader import parse_workflow, walk_workflows

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402


class ActionsPermissionsBlockValidator(BaseValidator):
    """V41: GitHub Actions Permissions Block."""

    id = "V41-actions-permissions-block"
    name = "GitHub Actions Permissions Block"
    file_patterns: list[str] = [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: scan the single workflow file that was just edited."""
        path = Path(file_path)
        if not path.is_file():
            return []
        return self._check_workflow(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: scan all workflow files under .github/workflows/.

        Phase60: directory walk + YAML parse extracted to
        ``lib.workflow_loader.walk_workflows``.
        """
        findings: list[Finding] = []
        for wf_file, workflow in walk_workflows(ctx.project_root):
            findings.extend(self._check_workflow_data(wf_file, workflow))
        return findings

    # ── Internals ──────────────────────────────────────────────────────

    def _check_workflow(self, file_path: Path) -> list[Finding]:
        """Tier 2 entrypoint — parse the single edited file then check."""
        workflow = parse_workflow(file_path)
        if workflow is None:
            return []
        return self._check_workflow_data(file_path, workflow)

    def _check_workflow_data(self, file_path: Path, workflow: dict) -> list[Finding]:
        # Pass: top-level permissions key present (even if empty dict)
        if "permissions" in workflow:
            return []

        # Pass: no jobs defined (e.g., composite/reusable workflow) — nothing to scope
        jobs = workflow.get("jobs") or {}
        if not isinstance(jobs, dict) or not jobs:
            return []

        # Pass: every job has its own permissions key
        all_jobs_have_permissions = all(
            isinstance(job_def, dict) and "permissions" in job_def for job_def in jobs.values()
        )
        if all_jobs_have_permissions:
            return []

        # Fail: no top-level permissions AND at least one job without permissions
        return [
            Finding(
                severity="warning",
                file=str(file_path),
                rule="V41-NO-PERMISSIONS-BLOCK",
                message=(
                    "Workflow has no top-level `permissions:` and not every job declares its own. "
                    "GITHUB_TOKEN scope is undefined per least-privilege; supply-chain compromise blast radius unknown."
                ),
                fix=(
                    "Add `permissions: {}` (deny-all) at the workflow top level, then grant per-job: "
                    "e.g. for a checkout-only job, `permissions: { contents: read }`. "
                    "Reference: https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions"
                ),
            )
        ]


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = ActionsPermissionsBlockValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
