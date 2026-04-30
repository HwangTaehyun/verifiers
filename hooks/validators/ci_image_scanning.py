"""V43: CI Image Scanning validator.

Enforces that every workflow job that builds a Docker image (via
``docker build`` in a ``run:`` step or ``docker/build-push-action``)
is followed by an image scanner either in the same job or in a
downstream job that declares ``needs:`` on the build job.

Rules:
  - V43-NO-IMAGE-SCAN — build job has no scanner in same or dependent job (error)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402
from lib.workflow_loader import walk_workflow_paths  # noqa: E402

# Known scanner GitHub Actions (prefix-matched so any version suffix works)
_SCANNER_ACTIONS = [
    "aquasecurity/trivy-action",
    "anchore/scan-action",
    "snyk/actions/docker",
    "docker/scout-action",
]


class CiImageScanningValidator(BaseValidator):
    """V43: CI Image Scanning."""

    id = "V43-ci-image-scanning"
    name = "CI Image Scanning"
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

        Phase60: dir walker extracted to lib.workflow_loader.walk_workflow_paths.
        """
        findings: list[Finding] = []
        for wf_file in walk_workflow_paths(ctx.project_root):
            findings.extend(self._check_workflow(wf_file))
        return findings

    # ── Internals ──────────────────────────────────────────────────────

    def _check_workflow(self, file_path: Path) -> list[Finding]:
        try:
            data = yaml.safe_load(file_path.read_text(errors="replace"))
        except Exception:
            return []

        if not isinstance(data, dict) or "jobs" not in data:
            return []

        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict):
            return []

        findings: list[Finding] = []
        for job_name, job_config in jobs.items():
            if not isinstance(job_config, dict):
                continue
            if not self._has_docker_build(job_config):
                continue
            if not self._has_scanner(job_config, jobs, job_name):
                findings.append(
                    Finding(
                        severity="error",
                        file=str(file_path),
                        rule="V43-NO-IMAGE-SCAN",
                        message=(
                            f"Job '{job_name}' builds a Docker image (via `docker build` or "
                            f"`docker/build-push-action`) but neither it nor any "
                            f"downstream-dependent job runs an image scanner. "
                            f"CVE in production base images go undetected."
                        ),
                        fix=(
                            "Add a Trivy scan step in the same job after the build, or in a dependent job:\n"
                            "    - uses: aquasecurity/trivy-action@<sha>\n"
                            "      with:\n"
                            "        image-ref: ${{ env.IMAGE_TAG }}\n"
                            "        severity: CRITICAL,HIGH\n"
                            "        exit-code: 1"
                        ),
                    )
                )
        return findings

    def _has_docker_build(self, job_config: dict) -> bool:
        """Return True if the job contains a docker build step."""
        steps = job_config.get("steps", [])
        if not isinstance(steps, list):
            return False
        for step in steps:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses", "") or ""
            run = step.get("run", "") or ""
            if "docker/build-push-action" in uses:
                return True
            if "docker build" in run:
                return True
        return False

    def _step_has_scanner(self, step: dict) -> bool:
        """Return True if a single step invokes a known image scanner."""
        if not isinstance(step, dict):
            return False
        uses = (step.get("uses", "") or "").lower()
        run = (step.get("run", "") or "").lower()
        for action in _SCANNER_ACTIONS:
            if action in uses:
                return True
        # Bare shell commands: grype or trivy (with trailing space to avoid
        # matching "trivial", "trivium", etc.)
        if "grype" in run or "trivy " in run:
            return True
        return False

    def _job_has_scanner_step(self, job_config: dict) -> bool:
        """Return True if any step in the job invokes an image scanner."""
        steps = job_config.get("steps", [])
        if not isinstance(steps, list):
            return False
        return any(self._step_has_scanner(s) for s in steps)

    def _has_scanner(self, job_config: dict, all_jobs: dict, job_name: str) -> bool:
        """Return True if this job or a downstream job that needs it runs a scanner."""
        # Same job
        if self._job_has_scanner_step(job_config):
            return True
        # Downstream jobs that declare `needs: <job_name>`
        for other_name, other_job in all_jobs.items():
            if not isinstance(other_job, dict):
                continue
            needs = other_job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            if not isinstance(needs, list):
                continue
            if job_name in needs and self._job_has_scanner_step(other_job):
                return True
        return False


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = CiImageScanningValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
