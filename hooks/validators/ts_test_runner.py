"""V10: TypeScript targeted test runner — run related tests for modified TS/TSX files.

PostToolUse checks:
  V10-TEST-FAIL: Test failures for the modified file
  V10-NO-TEST: No matching test file found (warning)
  V10-REPEATED-FAIL: Same test failed 3+ consecutive times (warning)
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

# Failure tracker file path (shared with V09/V11)
FAILURE_TRACKER = Path(__file__).parent.parent.parent / "logs" / "test-failure-tracker.json"

# Threshold for repeated failure warning
REPEATED_FAIL_THRESHOLD = 3

# Directories to exclude
EXCLUDE_DIRS = {"node_modules", "dist", "build", ".next", "coverage", "generated", "gen"}


class TsTestRunnerValidator(BaseValidator):
    """V10: TypeScript Test Runner — targeted test execution."""

    id = "V10-ts-test-runner"
    name = "TypeScript Test Runner"
    file_patterns: list[str] = [
        "**/*.ts",
        "**/*.tsx",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        if not ctx.web_dir or not ctx.web_dir.exists():
            return ValidationResult(validator_id=self.id, findings=findings)

        if mode == "post_tool_use" and file_path and file_path.endswith((".ts", ".tsx")):
            # Skip excluded directories
            if self._is_excluded(file_path):
                return ValidationResult(validator_id=self.id, findings=findings)

            if self._is_test_file(file_path):
                # Test file modified — run it directly
                findings.extend(self._run_test_file(ctx, file_path))
            else:
                test_file = self._resolve_test_file(ctx, file_path)
                if test_file:
                    findings.extend(self._run_test_file(ctx, test_file))
                else:
                    findings.extend(self._check_test_exists(file_path))

        # Stop mode: not needed — V07 covers comprehensive checks
        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Test file detection ─────────────────────────────────────────────

    def _is_test_file(self, file_path: str) -> bool:
        """Check if this is a test file."""
        name = Path(file_path).name
        if re.search(r"\.(test|spec)\.(ts|tsx|js|jsx)$", name):
            return True
        if "__tests__" in file_path:
            return True
        return False

    def _is_excluded(self, file_path: str) -> bool:
        """Check if a file is in an excluded directory."""
        parts = Path(file_path).parts
        return any(part in EXCLUDE_DIRS for part in parts)

    # ── Test file resolution ────────────────────────────────────────────

    def _resolve_test_file(self, ctx: ProjectContext, source_path: str) -> str | None:
        """Resolve source file to its corresponding test file.

        Search order:
        1. Same directory: Button.tsx → Button.test.tsx
        2. Same directory: Button.tsx → Button.spec.tsx
        3. __tests__ directory: Button.tsx → __tests__/Button.test.tsx
        """
        fp = Path(source_path)
        stem = fp.stem
        parent = fp.parent

        # Determine if path is absolute or relative to web_dir
        if fp.is_absolute():
            abs_parent = parent
        else:
            abs_parent = ctx.web_dir / parent  # type: ignore[operator]

        # Check extensions in order of preference
        extensions = []
        if source_path.endswith(".tsx"):
            extensions = [".test.tsx", ".spec.tsx", ".test.ts", ".spec.ts"]
        elif source_path.endswith(".ts"):
            extensions = [".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"]

        # 1. Same directory
        for ext in extensions:
            candidate = abs_parent / f"{stem}{ext}"
            if candidate.exists():
                return str(candidate)

        # 2. __tests__ subdirectory
        tests_dir = abs_parent / "__tests__"
        if tests_dir.exists():
            for ext in extensions:
                candidate = tests_dir / f"{stem}{ext}"
                if candidate.exists():
                    return str(candidate)

        # 3. __tests__ in parent directory
        tests_dir = abs_parent.parent / "__tests__"
        if tests_dir.exists():
            for ext in extensions:
                candidate = tests_dir / f"{stem}{ext}"
                if candidate.exists():
                    return str(candidate)

        return None

    # ── Test runner detection ───────────────────────────────────────────

    def _detect_test_runner(self, ctx: ProjectContext) -> tuple[list[str], str]:
        """Detect the test runner and return (command_prefix, runner_name).

        Returns:
            Tuple of (command parts, runner name)
            e.g., (["bun", "run", "vitest", "run"], "vitest")
        """
        web_dir = ctx.web_dir
        if not web_dir:
            return ["bun", "test"], "bun"

        # Check for vitest
        for config in ["vitest.config.ts", "vitest.config.js", "vitest.config.mts"]:
            if (web_dir / config).exists():
                return ["bun", "run", "vitest", "run"], "vitest"

        # Check for jest
        for config in ["jest.config.ts", "jest.config.js", "jest.config.mjs", "jest.config.cjs"]:
            if (web_dir / config).exists():
                return ["bun", "run", "jest"], "jest"

        # Check package.json for jest
        pkg_json = web_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                if "jest" in pkg.get("devDependencies", {}) or "jest" in pkg.get("dependencies", {}):
                    return ["bun", "run", "jest"], "jest"
                if "vitest" in pkg.get("devDependencies", {}) or "vitest" in pkg.get("dependencies", {}):
                    return ["bun", "run", "vitest", "run"], "vitest"
            except (json.JSONDecodeError, OSError):
                pass

        # Default to bun test
        return ["bun", "test"], "bun"

    # ── Test execution ──────────────────────────────────────────────────

    def _run_test_file(self, ctx: ProjectContext, test_file: str) -> list[Finding]:
        """Run a specific test file and parse results."""
        findings: list[Finding] = []
        cmd_parts, runner = self._detect_test_runner(ctx)

        # Build the command based on runner
        if runner == "vitest":
            cmd = [*cmd_parts, test_file]
        elif runner == "jest":
            # Jest uses --testPathPattern for file matching
            cmd = [*cmd_parts, "--testPathPattern", Path(test_file).name]
        else:
            cmd = [*cmd_parts, test_file]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ctx.web_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            # Extract failed test names from output
            failed_tests = self._parse_test_failures(result.stdout + result.stderr)
            test_file_name = Path(test_file).name

            # Track failures
            for test_name in failed_tests:
                count = self._track_failure(test_name, passed=False)
                if count >= REPEATED_FAIL_THRESHOLD:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=test_file,
                            rule="V10-REPEATED-FAIL",
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

            # Truncate output for the error message
            output = (result.stdout + result.stderr).strip()
            if len(output) > 500:
                output = output[:500] + "..."

            findings.append(
                Finding(
                    severity="error",
                    file=test_file,
                    rule="V10-TEST-FAIL",
                    message=f"Tests failed in {test_file_name}: {', '.join(failed_tests) if failed_tests else 'see output'}",
                    fix=(
                        f"Fix failing tests. Run 'cd {ctx.web_dir} && "
                        f"{' '.join(cmd)}' for details"
                    ),
                )
            )
        else:
            # Tests passed — reset failure counters
            passed_tests = self._parse_test_passes(result.stdout + result.stderr)
            for test_name in passed_tests:
                self._track_failure(test_name, passed=True)

        return findings

    def _parse_test_failures(self, output: str) -> list[str]:
        """Parse test output to extract failed test names."""
        failed: list[str] = []

        # Vitest/Jest FAIL pattern: ✕ test name / × test name / FAIL test name
        for match in re.finditer(r"(?:✕|×|✗|FAIL)\s+(.+?)(?:\s+\(\d+\s*m?s\))?$", output, re.MULTILINE):
            name = match.group(1).strip()
            if name:
                failed.append(name)

        # Also try: "● test name" pattern (Jest)
        if not failed:
            for match in re.finditer(r"●\s+(.+?)$", output, re.MULTILINE):
                name = match.group(1).strip()
                if name:
                    failed.append(name)

        return failed

    def _parse_test_passes(self, output: str) -> list[str]:
        """Parse test output to extract passed test names."""
        passed: list[str] = []
        for match in re.finditer(r"(?:✓|✔|√|PASS)\s+(.+?)(?:\s+\(\d+\s*m?s\))?$", output, re.MULTILINE):
            name = match.group(1).strip()
            if name:
                passed.append(name)
        return passed

    # ── Test existence check ────────────────────────────────────────────

    def _check_test_exists(self, file_path: str) -> list[Finding]:
        """Warn if no test file exists for the source file."""
        findings: list[Finding] = []

        # Skip non-component files that typically don't have tests
        name = Path(file_path).name
        skip_patterns = [
            "index.ts",
            "types.ts",
            "constants.ts",
            "interfaces.ts",
            "declarations.d.ts",
            "env.d.ts",
            "vite-env.d.ts",
            "global.d.ts",
        ]
        if name in skip_patterns or name.endswith(".d.ts"):
            return findings

        findings.append(
            Finding(
                severity="warning",
                file=file_path,
                rule="V10-NO-TEST",
                message="No test file found for this source file.",
                fix=(
                    f"Create a test file (e.g., "
                    f"{Path(file_path).stem}.test{Path(file_path).suffix}) "
                    f"in the same directory or __tests__/."
                ),
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
    validator = TsTestRunnerValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
