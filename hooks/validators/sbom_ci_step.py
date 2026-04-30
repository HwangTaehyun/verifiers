"""V57: SBOM CI Step validator.

Enforces that at least one GitHub Actions workflow generates a
Software Bill of Materials (SBOM) artifact using a recognised tool.
SBOM generation is distinct from CVE scanning (V43): V43 checks that
*images are scanned*; V57 checks that a *machine-readable dependency
inventory is produced as an artifact*.

Rules:
  - V57-NO-SBOM-CI — no workflow in .github/workflows/ generates an
    SBOM (warning)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402
from lib.workflow_loader import walk_workflow_paths  # noqa: E402

logger = logging.getLogger(__name__)

# GitHub Actions that produce an SBOM artifact (prefix-matched).
_SBOM_ACTIONS = [
    "anchore/sbom-action",
    "cyclonedx/gh-gomod-generate-sbom",
    "microsoft/sbom-action",
]

# Shell commands whose presence in a ``run:`` step indicates SBOM generation.
_SBOM_COMMANDS = [
    "cyclonedx-gomod",
    "syft",
]

# Trivy output formats that constitute an SBOM (not just a vulnerability scan).
_TRIVY_SBOM_FORMATS = {"cyclonedx", "spdx-json", "spdx_json"}


class SbomCiStepValidator(BaseValidator):
    """V57: SBOM Generation in CI."""

    id = "V57-sbom-ci-step"
    name = "SBOM Generation in CI"
    file_patterns: list[str] = [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: single file was edited — still run the full project check.

        SBOM presence is a project-level property (any one workflow satisfies
        the requirement), so editing one file triggers the same cross-workflow
        scan as Tier 3.
        """
        return self._check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: comprehensive check across all workflows."""
        return self._check(ctx)

    # ── Internals ──────────────────────────────────────────────────────

    def _check(self, ctx: ProjectContext) -> list[Finding]:
        """Walk all workflows; return finding if none produce an SBOM.

        Phase60: dir walker extracted to lib.workflow_loader.walk_workflow_paths.
        """
        workflows_dir = Path(ctx.project_root) / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return []

        # Early-bail walk: stop as soon as one workflow produces SBOM.
        any_workflow = False
        for wf_file in walk_workflow_paths(ctx.project_root):
            any_workflow = True
            if self._workflow_has_sbom(wf_file):
                return []

        if not any_workflow:
            return []

        return [
            Finding(
                severity="warning",
                file=str(workflows_dir),
                rule="V57-NO-SBOM-CI",
                message=(
                    "No SBOM (Software Bill of Materials) generator detected in any workflow. "
                    "Without machine-readable dependency inventory, supply-chain audits and "
                    "regulatory compliance (NTIA minimum elements, EU Cyber Resilience Act) "
                    "are blocked. V43 catches CVE scanning; V57 enforces SBOM artifact generation."
                ),
                fix=(
                    "Add to .github/workflows/ci.yml after the build step:\n"
                    "  - name: Generate SBOM\n"
                    "    uses: anchore/sbom-action@v0\n"
                    "    with:\n"
                    "      format: cyclonedx-json\n"
                    "      output-file: sbom.cdx.json\n"
                    "  - uses: actions/upload-artifact@v4\n"
                    "    with: { name: sbom, path: sbom.cdx.json }\n"
                    "Alternative: cyclonedx/gh-gomod-generate-sbom (Go-only) or "
                    "syft via run command."
                ),
            )
        ]

    def _workflow_has_sbom(self, file_path: Path) -> bool:
        """Return True if any step in this workflow generates an SBOM."""
        try:
            data = yaml.safe_load(file_path.read_text(errors="replace"))
        except Exception:
            logger.warning("V57: failed to parse %s", file_path)
            return False

        if not isinstance(data, dict):
            return False

        jobs = data.get("jobs", {})
        if not isinstance(jobs, dict):
            return False

        for job_config in jobs.values():
            if not isinstance(job_config, dict):
                continue
            steps = job_config.get("steps", [])
            if not isinstance(steps, list):
                continue
            for step in steps:
                if self._step_is_sbom(step):
                    return True

        return False

    def _step_is_sbom(self, step: object) -> bool:
        """Return True if a single workflow step produces an SBOM."""
        if not isinstance(step, dict):
            return False

        uses = (step.get("uses", "") or "").lower()
        run = (step.get("run", "") or "").lower()
        with_block = step.get("with", {}) or {}

        # Known SBOM-specific GitHub Actions
        for action in _SBOM_ACTIONS:
            if uses.startswith(action.lower()):
                return True

        # Shell commands that are SBOM generators
        for cmd in _SBOM_COMMANDS:
            if cmd in run:
                return True

        # Trivy with an SBOM-format output (cyclonedx or spdx-json)
        if "aquasecurity/trivy-action" in uses:
            fmt = str(with_block.get("format", "")).lower().strip("'\"")
            if fmt in _TRIVY_SBOM_FORMATS:
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
    validator = SbomCiStepValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
