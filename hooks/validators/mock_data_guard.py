"""V18: Mock data guard — prevents frontend hooks from using hardcoded mock data instead of real API calls.

PostToolUse checks (fast, <5s):
  V18-MOCK-DATA: Detects mock/hardcoded data arrays in hook files instead of API client calls
  V18-MOCK-VARIABLE: Detects mock data variable declarations (MOCK_*, mock*)
  V18-FAKE-DELAY: Detects simulated network delays (setTimeout/Promise for fake loading)
  V18-TODO-API: Detects TODO comments about replacing with API calls
  V18-NO-API-IMPORT: Hook files that don't import any API client
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

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext

# ── Patterns for detecting mock data ──────────────────────────────────────────

# Variable names that indicate mock data
MOCK_VAR_PATTERN = re.compile(
    r"""(?:^|\s)(?:const|let|var)\s+("""
    r"""MOCK_\w+|mock\w+|FAKE_\w+|fake\w+|DUMMY_\w+|dummy\w+|STUB_\w+|stub\w+"""
    r""")""",
    re.IGNORECASE,
)

# Inline mock data assignments (hardcoded arrays/objects as state)
# Use word-boundary (\b) to match standalone property names only,
# so "totalCount" does not false-positive match "count".
HARDCODED_STATE_PATTERN = re.compile(
    r"""set\w+\(\s*\[?\s*\{[^}]*\b(?:rank|score|username|count|value|id)\s*:""",
    re.IGNORECASE,
)

# Simulated delay patterns
FAKE_DELAY_PATTERN = re.compile(
    r"""new\s+Promise\s*\(\s*(?:resolve|r)\s*=>\s*setTimeout"""
    r"""|await\s+new\s+Promise.*setTimeout"""
    r"""|// Simulate\s+(?:network|API|loading|delay)"""
    r"""|// Simulated\s+(?:network|API|loading|delay)""",
    re.IGNORECASE,
)

# TODO comments about API replacement
TODO_API_PATTERN = re.compile(
    r"""(?://|/\*)\s*TODO\s*:?\s*(?:Replace|Connect|Wire|Hook)\s+(?:with|to|up)\s+(?:actual|real)\s+API""",
    re.IGNORECASE,
)

# Import patterns that indicate real API client usage
API_IMPORT_PATTERN = re.compile(
    r"""import\s+.*from\s+['"](?:"""
    r"""\.\.?/api/|"""
    r"""@connectrpc/|"""
    r"""\.\.?/gen/|"""
    r"""\.\.?/client|"""
    r"""\.\.?/services?/"""
    r""")""",
    re.IGNORECASE,
)


class MockDataGuardValidator(BaseValidator):
    """V18: Mock Data Guard — prevents hardcoded mock data in frontend hooks."""

    id = "V18-mock-data-guard"
    name = "Mock Data Guard"
    file_patterns: list[str] = [
        "**/hooks/use*Data.ts",
        "**/hooks/use*Data.tsx",
        "**/hooks/use*.ts",
        "**/hooks/use*.tsx",
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

        if file_path and self._is_hook_file(file_path):
            findings.extend(self._check_mock_variables(file_path))
            findings.extend(self._check_hardcoded_state(file_path))
            findings.extend(self._check_fake_delay(file_path))
            findings.extend(self._check_todo_api(file_path))
            findings.extend(self._check_no_api_import(file_path))

        # Stop mode: scan all hook files in web/src/hooks/
        if mode == "stop":
            hooks_dir = ctx.web_dir / "src" / "hooks"
            if hooks_dir.exists():
                for hook_file in hooks_dir.glob("use*Data.ts"):
                    fp = str(hook_file)
                    if fp == file_path:
                        continue  # already checked above
                    findings.extend(self._check_mock_variables(fp))
                    findings.extend(self._check_hardcoded_state(fp))
                    findings.extend(self._check_fake_delay(fp))
                    findings.extend(self._check_todo_api(fp))
                    findings.extend(self._check_no_api_import(fp))

        return ValidationResult(validator_id=self.id, findings=findings)

    @staticmethod
    def _is_hook_file(file_path: str) -> bool:
        """Check if the file is a React hook file (use*.ts or use*.tsx)."""
        name = Path(file_path).name
        return name.startswith("use") and name.endswith((".ts", ".tsx"))

    # ── Check 1: Mock variable names ──────────────────────────────────────

    def _check_mock_variables(self, file_path: str) -> list[Finding]:
        """Detect variables named MOCK_*, mock*, FAKE_*, etc."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        # Skip test files
        if any(exc in file_path for exc in [".test.", ".spec.", "__tests__"]):
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            match = MOCK_VAR_PATTERN.search(line)
            if match:
                var_name = match.group(1)
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V18-MOCK-VARIABLE",
                        message=f"Mock data variable '{var_name}' found — use real API client instead",
                        fix=(
                            f"Remove mock variable '{var_name}' at {file_path}:{i}. "
                            f"Import the API client from 'api/client' and call the real endpoint."
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 2: Hardcoded state ──────────────────────────────────────────

    def _check_hardcoded_state(self, file_path: str) -> list[Finding]:
        """Detect inline hardcoded data being set directly into state."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        if any(exc in file_path for exc in [".test.", ".spec.", "__tests__"]):
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "*", "/*")):
                continue
            if HARDCODED_STATE_PATTERN.search(line):
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V18-MOCK-DATA",
                        message="Hardcoded mock data set into state — use API response instead",
                        fix=(
                            f"Replace hardcoded state assignment at {file_path}:{i} "
                            f"with data from the Connect-RPC API client response."
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 3: Fake delay ───────────────────────────────────────────────

    def _check_fake_delay(self, file_path: str) -> list[Finding]:
        """Detect simulated network delays."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        if any(exc in file_path for exc in [".test.", ".spec.", "__tests__"]):
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            if FAKE_DELAY_PATTERN.search(line):
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V18-FAKE-DELAY",
                        message="Simulated network delay found — real API calls have actual latency",
                        fix=(f"Remove fake delay at {file_path}:{i}. Real API calls already have network latency."),
                        line=i,
                    )
                )

        return findings

    # ── Check 4: TODO API comments ────────────────────────────────────────

    def _check_todo_api(self, file_path: str) -> list[Finding]:
        """Detect TODO comments about replacing with real API."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            if TODO_API_PATTERN.search(line):
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V18-TODO-API",
                        message="TODO comment about API replacement found — connect to real API now",
                        fix=(
                            f"Implement the real API call at {file_path}:{i} instead of leaving a TODO. "
                            f"Import client from 'api/client' and call the actual endpoint."
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 5: No API import ────────────────────────────────────────────

    def _check_no_api_import(self, file_path: str) -> list[Finding]:
        """Detect hook files that don't import any API client."""
        name = Path(file_path).name
        # Only check use*Data.ts files — regular hooks like useAuth don't need API
        if not re.match(r"use\w+Data\.tsx?$", name):
            return []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        if any(exc in file_path for exc in [".test.", ".spec.", "__tests__"]):
            return []

        if API_IMPORT_PATTERN.search(content):
            return []

        return [
            Finding(
                severity="error",
                file=file_path,
                rule="V18-NO-API-IMPORT",
                message="Data hook has no API client import — likely using mock data",
                fix=(
                    f"Add API client import to {file_path}. Example: import {{ dashboardClient }} from '../api/client';"
                ),
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
    validator = MockDataGuardValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
