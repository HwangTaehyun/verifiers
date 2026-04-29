"""V21: Python pytest runner — runs the project's pytest suite at Stop.

Split from V19 (PyQualityValidator) in Phase 28 so the parallel runner
sees pytest as an independent unit. V19 now handles only ruff (lint /
format / project-wide); V21 owns the test execution path.

Stop-mode-only:
  V21-TEST-FAIL: pytest exited with at least one failed test.

The ``stop.run_pytest`` config key (see ``lib.config_loader.StopConfig``)
controls whether pytest runs:

  "always" — run on every Stop hook invocation (legacy V19 behavior).
  "never"  — skip in Stop; CI is the safety net.
  "smart"  — run only when the working tree has uncommitted .py /
             pyproject.toml changes this turn (default). Heuristic
             relies on ``git diff --name-only HEAD``.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext


# ── Smart-mode trigger ──────────────────────────────────────────────────


_SMART_TRIGGER_SUFFIXES = (".py",)
_SMART_TRIGGER_NAMES = ("pyproject.toml",)


def has_uncommitted_python_changes(project_root: Path) -> bool:
    """Smart-mode oracle: did this turn touch Python sources?

    Returns True (= run pytest) when ``git diff --name-only HEAD``
    reports any working-tree change to ``*.py`` or ``pyproject.toml``.
    Falls open (returns True) on git errors so a misconfigured repo
    never silently suppresses test runs.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return True  # fail open — never silently skip pytest

    if result.returncode != 0:
        return True  # not a git repo, or detached HEAD with no commits — fail open

    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        if name.endswith(_SMART_TRIGGER_SUFFIXES):
            return True
        # `pyproject.toml` may appear at root or nested
        if Path(name).name in _SMART_TRIGGER_NAMES:
            return True
    return False


# ── Validator ───────────────────────────────────────────────────────────


class PyPytestValidator(BaseValidator):
    """V21: pytest runner (Stop only, gated by stop.run_pytest config)."""

    id = "V21-pytest"
    name = "Python Pytest Runner"
    file_patterns: list[str] = [
        "**/*.py",
        "**/pyproject.toml",
    ]

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: Stop-mode pytest gate.

        V21 is Stop-only (validate_file stays the base no-op). The
        ``stop.run_pytest`` config selects always / never / smart.
        """
        run_mode = ctx.config.stop.run_pytest
        if run_mode == "never":
            return []

        py_root = self._find_python_root(ctx)
        if not py_root:
            return []

        if run_mode == "smart" and not has_uncommitted_python_changes(py_root):
            return []

        return self._check_pytest(py_root)

    # ── Python project detection (mirrors V19) ──────────────────────────

    def _find_python_root(self, ctx: ProjectContext) -> Path | None:
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

    def _find_python_bin(self, py_root: Path) -> str:
        venv_python = py_root / ".venv" / "bin" / "python"
        if venv_python.exists():
            return str(venv_python)
        return "python"

    def _load_dotenv(self, py_root: Path) -> dict[str, str]:
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

            if re.search(r"\d+ passed", output) and not re.search(r"\d+ failed", output):
                return findings

            if "FAILED" not in output and "ERROR" not in output and "failed" not in output:
                return findings

            failed_match = re.search(r"(\d+) failed", output)
            failed_count = failed_match.group(1) if failed_match else "unknown"

            failed_tests = re.findall(r"FAILED\s+(\S+)", output)
            test_names = ", ".join(failed_tests[:5]) if failed_tests else "see output"

            findings.append(
                Finding(
                    severity="error",
                    file=str(py_root),
                    rule="V21-TEST-FAIL",
                    message=f"pytest: {failed_count} test(s) failed: {test_names}",
                    fix=f"Fix failing tests. Run 'cd {py_root} && python -m pytest -x -v' for details",
                )
            )

        return findings


# ── Standalone execution ────────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = PyPytestValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
