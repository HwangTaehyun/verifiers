"""V37: Go Test Race + Coverage Gate validator.

Enforces that all ``go test`` invocations in CI workflow files include the
``-race`` flag, and that workflow-level ``go test`` steps have a coverage
gate (``-coverprofile`` + upload step).

Rules:
  - V37-CI-NO-RACE          — ``go test`` lacks ``-race`` flag (error)
  - V37-CI-NO-COVERAGE-GATE — ``go test`` runs but no coverage gate detected (warning)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402
from lib.workflow_loader import walk_workflow_paths  # noqa: E402

# Step uses that satisfy the coverage gate requirement
_COVERAGE_UPLOAD_USES = (
    "actions/upload-artifact",
    "codecov/codecov-action",
)


class GoTestRaceCoverageValidator(BaseValidator):
    """V37: Go Test Race + Coverage Gate."""

    id = "V37-go-test-race-coverage"
    name = "Go Test Race + Coverage Gate"
    file_patterns: list[str] = [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "Makefile",
        "**/justfile",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: scan the single file that was just edited."""
        path = Path(file_path)
        if not path.is_file():
            return []
        name = path.name
        if name in ("Makefile",) or name.endswith(".mk"):
            return list(self._scan_lines_race_only(path))
        if name == "justfile":
            return list(self._scan_lines_race_only(path))
        if path.suffix in (".yml", ".yaml"):
            return self._check_workflow(path)
        return []

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: walk all workflow + Makefile + justfile files."""
        findings: list[Finding] = []
        findings.extend(self._check_workflows(ctx))
        findings.extend(self._check_makefile(ctx))
        findings.extend(self._check_justfile(ctx))
        return findings

    # ── Workflow scanning ────────────────────────────────────────────────

    def _check_workflows(self, ctx: ProjectContext) -> list[Finding]:
        """Phase60: dir walker via lib.workflow_loader.walk_workflow_paths."""
        findings: list[Finding] = []
        for wf_file in walk_workflow_paths(ctx.project_root):
            findings.extend(self._check_workflow(wf_file))
        return findings

    def _check_workflow(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
            data = yaml.safe_load(src)
        except (OSError, yaml.YAMLError):
            return []

        if not data or "jobs" not in data:
            return []

        # Build a line-number index: line_no -> line text (1-based)
        lines = src.splitlines()

        findings: list[Finding] = []
        for _job_name, job_spec in data["jobs"].items():
            if not isinstance(job_spec, dict) or "steps" not in job_spec:
                continue
            findings.extend(self._check_job_steps(file_path, job_spec["steps"], lines))
        return findings

    def _check_job_steps(
        self,
        file_path: Path,
        steps: list,
        src_lines: list[str],
    ) -> list[Finding]:
        findings: list[Finding] = []

        # Collect all uses: values in this job for coverage gate detection
        all_uses = set()
        for step in steps:
            if isinstance(step, dict) and "uses" in step:
                all_uses.add(str(step["uses"]))

        job_has_coverage_upload = any(any(u in uses_val for u in _COVERAGE_UPLOAD_USES) for uses_val in all_uses)

        for step in steps:
            if not isinstance(step, dict) or "run" not in step:
                continue

            run_cmd: str = step["run"]

            # Find lines within the run block that contain `go test`
            for raw_line in run_cmd.splitlines():
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "go test" not in stripped:
                    continue

                # Locate this line in the source for an accurate line number
                line_no = self._find_line_no(src_lines, stripped)

                if "-race" not in stripped:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(file_path),
                            line=line_no,
                            rule="V37-CI-NO-RACE",
                            message=(
                                "`go test` invocation lacks -race flag. Concurrent code "
                                "(matchers, workers, invoice number generators) won't be "
                                "checked for data races."
                            ),
                            fix=(
                                "Add -race to the go test command: `go test -race ./...`. "
                                "CI runtime overhead is ~10-20% but catches entire classes "
                                "of concurrency bugs at PR time."
                            ),
                        )
                    )

                # Coverage gate: only warn when -race IS present
                # (no point double-reporting when already erroring on -race)
                has_coverprofile = "-coverprofile" in stripped
                if not has_coverprofile and not job_has_coverage_upload:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(file_path),
                            line=line_no,
                            rule="V37-CI-NO-COVERAGE-GATE",
                            message=("`go test` runs but no -coverprofile flag and no Codecov/upload-artifact step."),
                            fix=(
                                "Add -coverprofile=coverage.out and either upload via "
                                "`codecov/codecov-action@<sha>` or `actions/upload-artifact`."
                            ),
                        )
                    )

        return findings

    # ── Makefile / justfile scanning (race only) ─────────────────────────

    def _check_makefile(self, ctx: ProjectContext) -> list[Finding]:
        makefile = Path(ctx.project_root) / "Makefile"
        if not makefile.is_file():
            return []
        return list(self._scan_lines_race_only(makefile))

    def _check_justfile(self, ctx: ProjectContext) -> list[Finding]:
        justfile = Path(ctx.project_root) / "justfile"
        if not justfile.is_file():
            return []
        return list(self._scan_lines_race_only(justfile))

    def _scan_lines_race_only(self, file_path: Path):
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return
        for line_no, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "go test" not in line:
                continue
            if "-race" not in line:
                yield Finding(
                    severity="error",
                    file=str(file_path),
                    line=line_no,
                    rule="V37-CI-NO-RACE",
                    message=(
                        "`go test` invocation lacks -race flag. Concurrent code "
                        "(matchers, workers, invoice number generators) won't be "
                        "checked for data races."
                    ),
                    fix=(
                        "Add -race to the go test command: `go test -race ./...`. "
                        "CI runtime overhead is ~10-20% but catches entire classes "
                        "of concurrency bugs at PR time."
                    ),
                )

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _find_line_no(src_lines: list[str], needle: str) -> int | None:
        """Return the 1-based line number of the first line containing needle."""
        for i, line in enumerate(src_lines, 1):
            if needle in line:
                return i
        return None


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoTestRaceCoverageValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
