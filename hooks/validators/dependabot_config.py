"""V42: Dependabot / Renovate Config Presence validator.

Enforces that a repository declares automated dependency update configuration
via either ``.github/dependabot.yml`` (GitHub's native tool) or
``.github/renovate.json`` (alternative), covering the required package
ecosystems for a Go+Node.js+GitHub Actions monorepo.

Rules:
  - V42-NO-DEPENDABOT — neither dependabot.yml nor renovate.json exists,
    OR dependabot.yml is present but unparseable (treated as absent)
  - V42-DEPENDABOT-MISSING-ECOSYSTEM — dependabot.yml exists but is missing
    a required ecosystem entry (gomod, npm, github-actions), conditional on
    whether the corresponding dependency files are present in the repo
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402


class DependabotConfigValidator(BaseValidator):
    """V42: Dependabot / Renovate Config Presence."""

    id = "V42-dependabot-config"
    name = "Dependabot / Renovate Config Presence"
    file_patterns: list[str] = [
        ".github/dependabot.yml",
        ".github/dependabot.yaml",
        ".github/renovate.json",
        ".github/renovate.json5",
        "renovate.json",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: one of the config files was just edited — run the full check."""
        return self.validate_project(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: project-level check for dependency update config presence."""
        root = Path(ctx.project_root)
        github_dir = root / ".github"

        # Locate dependabot config (prefer .yml, also accept .yaml)
        dependabot_file: Path | None = None
        for name in ("dependabot.yml", "dependabot.yaml"):
            candidate = github_dir / name
            if candidate.is_file():
                dependabot_file = candidate
                break

        # Locate renovate config (any of the accepted forms)
        renovate_file: Path | None = None
        for candidate_path in (
            github_dir / "renovate.json",
            github_dir / "renovate.json5",
            root / "renovate.json",
        ):
            if candidate_path.is_file():
                renovate_file = candidate_path
                break

        # If Renovate is present, count as satisfied — don't inspect its schema
        if renovate_file is not None:
            return []

        # If dependabot config is present, validate its ecosystems
        if dependabot_file is not None:
            return list(self._check_dependabot_content(ctx, dependabot_file))

        # Neither exists
        return [
            Finding(
                severity="warning",
                file=str(github_dir),
                rule="V42-NO-DEPENDABOT",
                message=(
                    "Neither .github/dependabot.yml nor .github/renovate.json exists. "
                    "Automated dependency PRs are not flowing for this repo's Go modules / "
                    "npm packages / GitHub Actions ecosystems. CVE backlog accumulates silently."
                ),
                fix=(
                    "Add .github/dependabot.yml with at least these ecosystems:\n"
                    "  - package-ecosystem: gomod\n    directory: /server\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: npm\n    directory: /web\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: github-actions\n    directory: /\n    schedule: { interval: monthly }"
                ),
            )
        ]

    # ── Internals ──────────────────────────────────────────────────────

    def _required_ecosystems(self, ctx: ProjectContext) -> list[str]:
        """Compute the set of required ecosystems based on what the project has."""
        root = Path(ctx.project_root)
        required: list[str] = ["github-actions"]
        if (root / "server" / "go.mod").is_file():
            required.append("gomod")
        if (root / "web" / "package.json").is_file():
            required.append("npm")
        return required

    def _check_dependabot_content(self, ctx: ProjectContext, dependabot_file: Path) -> Iterator[Finding]:
        """Parse dependabot.yml and check for required ecosystem entries.

        If the file is unparseable, treat it as absent (V42-NO-DEPENDABOT).
        """
        try:
            data = yaml.safe_load(dependabot_file.read_text(errors="replace"))
        except Exception:
            yield Finding(
                severity="warning",
                file=str(Path(ctx.project_root) / ".github"),
                rule="V42-NO-DEPENDABOT",
                message=(
                    "Neither .github/dependabot.yml nor .github/renovate.json exists. "
                    "Automated dependency PRs are not flowing for this repo's Go modules / "
                    "npm packages / GitHub Actions ecosystems. CVE backlog accumulates silently."
                ),
                fix=(
                    "Add .github/dependabot.yml with at least these ecosystems:\n"
                    "  - package-ecosystem: gomod\n    directory: /server\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: npm\n    directory: /web\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: github-actions\n    directory: /\n    schedule: { interval: monthly }"
                ),
            )
            return

        if not isinstance(data, dict) or "updates" not in data:
            yield Finding(
                severity="warning",
                file=str(Path(ctx.project_root) / ".github"),
                rule="V42-NO-DEPENDABOT",
                message=(
                    "Neither .github/dependabot.yml nor .github/renovate.json exists. "
                    "Automated dependency PRs are not flowing for this repo's Go modules / "
                    "npm packages / GitHub Actions ecosystems. CVE backlog accumulates silently."
                ),
                fix=(
                    "Add .github/dependabot.yml with at least these ecosystems:\n"
                    "  - package-ecosystem: gomod\n    directory: /server\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: npm\n    directory: /web\n    schedule: { interval: weekly }\n"
                    "  - package-ecosystem: github-actions\n    directory: /\n    schedule: { interval: monthly }"
                ),
            )
            return

        updates = data.get("updates", [])
        if not isinstance(updates, list):
            return

        declared = {u.get("package-ecosystem") for u in updates if isinstance(u, dict)}

        for ecosystem in self._required_ecosystems(ctx):
            if ecosystem not in declared:
                yield Finding(
                    severity="warning",
                    file=str(dependabot_file),
                    rule="V42-DEPENDABOT-MISSING-ECOSYSTEM",
                    message=(
                        f"dependabot.yml has no entry for `{ecosystem}` ecosystem; "
                        "this repo has matching deps that won't auto-update."
                    ),
                    fix=f"Add `package-ecosystem: {ecosystem}` entry to .github/dependabot.yml updates: array.",
                )


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = DependabotConfigValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
