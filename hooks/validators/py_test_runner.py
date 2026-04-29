"""V11: Python targeted test runner — run related pytest tests for modified .py files.

PostToolUse checks:
  V11-TEST-FAIL: Test failures for the modified file
  V11-NO-TEST: No matching test file found (warning)
  V11-REPEATED-FAIL: Same test failed 3+ consecutive times (warning)
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

from hooks.validators.base import BaseValidator, Finding, ValidationResult, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# Failure tracker file path (shared with V09/V10)
FAILURE_TRACKER = Path(__file__).parent.parent.parent / "logs" / "test-failure-tracker.json"

# Default; the live value comes from
# ctx.config.thresholds.test_runner.repeated_failure_count (P1-3 wiring).
REPEATED_FAIL_THRESHOLD = 3

# Directories to exclude
EXCLUDE_DIRS = {"__pycache__", ".venv", "venv", "env", ".tox", "dist", "build", "egg-info", "generated"}

# Files to skip test existence checks for
SKIP_FILES = {
    "__init__.py",
    "conftest.py",
    "setup.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "celery.py",
    "gunicorn.conf.py",
}


class PyTestRunnerValidator(BaseValidator):
    """V11: Python Test Runner — targeted pytest execution."""

    id = "V11-py-test-runner"
    name = "Python Test Runner"
    file_patterns: list[str] = [
        "**/*.py",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # Find the Python project root (pyproject.toml, setup.py, etc.)
        py_root = self._find_python_root(ctx)
        if not py_root:
            return ValidationResult(validator_id=self.id, findings=findings)

        threshold = ctx.config.thresholds.test_runner.repeated_failure_count

        if mode == "post_tool_use" and file_path and file_path.endswith(".py"):
            # Skip excluded directories
            if self._is_excluded(file_path):
                return ValidationResult(validator_id=self.id, findings=findings)

            if self._is_test_file(file_path):
                # Test file modified — run it directly
                findings.extend(self._run_test_file(py_root, file_path, threshold))
            else:
                test_file = self._resolve_test_file(py_root, file_path)
                if test_file:
                    findings.extend(self._run_test_file(py_root, test_file, threshold))
                else:
                    findings.extend(self._check_test_exists(file_path))

        # Stop mode: not needed — comprehensive test run can be done separately
        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Python project detection ────────────────────────────────────────

    def _find_python_root(self, ctx: ProjectContext) -> Path | None:
        """Find the Python project root by looking for pyproject.toml, setup.py, etc."""
        # Check project root first
        root = ctx.project_root
        indicators = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"]

        for indicator in indicators:
            if (root / indicator).exists():
                return root

        # Check server_dir
        if ctx.server_dir:
            for indicator in indicators:
                if (ctx.server_dir / indicator).exists():
                    return ctx.server_dir

        return None

    # ── Test file detection ─────────────────────────────────────────────

    def _is_test_file(self, file_path: str) -> bool:
        """Check if this is a test file."""
        name = Path(file_path).name
        if name.startswith("test_") and name.endswith(".py"):
            return True
        if name.endswith("_test.py"):
            return True
        if "tests/" in file_path or "test/" in file_path:
            if name.endswith(".py") and name != "__init__.py" and name != "conftest.py":
                return True
        return False

    def _is_excluded(self, file_path: str) -> bool:
        """Check if a file is in an excluded directory."""
        parts = Path(file_path).parts
        return any(part in EXCLUDE_DIRS for part in parts)

    # ── Test file resolution ────────────────────────────────────────────

    def _resolve_test_file(self, py_root: Path, source_path: str) -> str | None:
        """Resolve source file to its corresponding test file.

        Search order:
        1. Same directory: handler.py → test_handler.py
        2. tests/ directory: tests/test_handler.py
        3. tests/{subdir}/: tests/{subdir}/test_handler.py
        """
        fp = Path(source_path)
        stem = fp.stem

        # Determine absolute parent
        if fp.is_absolute():
            abs_parent = fp.parent
            abs_fp = fp
        else:
            abs_fp = py_root / source_path
            abs_parent = abs_fp.parent

        # 1. Same directory: test_{name}.py
        candidate = abs_parent / f"test_{stem}.py"
        if candidate.exists():
            return str(candidate)

        # Also check {name}_test.py
        candidate = abs_parent / f"{stem}_test.py"
        if candidate.exists():
            return str(candidate)

        # 2. tests/ directory at project root
        tests_dir = py_root / "tests"
        if tests_dir.exists():
            candidate = tests_dir / f"test_{stem}.py"
            if candidate.exists():
                return str(candidate)

            # 3. tests/{subdir}/ — mirror the source structure
            try:
                rel = abs_fp.parent.relative_to(py_root)
                candidate = tests_dir / rel / f"test_{stem}.py"
                if candidate.exists():
                    return str(candidate)
            except ValueError:
                pass

            # Search recursively in tests/ for test_{stem}.py
            for candidate in tests_dir.rglob(f"test_{stem}.py"):
                return str(candidate)

        # 4. test/ directory (alternative naming)
        test_dir = py_root / "test"
        if test_dir.exists():
            candidate = test_dir / f"test_{stem}.py"
            if candidate.exists():
                return str(candidate)

        return None

    # ── Test execution ──────────────────────────────────────────────────

    def _run_test_file(
        self,
        py_root: Path,
        test_file: str,
        repeated_fail_threshold: int = REPEATED_FAIL_THRESHOLD,
    ) -> list[Finding]:
        """Run pytest for a specific test file and parse results.

        ``repeated_fail_threshold`` controls when V11-REPEATED-FAIL fires;
        defaults to the module constant for back-compat with any external
        caller, while ``validate()`` always supplies the config value.
        """
        findings: list[Finding] = []

        # Determine how to run pytest
        cmd = self._build_pytest_cmd(py_root, test_file)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(py_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            # Parse failed test names
            failed_tests = self._parse_test_failures(result.stdout + result.stderr)
            test_file_name = Path(test_file).name

            # Track failures
            for test_name in failed_tests:
                count = self._track_failure(test_name, passed=False)
                if count >= repeated_fail_threshold:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=test_file,
                            rule="V11-REPEATED-FAIL",
                            message=(
                                f"'{test_name}' has failed {count} consecutive times. "
                                "This may indicate the PRD/test expectations need updating."
                            ),
                            fix=(
                                "Consider: /tdd-update to revise tests based on updated requirements. "
                                "Or confirm the test is correct and continue debugging."
                            ),
                        )
                    )

            findings.append(
                Finding(
                    severity="error",
                    file=test_file,
                    rule="V11-TEST-FAIL",
                    message=(
                        f"Tests failed in {test_file_name}: {', '.join(failed_tests) if failed_tests else 'see output'}"
                    ),
                    fix=(f"Fix failing tests. Run '{' '.join(cmd)}' for details"),
                )
            )
        else:
            # Tests passed — reset failure counters
            passed_tests = self._parse_test_passes(result.stdout + result.stderr)
            for test_name in passed_tests:
                self._track_failure(test_name, passed=True)

        return findings

    def _build_pytest_cmd(self, py_root: Path, test_file: str) -> list[str]:
        """Build the pytest command based on project setup."""
        # Check if uv is available (pyproject.toml with uv)
        if (py_root / "pyproject.toml").exists():
            return ["uv", "run", "pytest", test_file, "-v", "--tb=short", "-q"]

        # Fallback to python -m pytest
        return ["python", "-m", "pytest", test_file, "-v", "--tb=short", "-q"]

    def _parse_test_failures(self, output: str) -> list[str]:
        """Parse pytest output to extract failed test names."""
        failed: list[str] = []

        # Pattern: FAILED tests/test_foo.py::test_bar - AssertionError
        for match in re.finditer(r"FAILED\s+(\S+)", output):
            name = match.group(1).strip()
            if name:
                failed.append(name)

        return failed

    def _parse_test_passes(self, output: str) -> list[str]:
        """Parse pytest output to extract passed test names."""
        passed: list[str] = []

        # Pattern: PASSED tests/test_foo.py::test_bar
        for match in re.finditer(r"PASSED\s+(\S+)", output):
            name = match.group(1).strip()
            if name:
                passed.append(name)

        return passed

    # ── Test existence check ────────────────────────────────────────────

    def _check_test_exists(self, file_path: str) -> list[Finding]:
        """Warn if no test file exists for the source file."""
        findings: list[Finding] = []

        name = Path(file_path).name
        if name in SKIP_FILES or name.startswith("_"):
            return findings

        stem = Path(file_path).stem

        findings.append(
            Finding(
                severity="warning",
                file=file_path,
                rule="V11-NO-TEST",
                message="No test file found for this source file.",
                fix=(f"Create a test file (e.g., test_{stem}.py) in the same directory or tests/ directory."),
            )
        )

        return findings

    # ── Failure tracking ────────────────────────────────────────────────

    def _track_failure(self, test_name: str, passed: bool) -> int:
        """Track consecutive test failures. Returns current count."""
        tracker = self._load_tracker()

        if passed:
            tracker.pop(test_name, None)
            self._save_tracker(tracker)
            return 0
        else:
            tracker[test_name] = tracker.get(test_name, 0) + 1
            self._save_tracker(tracker)
            return tracker[test_name]

    def _load_tracker(self) -> dict[str, int]:
        """Load the failure tracker from disk."""
        try:
            if FAILURE_TRACKER.exists():
                return json.loads(FAILURE_TRACKER.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def _save_tracker(self, tracker: dict[str, int]) -> None:
        """Save the failure tracker to disk."""
        try:
            FAILURE_TRACKER.parent.mkdir(parents=True, exist_ok=True)
            FAILURE_TRACKER.write_text(json.dumps(tracker, ensure_ascii=False))
        except OSError:
            pass


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
    validator = PyTestRunnerValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
