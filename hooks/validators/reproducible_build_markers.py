"""V58: Reproducible Build Markers (SOURCE_DATE_EPOCH) validator.

Checks that production Dockerfiles and/or CI workflows pass or declare
``SOURCE_DATE_EPOCH`` so that image layer hashes are deterministic across
builds with identical source code.

Rules:
  - V58-NO-SOURCE-DATE-EPOCH — a production Dockerfile lacks SOURCE_DATE_EPOCH
    marker and no CI workflow compensates (warning)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402
from lib.workflow_loader import walk_workflow_paths  # noqa: E402

# ── Dockerfile patterns ────────────────────────────────────────────────────────

# Matches: ARG SOURCE_DATE_EPOCH  (optional =value)
_ARG_SDE_RE = re.compile(r"^\s*ARG\s+SOURCE_DATE_EPOCH", re.IGNORECASE)

# Matches: ENV SOURCE_DATE_EPOCH=...
_ENV_SDE_RE = re.compile(r"^\s*ENV\s+SOURCE_DATE_EPOCH\s*=", re.IGNORECASE)

# Matches: --build-arg SOURCE_DATE_EPOCH referenced inline in a RUN/ARG line
_BUILDARG_SDE_RE = re.compile(r"--build-arg\s+SOURCE_DATE_EPOCH", re.IGNORECASE)

# Matches FROM lines; captures optional AS alias
_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?\S+(?:\s+(?:AS|as)\s+(\S+))?",
)

# ── Workflow patterns ─────────────────────────────────────────────────────────

# Step involves docker build or docker/build-push-action
_DOCKER_BUILD_RE = re.compile(r"docker\s+build|docker/build-push-action", re.IGNORECASE)

# build-args block contains SOURCE_DATE_EPOCH
_BUILDARGS_SDE_RE = re.compile(r"build-args\s*:.*SOURCE_DATE_EPOCH", re.IGNORECASE | re.DOTALL)

# A simpler line-level check for build-args: containing SOURCE_DATE_EPOCH
_BUILDARGS_LINE_RE = re.compile(r"SOURCE_DATE_EPOCH", re.IGNORECASE)

# Export / set env SOURCE_DATE_EPOCH in a workflow step
_EXPORT_SDE_RE = re.compile(r"SOURCE_DATE_EPOCH\s*=", re.IGNORECASE)


class ReproducibleBuildMarkersValidator(BaseValidator):
    """V58: Reproducible Build Markers (SOURCE_DATE_EPOCH)."""

    id = "V58-reproducible-build-markers"
    name = "Reproducible Build Markers (SOURCE_DATE_EPOCH)"
    file_patterns: list[str] = [
        "**/Dockerfile*",
        "**/*.Dockerfile",
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: delegate to full _check so context (other files) is considered."""
        return self._check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: full project sweep."""
        return self._check(ctx)

    # ── Core logic ────────────────────────────────────────────────────────────

    def _check(self, ctx: ProjectContext) -> list[Finding]:
        root = Path(ctx.project_root)

        # Collect all Dockerfiles
        dockerfiles: list[Path] = []
        seen: set[Path] = set()
        for pattern in ("**/Dockerfile*", "**/*.Dockerfile"):
            for df in sorted(root.glob(pattern)):
                if df.is_file() and df not in seen:
                    seen.add(df)
                    dockerfiles.append(df)

        if not dockerfiles:
            return []

        # Collect production Dockerfiles only
        prod_dockerfiles = [df for df in dockerfiles if not _is_dev_dockerfile(df)]

        if not prod_dockerfiles:
            return []

        # Check whether any CI workflow globally passes SOURCE_DATE_EPOCH
        workflow_satisfies = self._workflow_satisfies_sde(root)

        findings: list[Finding] = []
        for df in prod_dockerfiles:
            if workflow_satisfies or _dockerfile_has_sde_in_final_stage(df):
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(df),
                    rule="V58-NO-SOURCE-DATE-EPOCH",
                    message=(
                        "Production Dockerfile lacks SOURCE_DATE_EPOCH marker for reproducible builds. "
                        "Image hashes drift across builds even with identical source, blocking attestation, "
                        "caching efficiency, and supply-chain provenance verification."
                    ),
                    fix=(
                        "Option A — declare in Dockerfile:\n"
                        "  ARG SOURCE_DATE_EPOCH\n"
                        "  ENV SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH}\n"
                        "Option B — pass via CI build-args:\n"
                        "  - uses: docker/build-push-action@v5\n"
                        "    with:\n"
                        "      build-args: |\n"
                        "        SOURCE_DATE_EPOCH=${{ github.event.head_commit.timestamp }}\n"
                        "Reference: https://reproducible-builds.org/docs/source-date-epoch/"
                    ),
                )
            )
        return findings

    def _workflow_satisfies_sde(self, root: Path) -> bool:
        """Return True if any CI workflow passes SOURCE_DATE_EPOCH to a docker build step.

        Phase60: dir walker via lib.workflow_loader.walk_workflow_paths.
        """
        for wf_path in walk_workflow_paths(root):
            if _workflow_passes_sde(wf_path):
                return True
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_dev_dockerfile(path: Path) -> bool:
    """Return True if the Dockerfile is dev-only and should be exempt.

    Dev heuristics:
    1. Filename contains 'dev' (case-insensitive).
    2. The final FROM stage has an AS alias containing 'dev' but no 'prod' alias.
    """
    name = path.name.lower()
    if "dev" in name:
        return True

    # Check final FROM stage alias
    try:
        src = path.read_text(errors="replace")
    except OSError:
        return False

    final_alias = _get_final_stage_alias(src)
    if final_alias and "dev" in final_alias.lower():
        # Exempt unless there is also a prod* stage
        aliases = _get_all_stage_aliases(src)
        has_prod = any("prod" in a.lower() for a in aliases)
        if not has_prod:
            return True

    return False


def _get_final_stage_alias(src: str) -> str | None:
    """Return the AS alias of the last FROM line, or None."""
    last_alias: str | None = None
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _FROM_RE.match(stripped)
        if m:
            last_alias = m.group(1)  # may be None if no AS clause
    return last_alias


def _get_all_stage_aliases(src: str) -> list[str]:
    """Return all AS aliases from all FROM lines."""
    aliases: list[str] = []
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _FROM_RE.match(stripped)
        if m and m.group(1):
            aliases.append(m.group(1))
    return aliases


def _dockerfile_has_sde_in_final_stage(path: Path) -> bool:
    """Return True if the FINAL stage of the Dockerfile declares SOURCE_DATE_EPOCH.

    We find the byte offset of the last FROM line and scan only from that
    point onward for ARG/ENV/--build-arg SOURCE_DATE_EPOCH.
    """
    try:
        src = path.read_text(errors="replace")
    except OSError:
        return False

    lines = src.splitlines()
    last_from_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _FROM_RE.match(stripped):
            last_from_idx = i

    if last_from_idx == -1:
        return False

    # Check lines from the last FROM onward
    for line in lines[last_from_idx:]:
        if _ARG_SDE_RE.search(line) or _ENV_SDE_RE.search(line) or _BUILDARG_SDE_RE.search(line):
            return True
    return False


def _workflow_passes_sde(path: Path) -> bool:
    """Return True if the workflow file passes SOURCE_DATE_EPOCH to a docker build step.

    Strategy (YAML-free, regex-based to stay lightweight):
    1. Parse the file into "step blocks" by splitting on leading `- ` lines.
    2. For each block that contains a docker build reference, check if it
       also mentions SOURCE_DATE_EPOCH (in build-args: or in an env: block).
    3. Additionally, if any step exports SOURCE_DATE_EPOCH as an env var,
       treat the whole workflow as satisfying the requirement (it will be
       available in subsequent steps).
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False

    # Fast path: if SOURCE_DATE_EPOCH not mentioned at all, skip
    if "SOURCE_DATE_EPOCH" not in text:
        return False

    # Check for global env export (e.g., `run: echo "SOURCE_DATE_EPOCH=..." >> $GITHUB_ENV`)
    if _EXPORT_SDE_RE.search(text):
        return True

    # Check step blocks: a step with docker build that also has SOURCE_DATE_EPOCH
    # Split into loose "step" chunks by finding lines that start with `      - ` or `    - `
    # (YAML step list item). We look for proximity of docker-build keyword and SDE.
    step_blocks = _split_into_step_blocks(text)
    for block in step_blocks:
        if _DOCKER_BUILD_RE.search(block) and _BUILDARGS_LINE_RE.search(block):
            return True

    return False


def _split_into_step_blocks(text: str) -> list[str]:
    """Split workflow text into rough step-level blocks.

    Each block starts at a line matching ``^\\s+-\\s+`` (YAML list item).
    This is intentionally coarse — precise YAML parsing isn't needed.
    """
    import re as _re

    step_start = _re.compile(r"^\s+-\s+", re.MULTILINE)
    lines = text.splitlines(keepends=True)
    blocks: list[str] = []
    current: list[str] = []

    for line in lines:
        if step_start.match(line) and current:
            blocks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("".join(current))
    return blocks


# ── Standalone execution ──────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = ReproducibleBuildMarkersValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
