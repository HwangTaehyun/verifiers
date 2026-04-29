"""V06: Go code quality validator — go vet, gofmt, go build, golangci-lint, go test.

PostToolUse checks (fast, <5s):
  V06-GO-VET: Suspicious code patterns detected by go vet
  V06-GOFMT: File not properly formatted by gofmt
  V06-BUILD-FAIL: Compilation error

Stop checks (slow, comprehensive):
  V06-LINT-*: golangci-lint findings (50+ linters)
  V06-TEST-FAIL: Test failures
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext


class GoQualityValidator(BaseValidator):
    """V06: Go Quality Validator."""

    id = "V06-go-quality"
    name = "Go Quality Validator"
    file_patterns: list[str] = [
        "**/*.go",
        "**/go.mod",
        "**/go.sum",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-edit Go vet + gofmt + go build (Tier 2)."""
        if not self._has_go_project(ctx):
            return []
        findings: list[Finding] = []
        findings.extend(self._check_go_vet(ctx))
        if file_path.endswith(".go"):
            findings.extend(self._check_gofmt(ctx, file_path))
        findings.extend(self._check_go_build(ctx))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide Go vet + build + golangci-lint + tests (Tier 3)."""
        if not self._has_go_project(ctx):
            return []
        findings: list[Finding] = []
        findings.extend(self._check_go_vet(ctx))
        findings.extend(self._check_go_build(ctx))
        findings.extend(self._check_golangci_lint(ctx))
        findings.extend(self._check_go_test(ctx))
        return findings

    def _has_go_project(self, ctx: ProjectContext) -> bool:
        if not ctx.server_dir or not ctx.server_dir.exists():
            return False
        has_go_files = any(ctx.server_dir.rglob("*.go"))
        has_go_mod = (ctx.server_dir / "go.mod").exists()
        return has_go_files or has_go_mod

    # ── Check 1: go vet ──────────────────────────────────────────────────

    def _check_go_vet(self, ctx: ProjectContext) -> list[Finding]:
        """Run go vet to detect suspicious code patterns."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                ["go", "vet", "./..."],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            for line in result.stderr.strip().split("\n"):
                match = re.search(r"(.+\.go):(\d+):\d+: (.+)", line)
                if match:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(ctx.server_dir / match.group(1)),
                            rule="V06-GO-VET",
                            message=match.group(3),
                            fix=(
                                f"Fix the issue reported by 'go vet' at "
                                f"{match.group(1)}:{match.group(2)}: {match.group(3)}"
                            ),
                            line=int(match.group(2)),
                        )
                    )

        return findings

    # ── Check 2: gofmt ───────────────────────────────────────────────────

    def _check_gofmt(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Check if file is properly formatted by gofmt."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                ["gofmt", "-l", file_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.stdout.strip():
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V06-GOFMT",
                    message="File is not properly formatted by gofmt",
                    fix=f"Run 'gofmt -w {file_path}' to auto-format this file",
                )
            )

        return findings

    # ── Check 3: go build ────────────────────────────────────────────────

    def _check_go_build(self, ctx: ProjectContext) -> list[Finding]:
        """Verify code compiles without errors."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                ["go", "build", "./..."],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            for line in result.stderr.strip().split("\n"):
                match = re.search(r"(.+\.go):(\d+):\d+: (.+)", line)
                if match:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(ctx.server_dir / match.group(1)),
                            rule="V06-BUILD-FAIL",
                            message=match.group(3),
                            fix=f"Fix compilation error: {match.group(3)}",
                            line=int(match.group(2)),
                        )
                    )

        return findings

    # ── Check 4: golangci-lint (Stop mode) ───────────────────────────────

    def _check_golangci_lint(self, ctx: ProjectContext) -> list[Finding]:
        """Comprehensive code quality analysis with golangci-lint."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                [
                    "golangci-lint",
                    "run",
                    "--timeout",
                    "60s",
                    "--out-format",
                    "json",
                ],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=90,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return findings

            for issue in data.get("Issues") or []:
                pos = issue.get("Pos", {})
                from_linter = issue.get("FromLinter", "unknown")
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(ctx.server_dir / pos.get("Filename", "")),
                        rule=f"V06-LINT-{from_linter}",
                        message=issue.get("Text", ""),
                        fix=(f"Fix lint issue from {from_linter}: {issue.get('Text', '')}"),
                        line=pos.get("Line"),
                    )
                )

        return findings

    # ── Check 5: go test (Stop mode) ─────────────────────────────────────

    def _check_go_test(self, ctx: ProjectContext) -> list[Finding]:
        """Run tests to verify correctness."""
        findings: list[Finding] = []

        # Use Makefile test target if available
        makefile = ctx.server_dir / "Makefile"
        if makefile.exists():
            try:
                if "test:" in makefile.read_text():
                    cmd = ["make", "test"]
                else:
                    cmd = ["go", "test", "-race", "-count=1", "-timeout=120s", "./..."]
            except OSError:
                cmd = ["go", "test", "-race", "-count=1", "-timeout=120s", "./..."]
        else:
            cmd = ["go", "test", "-race", "-count=1", "-timeout=120s", "./..."]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            failed_tests = re.findall(r"--- FAIL: (\S+)", result.stdout)
            test_names = ", ".join(failed_tests) if failed_tests else "see output"
            run_target = failed_tests[0] if failed_tests else ""

            findings.append(
                Finding(
                    severity="error",
                    file=str(ctx.server_dir),
                    rule="V06-TEST-FAIL",
                    message=f"Tests failed: {test_names}",
                    fix=(f"Fix failing tests. Run 'cd {ctx.server_dir} && go test -v -run {run_target}' for details"),
                )
            )

        return findings


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
    validator = GoQualityValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
