"""V19: Python code quality validator — ruff check + ruff format.

Mirrors V06 (Go Quality) for Python projects, ruff-only after Phase28.
The pytest path moved to V21 (``py_pytest.py``) so the parallel runner
treats lint and test execution as independent units.

PostToolUse checks (fast, <5s):
  V19-RUFF-CHECK: Lint errors detected by ruff check
  V19-RUFF-FORMAT: File not properly formatted by ruff format

Stop checks (slow, comprehensive):
  V19-RUFF-ALL: Full-project ruff check with all rules
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext


class PyQualityValidator(BaseValidator):
    """V19: Python Quality Validator — ruff (lint / format / project-wide).

    The pytest path moved to V21 (``py_pytest.py``) in Phase28 so the
    parallel runner sees lint and test execution as independent units.
    """

    id = "V19-py-quality"
    name = "Python Quality Validator"
    file_patterns: list[str] = [
        "**/*.py",
        "**/pyproject.toml",
        "**/ruff.toml",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        py_root = self._find_python_root(ctx)
        if not py_root:
            return ValidationResult(validator_id=self.id, findings=findings)

        # Fast checks (PostToolUse) — per-file
        if file_path and file_path.endswith(".py"):
            findings.extend(self._check_ruff_lint(py_root, file_path))
            findings.extend(self._check_ruff_format(py_root, file_path))

        # Slow checks (Stop mode only) — full project
        if mode == "stop":
            findings.extend(self._check_ruff_all(py_root))

        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Python project detection ──────────────────────────────────────

    def _find_python_root(self, ctx: ProjectContext) -> Path | None:
        """Find Python project root by pyproject.toml, setup.py, etc."""
        root = ctx.project_root
        indicators = [
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "Pipfile",
        ]
        for ind in indicators:
            if (root / ind).exists():
                return root
        if ctx.server_dir:
            for ind in indicators:
                if (ctx.server_dir / ind).exists():
                    return ctx.server_dir
        return None

    # ── Tool resolution ─────────────────────────────────────────────

    def _find_ruff_bin(self, py_root: Path) -> str:
        """Find ruff binary (prefer .venv, then system)."""
        venv_ruff = py_root / ".venv" / "bin" / "ruff"
        if venv_ruff.exists():
            return str(venv_ruff)
        return "ruff"

    # ── Check 1: ruff check (per-file) ────────────────────────────────

    def _check_ruff_lint(self, py_root: Path, file_path: str) -> list[Finding]:
        """Run ruff check on a single file."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                [self._find_ruff_bin(py_root), "check", file_path, "--output-format", "text", "--no-fix"],
                cwd=str(py_root),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                match = re.match(r"(.+?):(\d+):(\d+): (\S+) (.+)", line)
                if match:
                    findings.append(
                        Finding(
                            severity="error",
                            file=match.group(1),
                            rule=f"V19-RUFF-{match.group(4)}",
                            message=match.group(5),
                            fix=f"Fix ruff error {match.group(4)}: {match.group(5)}",
                            line=int(match.group(2)),
                        )
                    )

        return findings

    # ── Check 2: ruff format (per-file) ───────────────────────────────

    def _check_ruff_format(self, py_root: Path, file_path: str) -> list[Finding]:
        """Check if file is properly formatted by ruff."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                [self._find_ruff_bin(py_root), "format", "--check", file_path],
                cwd=str(py_root),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V19-RUFF-FORMAT",
                    message="File is not properly formatted by ruff",
                    fix=f"Run 'ruff format {file_path}' to auto-format",
                )
            )

        return findings

    # ── Check 3: ruff check full project (Stop mode) ──────────────────

    def _check_ruff_all(self, py_root: Path) -> list[Finding]:
        """Run ruff check on the entire project."""
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                [self._find_ruff_bin(py_root), "check", ".", "--output-format", "text", "--no-fix"],
                cwd=str(py_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout.strip():
            count = 0
            for line in result.stdout.strip().split("\n"):
                match = re.match(r"(.+?):(\d+):(\d+): (\S+) (.+)", line)
                if match:
                    count += 1
                    # Cap at 20 findings to avoid overwhelming output
                    if count <= 20:
                        findings.append(
                            Finding(
                                severity="warning",
                                file=str(py_root / match.group(1)),
                                rule=f"V19-RUFF-{match.group(4)}",
                                message=match.group(5),
                                fix=f"Fix ruff warning {match.group(4)}: {match.group(5)}",
                                line=int(match.group(2)),
                            )
                        )

            if count > 20:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(py_root),
                        rule="V19-RUFF-SUMMARY",
                        message=f"{count} total ruff issues found ({count - 20} not shown)",
                        fix="Run 'ruff check .' to see all issues",
                    )
                )

        return findings

    # ── Check 4: pytest (Stop mode) ───────────────────────────────────


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
    validator = PyQualityValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
