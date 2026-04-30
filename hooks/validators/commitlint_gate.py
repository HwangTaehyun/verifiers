"""V54: Commitlint / Conventional Commits Gate validator.

Fires when a project already *consumes* conventional commits (changelog
generator in package.json deps/scripts, or a Keep-a-Changelog-formatted
CHANGELOG.md) but has no *enforcement* gate (no commitlint config, no
husky commit-msg hook, no lefthook commit-msg entry, no pre-commit
conventional-pre-commit hook).

Rules:
  - V54-COMMITLINT-NOT-ENFORCED — project consumes conventional commits
    but has no commit-message linting gate.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

logger = logging.getLogger(__name__)

# Commitlint config file names recognised as enforcement
_COMMITLINT_CONFIGS = [
    "commitlint.config.js",
    "commitlint.config.ts",
    "commitlint.config.cjs",
    "commitlint.config.mjs",
    "commitlint.config.json",
    ".commitlintrc.js",
    ".commitlintrc.ts",
    ".commitlintrc.json",
    ".commitlintrc.yml",
    ".commitlintrc.yaml",
    ".commitlintrc.cjs",
    ".commitlintrc.mjs",
]

# Keep-a-Changelog header pattern
_CHANGELOG_HEADER_RE = re.compile(r"^## \[Unreleased\]|^## \[\d+\.\d+", re.MULTILINE)


class CommitlintGateValidator(BaseValidator):
    """V54: Commitlint / Conventional Commits Gate."""

    id = "V54-commitlint-gate"
    name = "Commitlint / Conventional Commits Gate"
    file_patterns: list[str] = [
        "**/package.json",
        "commitlint.config.*",
        ".pre-commit-config.yaml",
        ".pre-commit-config.yml",
        "lefthook.yml",
        ".husky/**",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: a monitored file changed — run the full project check."""
        return self._check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: project-level check."""
        return self._check(ctx)

    # ── Core logic ────────────────────────────────────────────────────

    def _check(self, ctx: ProjectContext) -> list[Finding]:
        root = Path(ctx.project_root)

        consumes = self._detects_consumption(root)
        if not consumes:
            return []

        enforced = self._detects_enforcement(root)
        if enforced:
            return []

        return [
            Finding(
                severity="warning",
                file=str(root),
                rule="V54-COMMITLINT-NOT-ENFORCED",
                message=(
                    "Project consumes conventional commits (changelog generator / Keep-a-Changelog format) "
                    "but has no enforcement gate. Contributors omitting `feat:`/`fix:` prefix produce silently "
                    "incomplete changelogs."
                ),
                fix=(
                    "Pick one of:\n"
                    "  (a) commitlint.config.js + husky:\n"
                    "      bun add -D @commitlint/cli @commitlint/config-conventional husky\n"
                    "      echo \"export default { extends: ['@commitlint/config-conventional'] }\" > commitlint.config.js\n"
                    "      bunx husky init && echo 'bunx commitlint --edit \"$1\"' > .husky/commit-msg\n"
                    "  (b) lefthook (Go-friendly):\n"
                    "      lefthook.yml with commit-msg hook running commitlint\n"
                    "  (c) pre-commit:\n"
                    "      .pre-commit-config.yaml with `compilerla/conventional-pre-commit`"
                ),
            )
        ]

    # ── Step 1: detect consumption ────────────────────────────────────

    def _detects_consumption(self, root: Path) -> bool:
        """Return True if the project uses conventional commits tooling or format."""
        # Check all package.json files under root
        for pkg_path in root.rglob("package.json"):
            try:
                data = json.loads(pkg_path.read_text(errors="replace"))
            except Exception:
                logger.debug("commitlint_gate: failed to parse %s", pkg_path)
                continue

            if not isinstance(data, dict):
                continue

            all_deps: dict[str, str] = {}
            all_deps.update(data.get("dependencies", {}) or {})
            all_deps.update(data.get("devDependencies", {}) or {})

            if any("conventional-changelog" in k for k in all_deps):
                return True

            scripts_text = " ".join((data.get("scripts") or {}).values())
            if "conventional-changelog" in scripts_text:
                return True

        # Heuristic: CHANGELOG.md with Keep-a-Changelog headers
        changelog = root / "CHANGELOG.md"
        if changelog.is_file():
            try:
                text = changelog.read_text(errors="replace")
                if _CHANGELOG_HEADER_RE.search(text):
                    return True
            except Exception:
                logger.debug("commitlint_gate: failed to read CHANGELOG.md")

        return False

    # ── Step 2: detect enforcement ────────────────────────────────────

    def _detects_enforcement(self, root: Path) -> bool:
        """Return True if any commitlint enforcement gate is present."""
        # commitlint config files at repo root
        for name in _COMMITLINT_CONFIGS:
            if (root / name).is_file():
                return True

        # .husky/commit-msg
        if (root / ".husky" / "commit-msg").is_file():
            return True

        # commitlint in any package.json deps
        for pkg_path in root.rglob("package.json"):
            try:
                data = json.loads(pkg_path.read_text(errors="replace"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            all_deps: dict[str, str] = {}
            all_deps.update(data.get("dependencies", {}) or {})
            all_deps.update(data.get("devDependencies", {}) or {})
            if any("commitlint" in k for k in all_deps):
                return True

        # lefthook.yml with commit-msg hook
        lefthook = root / "lefthook.yml"
        if lefthook.is_file():
            try:
                text = lefthook.read_text(errors="replace")
                data = yaml.safe_load(text)
                if isinstance(data, dict) and "commit-msg" in data:
                    return True
            except Exception:
                logger.debug("commitlint_gate: failed to parse lefthook.yml")

        # .pre-commit-config.yaml with conventional-pre-commit
        for pre_commit_name in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
            pre_commit = root / pre_commit_name
            if not pre_commit.is_file():
                continue
            try:
                text = pre_commit.read_text(errors="replace")
                if "conventional-pre-commit" in text:
                    return True
            except Exception:
                logger.debug("commitlint_gate: failed to parse %s", pre_commit_name)

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
    validator = CommitlintGateValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
