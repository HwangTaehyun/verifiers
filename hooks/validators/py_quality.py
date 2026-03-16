"""V19: Python code quality validator — ruff check, ruff format, pytest.

Mirrors V06 (Go Quality) for Python projects:

PostToolUse checks (fast, <5s):
  V19-RUFF-CHECK: Lint errors detected by ruff check
  V19-RUFF-FORMAT: File not properly formatted by ruff format

Stop checks (slow, comprehensive):
  V19-RUFF-ALL: Full-project ruff check with all rules
  V19-TEST-FAIL: Test failures via pytest
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
    """V19: Python Quality Validator — ruff + pytest."""

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
            findings.extend(self._check_pytest(py_root))

        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Python project detection ──────────────────────────────────────

    def _find_python_root(self, ctx: ProjectContext) -> Path | None:
        """Find Python project root by pyproject.toml, setup.py, etc."""
        root = ctx.project_root
        indicators = [
            "pyproject.toml", "setup.py", "setup.cfg",
            "requirements.txt", "Pipfile",
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
                match = re.match(
                    r"(.+?):(\d+):(\d+): (\S+) (.+)", line
                )
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
                match = re.match(
                    r"(.+?):(\d+):(\d+): (\S+) (.+)", line
                )
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

    def _find_python_bin(self, py_root: Path) -> str:
        """Find the best python binary (prefer .venv)."""
        venv_python = py_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        return "python"

    def _load_dotenv(self, py_root: Path) -> dict[str, str]:
        """Load .env file and merge with current environment."""
        import os
        env = os.environ.copy()
        dotenv_path = py_root / ".env"
        if dotenv_path.exists():
            for line in dotenv_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip().strip("\"'")
        return env

    def _check_pytest(self, py_root: Path) -> list[Finding]:
        """Run pytest to verify all tests pass."""
        findings: list[Finding] = []

        python_bin = self._find_python_bin(py_root)
        cmd = [python_bin, "-m", "pytest", "-x", "-q", "--tb=no"]
        env = self._load_dotenv(py_root)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(py_root),
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            output = result.stdout + result.stderr

            # Skip if all tests passed (exit code from warnings/plugins/deprecations)
            if re.search(r"\d+ passed", output) and not re.search(r"\d+ failed", output):
                return findings

            # Skip if no actual test failure detected (import warnings, etc.)
            if "FAILED" not in output and "ERROR" not in output and "failed" not in output:
                return findings

            # Parse failed test count
            failed_match = re.search(r"(\d+) failed", output)
            failed_count = failed_match.group(1) if failed_match else "unknown"

            # Parse specific failed test names
            failed_tests = re.findall(r"FAILED\s+(\S+)", output)
            test_names = ", ".join(failed_tests[:5]) if failed_tests else "see output"

            findings.append(
                Finding(
                    severity="error",
                    file=str(py_root),
                    rule="V19-TEST-FAIL",
                    message=f"pytest: {failed_count} test(s) failed: {test_names}",
                    fix=f"Fix failing tests. Run 'cd {py_root} && python -m pytest -x -v' for details",
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
