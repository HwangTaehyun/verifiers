"""V51: ADR Template Compliance validator.

Checks:
  V51-ADR-MISSING-SECTION: An ADR file is missing one of the required
    Michael Nygard canonical sections (Context, Decision, Consequences, Status).
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

# ── ADR directory candidates (tried in order) ────────────────────────────────

_ADR_DIR_CANDIDATES: tuple[str, ...] = (
    "docs/ADR",
    "docs/adr",
    "docs/architecture/decisions",
    "docs/decisions",
)

# Files to skip (templates, indexes, numbered placeholders)
_SKIP_NAMES: frozenset[str] = frozenset({"template.md", "README.md", "index.md"})

# Nygard required sections (case-insensitive ## header match)
_REQUIRED_SECTIONS: tuple[str, ...] = ("Context", "Decision", "Consequences")


def _find_adr_dir(root: Path) -> Path | None:
    """Return the first existing ADR directory, or None."""
    for candidate in _ADR_DIR_CANDIDATES:
        d = root / candidate
        if d.is_dir():
            return d
    return None


def _has_status(content: str) -> bool:
    """Return True if the ADR has a status indicator (lenient check).

    Accepts any of:
      - frontmatter ``status:`` field  (``---\\nstatus: accepted\\n---``)
      - ``## Status`` section header  (case-insensitive)
      - ``**Status**:`` bold line in body
    """
    lower = content.lower()
    # frontmatter status: field
    if re.search(r"^---\s*\n(?:.*\n)*?status\s*:", content, re.MULTILINE | re.IGNORECASE):
        return True
    # ## Status section
    if re.search(r"^##\s+status\b", lower, re.MULTILINE):
        return True
    # **Status**: anywhere in body
    if re.search(r"\*\*status\*\*\s*:", lower):
        return True
    return False


def _has_section(content: str, section: str) -> bool:
    """Return True if content has a ``## <section>`` header (case-insensitive).

    Also accepts plural form for Decision → Decisions.
    """
    pattern = r"^##\s+" + re.escape(section)
    if section.lower() == "decision":
        pattern = r"^##\s+decisions?"
    return bool(re.search(pattern, content, re.MULTILINE | re.IGNORECASE))


class AdrTemplateComplianceValidator(BaseValidator):
    """V51: ADR (Architecture Decision Record) Template Compliance."""

    id = "V51-adr-template-compliance"
    name = "ADR (Architecture Decision Record) Template Compliance"
    file_patterns: list[str] = [
        "docs/ADR/*.md",
        "docs/adr/*.md",
        "docs/architecture/decisions/*.md",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: check the single ADR file just edited."""
        return self._check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: scan all ADR files in the project."""
        return self._check(ctx)

    # ── Internal ─────────────────────────────────────────────────────────────

    def _check(self, ctx: ProjectContext) -> list[Finding]:
        """Locate the ADR directory and validate each ADR file."""
        adr_dir = _find_adr_dir(ctx.project_root)
        if adr_dir is None:
            return []

        findings: list[Finding] = []
        for adr_file in sorted(adr_dir.glob("*.md")):
            # Skip templates, indexes, and 0000-* numbered placeholders
            if adr_file.name in _SKIP_NAMES:
                continue
            if adr_file.name.startswith("0000-"):
                continue

            try:
                content = adr_file.read_text(errors="replace")
            except OSError:
                continue

            # Check required Nygard sections
            for section in _REQUIRED_SECTIONS:
                if not _has_section(content, section):
                    findings.append(
                        Finding(
                            severity="info",
                            file=str(adr_file),
                            rule="V51-ADR-MISSING-SECTION",
                            message=(
                                f"ADR `{adr_file.name}` is missing the `## {section}` section required by Michael Nygard's "
                                f"canonical format. Without it, future readers can't reconstruct the {section.lower()}-level "
                                f"reasoning."
                            ),
                            fix=(
                                f"Add a `## {section}` heading to {adr_file.name} with prose explaining the {section.lower()}.\n"
                                f"Reference: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions"
                            ),
                        )
                    )

            # Check status indicator (lenient)
            if not _has_status(content):
                findings.append(
                    Finding(
                        severity="info",
                        file=str(adr_file),
                        rule="V51-ADR-MISSING-SECTION",
                        message=(
                            f"ADR `{adr_file.name}` is missing the `## Status` section required by Michael Nygard's "
                            f"canonical format. Without it, future readers can't reconstruct the status-level "
                            f"reasoning."
                        ),
                        fix=(
                            f"Add a `## Status` heading to {adr_file.name} with prose explaining the status.\n"
                            f"Reference: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions"
                        ),
                    )
                )

        return findings


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
    validator = AdrTemplateComplianceValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
