"""V52: README badges validator.

Checks:
  V52-NO-CI-BADGE: README.md has no CI status badge.
  V52-NO-LICENSE-BADGE: README.md has no license badge.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# ── Badge detection patterns ──────────────────────────────────────────────────

_CI_BADGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https://github\.com/.+/actions/workflows/", re.IGNORECASE),
    re.compile(r"https://img\.shields\.io/github/actions/workflow/status/", re.IGNORECASE),
    re.compile(r"https://img\.shields\.io/github/workflow/status/", re.IGNORECASE),
]

_LICENSE_BADGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https://img\.shields\.io/github/license/", re.IGNORECASE),
    re.compile(r"https://img\.shields\.io/badge/license-", re.IGNORECASE),
    # Generic [![License](... badge
    re.compile(r"\[!\[License\]", re.IGNORECASE),
]


def _has_ci_badge(content: str) -> bool:
    return any(p.search(content) for p in _CI_BADGE_PATTERNS)


def _has_license_badge(content: str) -> bool:
    return any(p.search(content) for p in _LICENSE_BADGE_PATTERNS)


class ReadmeBadgesValidator(BaseValidator):
    """V52: README Badges (CI status + License) Validator."""

    id = "V52-readme-badges"
    name = "README Badges (CI status + License)"
    file_patterns: list[str] = ["README.md", "README.rst", "readme.md"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: delegate to shared check."""
        return self._check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: delegate to shared check."""
        return self._check(ctx)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check(self, ctx: ProjectContext) -> list[Finding]:
        """Locate root README and check for CI + license badges."""
        readme_path = self._find_readme(ctx.project_root)
        if readme_path is None:
            return []

        content = readme_path.read_text(errors="replace")
        findings: list[Finding] = []

        if not _has_ci_badge(content):
            findings.append(
                Finding(
                    severity="info",
                    file=str(readme_path),
                    rule="V52-NO-CI-BADGE",
                    message=(
                        "README.md has no CI status badge. Discoverability suffers — "
                        "first-time visitors can't see at a glance whether main is green."
                    ),
                    fix=(
                        "Add near the top of README.md:\n"
                        "  [![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)]"
                        "(https://github.com/<owner>/<repo>/actions/workflows/ci.yml)\n"
                        "Replace `<owner>/<repo>` with the actual GitHub path."
                    ),
                )
            )

        if not _has_license_badge(content):
            findings.append(
                Finding(
                    severity="info",
                    file=str(readme_path),
                    rule="V52-NO-LICENSE-BADGE",
                    message=(
                        "README.md has no license badge. Open-source consumers can't quickly "
                        "verify the license terms before integrating."
                    ),
                    fix=(
                        "Add to the badge row of README.md:\n"
                        "  [![License](https://img.shields.io/github/license/<owner>/<repo>.svg)]"
                        "(LICENSE)\n"
                        "Or use a static badge if licence file is non-standard."
                    ),
                )
            )

        return findings

    def _find_readme(self, root: Path) -> Path | None:
        """Return the root-level README file (case-insensitive), or None."""
        for candidate in ("README.md", "readme.md", "README.rst"):
            path = root / candidate
            if path.exists():
                return path
        # Broader case-insensitive fallback
        for child in root.iterdir():
            if child.is_file() and child.name.lower() in ("readme.md", "readme.rst"):
                return child
        return None


# ── Standalone execution ──────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone PostToolUse hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = ReadmeBadgesValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
