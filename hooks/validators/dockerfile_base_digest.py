"""V44: Dockerfile Base-Image Digest Pinning validator.

Enforces that all ``FROM`` lines in Dockerfiles reference a base image
pinned with an ``@sha256:<64-hex-chars>`` digest instead of a floating
tag (``FROM golang:1.25-bookworm``, ``FROM nginx:latest``, etc.).

Rules:
  - V44-FROM-NO-DIGEST — a FROM line uses a tag-only ref without @sha256 digest (warning)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Match FROM lines, capturing the image ref (including optional --platform flag).
# Group 1: the full image ref (may include @sha256:... or not).
_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?([^\s#]+)(?:\s+(?:AS|as)\s+\S+)?",
)

# A valid sha256 digest pin embedded in the image ref.
_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}")


class DockerfileBaseDigestValidator(BaseValidator):
    """V44: Dockerfile Base-Image Digest Pinning."""

    id = "V44-dockerfile-base-digest-pin"
    name = "Dockerfile Base-Image Digest Pinning"
    file_patterns: list[str] = [
        "**/Dockerfile*",
        "**/*.Dockerfile",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: scan the single Dockerfile that was just edited."""
        path = Path(file_path)
        if not path.is_file():
            return []
        return self._check_dockerfile(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: walk all Dockerfile* and *.Dockerfile under project_root."""
        root = Path(ctx.project_root)
        findings: list[Finding] = []
        seen: set[Path] = set()

        for pattern in ("**/Dockerfile*", "**/*.Dockerfile"):
            for df in sorted(root.glob(pattern)):
                if df.is_file() and df not in seen:
                    seen.add(df)
                    findings.extend(self._check_dockerfile(df))
        return findings

    # ── Internals ──────────────────────────────────────────────────────

    def _check_dockerfile(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []
        return list(self._scan_lines(src, file_path))

    def _scan_lines(self, src: str, file_path: Path):
        # Collect stage names defined by AS aliases so we can skip
        # multi-stage references (e.g. `FROM build AS prod` where `build`
        # is a prior stage name).
        stage_names: set[str] = set()

        for line_no, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()

            # Skip blank lines and comment lines
            if not stripped or stripped.startswith("#"):
                continue

            m = _FROM_RE.match(stripped)
            if not m:
                continue

            ref = m.group(1)

            # Record AS alias for this stage so later FROM lines can skip it
            alias_match = re.search(r"\s+(?:AS|as)\s+(\S+)", stripped)
            if alias_match:
                stage_names.add(alias_match.group(1))

            # Skip variable substitutions (e.g. FROM ${BASE_IMAGE})
            if ref.startswith("$"):
                continue

            # Skip multi-stage references — the ref is a previous stage name
            # (heuristic: no "/" or ":" in the ref and it matches a known stage).
            if "/" not in ref and ":" not in ref and "@" not in ref:
                if ref in stage_names or _is_stage_ref(ref, src):
                    continue

            # Check for digest pin
            if not _DIGEST_RE.search(ref):
                yield Finding(
                    severity="warning",
                    file=str(file_path),
                    line=line_no,
                    rule="V44-FROM-NO-DIGEST",
                    message=(
                        f"FROM line uses tag-only ref `{ref}` without `@sha256:<digest>`. "
                        f"Registry push can silently change next build's bits."
                    ),
                    fix=(
                        f"Append digest: `FROM {ref}@sha256:<digest>`. "
                        f"Use `docker buildx imagetools inspect {ref}` to obtain the current digest. "
                        "Renovate or Dependabot's `docker` ecosystem keeps digests fresh."
                    ),
                )


def _is_stage_ref(ref: str, src: str) -> bool:
    """Return True if ``ref`` appears as an AS alias in any FROM line of src."""
    pattern = re.compile(
        r"^\s*FROM\s+\S+\s+(?:AS|as)\s+" + re.escape(ref) + r"\s*(?:#|$)",
        re.MULTILINE | re.IGNORECASE,
    )
    return bool(pattern.search(src))


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = DockerfileBaseDigestValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
