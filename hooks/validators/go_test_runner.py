"""V09: Go targeted test runner — run tests for the specific package being modified.

PostToolUse checks:
  V09-TEST-FAIL: Test failures in the modified package
  V09-NO-TEST: No _test.go file exists for the modified package (warning)
  V09-REPEATED-FAIL: Same test failed 3+ consecutive times (warning)
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# Failure tracker file path
FAILURE_TRACKER = Path(__file__).parent.parent.parent / "logs" / "test-failure-tracker.json"

# Directories to exclude from test existence checks
EXCLUDE_DIRS = {"vendor", "testdata", "mock", "mocks", "generated", "gen", "third_party"}

# Default; the live value comes from
# ctx.config.thresholds.test_runner.repeated_failure_count (P1-3 wiring).
REPEATED_FAIL_THRESHOLD = 3


class GoTestRunnerValidator(BaseValidator):
    """V09: Go Test Runner — targeted package test execution."""

    id = "V09-go-test-runner"
    name = "Go Test Runner"
    file_patterns: list[str] = [
        "**/*.go",
        "**/go.mod",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-edit Go test resolution + execution (Tier 2).

        Stop mode is intentionally a no-op — V06 already runs ``go test ./...``.
        """
        if not ctx.server_dir or not ctx.server_dir.exists():
            return []
        if not file_path.endswith(".go"):
            return []

        threshold = ctx.config.thresholds.test_runner.repeated_failure_count
        findings: list[Finding] = []

        if file_path.endswith("_test.go"):
            pkg_dir = self._get_package_dir(ctx, file_path)
            if pkg_dir:
                findings.extend(self._run_package_tests(ctx, pkg_dir, file_path, threshold))
            return findings

        if self._is_excluded(file_path):
            return findings

        pkg_dir = self._resolve_test_package(ctx, file_path)
        if pkg_dir:
            findings.extend(self._run_package_tests(ctx, pkg_dir, file_path, threshold))
        else:
            findings.extend(self._check_test_exists(ctx, file_path))
        return findings

    # ── Package resolution ──────────────────────────────────────────────

    def _get_package_dir(self, ctx: ProjectContext, file_path: str) -> str | None:
        """Get the Go package directory relative to server_dir from a file path."""
        try:
            fp = Path(file_path)
            if fp.is_absolute():
                rel = fp.parent.relative_to(ctx.server_dir)  # type: ignore[arg-type]
            else:
                # Try resolving relative to server_dir
                abs_path = ctx.server_dir / file_path  # type: ignore[operator]
                if abs_path.exists():
                    rel = abs_path.parent.relative_to(ctx.server_dir)  # type: ignore[arg-type]
                else:
                    rel = fp.parent
            return f"./{rel}" if str(rel) != "." else "."
        except (ValueError, TypeError):
            return None

    def _resolve_test_package(self, ctx: ProjectContext, file_path: str) -> str | None:
        """Resolve file path to a Go package that has test files.

        Returns the package path (e.g., './internal/auth/') if _test.go exists,
        or None if no tests found.
        """
        try:
            fp = Path(file_path)
            if fp.is_absolute():
                pkg_abs = fp.parent
            else:
                pkg_abs = (ctx.server_dir / file_path).parent  # type: ignore[operator]

            # Check if there are any _test.go files in this package
            test_files = list(pkg_abs.glob("*_test.go"))
            if test_files:
                rel = pkg_abs.relative_to(ctx.server_dir)  # type: ignore[arg-type]
                return f"./{rel}" if str(rel) != "." else "."

            return None
        except (ValueError, TypeError, OSError):
            return None

    def _is_excluded(self, file_path: str) -> bool:
        """Check if a file is in an excluded directory."""
        parts = Path(file_path).parts
        return any(part in EXCLUDE_DIRS for part in parts)

    # ── Test execution ──────────────────────────────────────────────────

    def _run_package_tests(
        self,
        ctx: ProjectContext,
        pkg_dir: str,
        file_path: str,
        repeated_fail_threshold: int = REPEATED_FAIL_THRESHOLD,
    ) -> list[Finding]:
        """Run go test for a specific package and parse results.

        ``repeated_fail_threshold`` controls when V09-REPEATED-FAIL fires;
        defaults to the module constant for back-compat with any external
        caller, while ``validate()`` always supplies the config value.
        """
        findings: list[Finding] = []

        try:
            result = subprocess.run(
                ["go", "test", "-json", "-count=1", "-timeout=30s", pkg_dir],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        # Parse JSON output line by line
        failed_tests: list[str] = []
        passed_tests: list[str] = []

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            test_name = event.get("Test", "")
            action = event.get("Action", "")

            if not test_name:
                continue

            if action == "fail":
                failed_tests.append(test_name)
            elif action == "pass":
                passed_tests.append(test_name)

        # Track failures and check for repeated failures
        for test_name in passed_tests:
            self._track_failure(test_name, passed=True)

        for test_name in failed_tests:
            count = self._track_failure(test_name, passed=False)

            if count >= repeated_fail_threshold:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V09-REPEATED-FAIL",
                        message=(
                            f"'{test_name}' has failed {count} consecutive times. "
                            "This may indicate the PRD/test expectations need updating, "
                            "not just the implementation."
                        ),
                        fix=(
                            "Consider: /tdd-update to revise tests based on updated requirements. "
                            "Or confirm the test is correct and continue debugging the implementation."
                        ),
                    )
                )

        # Report test failures
        if failed_tests:
            test_names = ", ".join(failed_tests)
            run_target = failed_tests[0]

            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V09-TEST-FAIL",
                    message=f"Tests failed in {pkg_dir}: {test_names}",
                    fix=(
                        f"Fix failing tests. Run 'cd {ctx.server_dir} && "
                        f"go test -v -run {run_target} {pkg_dir}' for details"
                    ),
                )
            )

        return findings

    # ── Test existence check ────────────────────────────────────────────

    def _check_test_exists(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Warn if no _test.go file exists for the package."""
        findings: list[Finding] = []

        try:
            fp = Path(file_path)
            if fp.is_absolute():
                pkg_abs = fp.parent
            else:
                pkg_abs = (ctx.server_dir / file_path).parent  # type: ignore[operator]

            # Only warn if the directory exists and has .go files
            if not pkg_abs.exists():
                return findings

            go_files = [f for f in pkg_abs.glob("*.go") if not f.name.endswith("_test.go")]
            if not go_files:
                return findings

            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V09-NO-TEST",
                    message=(
                        "No _test.go file found in package directory. Consider adding tests for better code coverage."
                    ),
                    fix=(f"Create a test file (e.g., {Path(file_path).stem}_test.go) in the same package."),
                )
            )
        except (ValueError, TypeError, OSError):
            pass

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
            pass  # Tracking failure should never block validation


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
    validator = GoTestRunnerValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
