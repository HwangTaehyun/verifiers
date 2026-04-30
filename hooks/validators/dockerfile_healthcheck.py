"""V45: Dockerfile HEALTHCHECK presence validator.

Checks:
  V45-DOCKERFILE-NO-HEALTHCHECK: Final stage exposes port(s) but has no
    HEALTHCHECK instruction (or has HEALTHCHECK NONE which disables it).
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

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext


class DockerfileHealthcheckValidator(BaseValidator):
    """V45: Dockerfile HEALTHCHECK Presence.

    Checks:
      V45-DOCKERFILE-NO-HEALTHCHECK: Final stage has EXPOSE but no HEALTHCHECK
        (or HEALTHCHECK NONE which explicitly disables health checking).

    Workers and background jobs that have no EXPOSE are exempt.
    """

    id = "V45-dockerfile-healthcheck"
    name = "Dockerfile HEALTHCHECK Presence"
    file_patterns: list[str] = [
        "**/Dockerfile*",
        "**/*.Dockerfile",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 per-edit check: scan a single Dockerfile."""
        path = Path(file_path)
        if not self._is_dockerfile(path):
            return []
        return self._check_healthcheck(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 project-wide sweep: walk all Dockerfiles."""
        findings: list[Finding] = []

        dockerfiles = list(ctx.project_root.glob("**/Dockerfile*"))
        dockerfiles.extend(ctx.project_root.glob("**/*.Dockerfile"))

        # Deduplicate (glob may return overlapping results)
        seen: set[Path] = set()
        for dockerfile in dockerfiles:
            if dockerfile in seen:
                continue
            seen.add(dockerfile)

            if not self._is_dockerfile(dockerfile):
                continue
            if ctx.is_excluded(str(dockerfile)):
                continue

            findings.extend(self._check_healthcheck(dockerfile))

        return findings

    # ── Helpers ──────────────────────────────────────────────────────────

    def _is_dockerfile(self, path: Path) -> bool:
        """True if the path looks like a Dockerfile."""
        name = path.name
        return name == "Dockerfile" or name.endswith(".Dockerfile") or name.startswith("Dockerfile.")

    def _extract_final_stage(self, lines: list[str]) -> tuple[list[str], int]:
        """Return (final_stage_lines, 0-based index of last FROM line)."""
        last_from = -1
        for i, line in enumerate(lines):
            if line.strip().upper().startswith("FROM ") or line.strip().upper() == "FROM":
                last_from = i
        if last_from < 0:
            return [], -1
        return lines[last_from:], last_from

    def _check_healthcheck(self, file_path: Path) -> list[Finding]:
        """Check the final stage for EXPOSE + HEALTHCHECK."""
        try:
            text = file_path.read_text()
        except OSError:
            return []

        lines = text.splitlines()
        final_stage_lines, last_from_idx = self._extract_final_stage(lines)

        if not final_stage_lines:
            return []

        # Check for EXPOSE in the final stage (skipping comments)
        expose_ports: list[str] = []
        has_healthcheck = False

        for line in final_stage_lines:
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("#"):
                continue

            # Detect EXPOSE instruction
            expose_match = re.match(r"^EXPOSE\s+(.+)", stripped, re.IGNORECASE)
            if expose_match:
                ports_str = expose_match.group(1).strip()
                # Collect port values (may be multiple: EXPOSE 80 443)
                for port in ports_str.split():
                    expose_ports.append(port)

            # Detect HEALTHCHECK instruction
            # HEALTHCHECK NONE counts as "no real health check" (explicitly disabled)
            if re.match(r"^HEALTHCHECK\s+", stripped, re.IGNORECASE):
                # Treat HEALTHCHECK NONE as absent — it disables health checking
                if re.match(r"^HEALTHCHECK\s+NONE\s*$", stripped, re.IGNORECASE):
                    has_healthcheck = False
                else:
                    has_healthcheck = True

        # No EXPOSE → worker-style Dockerfile → exempt
        if not expose_ports:
            return []

        # Has EXPOSE but also has a real HEALTHCHECK → pass
        if has_healthcheck:
            return []

        ports = " ".join(expose_ports)
        return [
            Finding(
                severity="warning",
                file=str(file_path),
                rule="V45-DOCKERFILE-NO-HEALTHCHECK",
                message=(
                    f"Dockerfile final stage exposes port(s) {ports} but has no HEALTHCHECK instruction. "
                    f"Docker / Compose / k8s can't auto-restart unhealthy containers."
                ),
                fix=(
                    "Add to the prod stage:\n"
                    "    HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\\n"
                    "        CMD curl -f http://localhost:<PORT>/health || exit 1\n"
                    "Pair with V50 (/livez vs /readyz split) for k8s probe semantics."
                ),
                line=last_from_idx + 1,
            )
        ]


# ── Standalone execution ─────────────────────────────────────────────────────


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
    validator = DockerfileHealthcheckValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
