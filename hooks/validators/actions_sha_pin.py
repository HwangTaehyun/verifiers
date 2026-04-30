"""V40: GitHub Actions SHA Pinning validator.

Enforces that all ``uses:`` lines in GitHub Actions workflow files
reference a 40-character immutable commit SHA instead of a floating
tag (``@v4``, ``@latest``, ``@main``, etc.).

Rules:
  - V40-ACTION-NOT-PINNED     — third-party action uses a floating tag (error)
  - V40-FIRST-PARTY-NOT-PINNED — GitHub-owned ``actions/*`` uses a floating tag (warning)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Match lines like:   - uses: owner/repo@ref  # optional comment
# Captures the full ref including the @part via two named groups.
_USES_LINE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")

# A valid SHA pin is exactly 40 lowercase hex characters.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ActionsSHAPinValidator(BaseValidator):
    """V40: GitHub Actions SHA Pinning."""

    id = "V40-actions-sha-pin"
    name = "GitHub Actions SHA Pinning"
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
        """Tier 3: scan all workflow files under .github/workflows/."""
        workflows_dir = Path(ctx.project_root) / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return []

        findings: list[Finding] = []
        for pattern in ("*.yml", "*.yaml"):
            for wf_file in sorted(workflows_dir.glob(pattern)):
                findings.extend(self._check_workflow(wf_file))
        return findings

    # ── Internals ──────────────────────────────────────────────────────

    def _check_workflow(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []
        return list(self._scan_lines(src, file_path))

    def _scan_lines(self, src: str, file_path: Path):
        for line_no, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()

            # Skip blank lines and comment lines
            if not stripped or stripped.startswith("#"):
                continue

            m = _USES_LINE.match(line)
            if not m:
                continue

            ref_full = m.group(1)

            # Skip local action references (no @)
            if "@" not in ref_full:
                continue

            # Skip Docker actions
            if ref_full.startswith("docker://"):
                continue

            owner_repo, ref = ref_full.split("@", 1)

            # Strip inline comment from ref if present (e.g. "v4 # comment")
            ref = ref.split("#")[0].strip()

            # Already pinned to a 40-char SHA — pass
            if re.fullmatch(r"[0-9a-f]{40}", ref):
                continue

            is_first_party = owner_repo.startswith("actions/")

            if is_first_party:
                yield Finding(
                    severity="warning",
                    file=str(file_path),
                    line=line_no,
                    rule="V40-FIRST-PARTY-NOT-PINNED",
                    message=(f"GitHub-owned action '{owner_repo}' uses floating tag '{ref}' instead of immutable SHA"),
                    fix=(
                        f"Pin to SHA: `uses: {owner_repo}@<40-char-sha>  # {ref}`. "
                        "Use `pin-github-action` CLI or Dependabot github-actions "
                        "ecosystem to keep pins fresh."
                    ),
                )
            else:
                yield Finding(
                    severity="error",
                    file=str(file_path),
                    line=line_no,
                    rule="V40-ACTION-NOT-PINNED",
                    message=(f"Third-party action '{owner_repo}' uses floating tag '{ref}' instead of immutable SHA"),
                    fix=(
                        f"Pin to SHA: `uses: {owner_repo}@<40-char-sha>  # {ref}`. "
                        "Use `pin-github-action` CLI or Dependabot github-actions "
                        "ecosystem to keep pins fresh."
                    ),
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
    validator = ActionsSHAPinValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
