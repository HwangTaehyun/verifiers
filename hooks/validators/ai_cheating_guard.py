"""V13: AI Cheating Guard — detect test deletion, disabling, weakening by AI agents.

Inspired by Kent Beck's experience: "I had trouble stopping AI agents from
deleting tests to make them pass."

Checks:
  V13-TEST-DELETED: Test function/method removed (error)
  V13-TEST-DISABLED: skip/disable annotation added (warning)
  V13-ASSERTION-REMOVED: Number of assert/expect statements decreased (warning)
  V13-TEST-WEAKENED: Strict assertion replaced with loose one (warning)
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
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

# ── Test file patterns ──────────────────────────────────────────────────────

TEST_FILE_PATTERNS: list[tuple[str, str]] = [
    (r"_test\.go$", "go"),
    (r"test_[^/]*\.py$", "python"),
    (r"[^/]*_test\.py$", "python"),
    (r"[^/]*\.test\.(ts|tsx|js|jsx)$", "typescript"),
    (r"[^/]*\.spec\.(ts|tsx|js|jsx)$", "typescript"),
    (r"__tests__/.*\.(ts|tsx|js|jsx)$", "typescript"),
]

# ── Test function patterns (for deletion detection) ─────────────────────────

TEST_FUNC_PATTERNS: dict[str, list[str]] = {
    "go": [
        r"func\s+Test\w+\s*\(",
        r"func\s+Benchmark\w+\s*\(",
    ],
    "python": [
        r"def\s+test_\w+\s*\(",
        r"class\s+Test\w+",
    ],
    "typescript": [
        r"\bit\s*\(",
        r"\btest\s*\(",
        r"\bdescribe\s*\(",
    ],
}

# ── Skip/disable patterns ──────────────────────────────────────────────────

SKIP_PATTERNS: dict[str, list[str]] = {
    "go": [
        r"t\.Skip\(",
        r"t\.Skipf\(",
        r"t\.SkipNow\(",
    ],
    "python": [
        r"@pytest\.mark\.skip",
        r"@pytest\.mark\.skipIf",
        r"@unittest\.skip",
        r"@unittest\.skipIf",
        r"@unittest\.skipUnless",
    ],
    "typescript": [
        r"\bit\.skip\s*\(",
        r"\btest\.skip\s*\(",
        r"\bxit\s*\(",
        r"\bxdescribe\s*\(",
        r"\bxtest\s*\(",
        r"\bdescribe\.skip\s*\(",
    ],
}

# ── Assertion patterns ──────────────────────────────────────────────────────

ASSERT_PATTERNS: dict[str, list[str]] = {
    "go": [
        r"assert\.\w+",
        r"require\.\w+",
        r"t\.Error\(",
        r"t\.Errorf\(",
        r"t\.Fatal\(",
        r"t\.Fatalf\(",
    ],
    "python": [
        r"\bassert\s+",
        r"self\.assert\w+\(",
        r"pytest\.raises\(",
        r"pytest\.warns\(",
    ],
    "typescript": [
        r"\bexpect\s*\(",
        r"\bassert\s*[\.(]",
        r"\.should\.",
    ],
}

# ── Mock/Patch patterns (for excessive mocking detection) ────────────────────

MOCK_PATTERNS: dict[str, list[str]] = {
    "go": [
        r"\bmock\.New\w+\(",  # testify/gomock: mock.NewMockFoo(...)
        r"\bgomock\.NewController\(",  # gomock controller creation
        r"\.EXPECT\(\)",  # gomock expectation setup
    ],
    "python": [
        r"\bmock\.patch\(",  # with mock.patch(...) / mock.patch(...)
        r"@mock\.patch",  # @mock.patch decorator
        r"@patch\(",  # @patch() decorator (standalone import)
        r"\bmock\.patch\.object\(",  # mock.patch.object(...)
        r"\bmocker\.patch\(",  # pytest-mock: mocker.patch(...)
        r"\bMagicMock\(",  # standalone MagicMock() call
        r"(?<!Magic)\bMock\(",  # standalone Mock() call — excludes MagicMock
    ],
    "typescript": [
        r"\bjest\.mock\(",  # jest.mock(...)
        r"\bjest\.spyOn\(",  # jest.spyOn(...)
        r"\bvi\.mock\(",  # vitest: vi.mock(...)
        r"\bvi\.spyOn\(",  # vitest: vi.spyOn(...)
        r"\bsinon\.stub\(",  # sinon: sinon.stub(...)
        r"\bsinon\.mock\(",  # sinon: sinon.mock(...)
        r"\.mockImplementation\(",  # jest/vitest mock setup
        r"\.mockReturnValue\(",  # jest/vitest mock return
        r"\.mockResolvedValue\(",  # jest/vitest async mock return
    ],
}

# ── Trivial assertion patterns (meaningless tests) ───────────────────────────

TRIVIAL_ASSERT_PATTERNS: dict[str, list[str]] = {
    "go": [
        r"assert\.True\(t,\s*true\)",
        r"assert\.False\(t,\s*false\)",
        r"assert\.Equal\(t,\s*1,\s*1\)",
        r"assert\.Equal\(t,\s*true,\s*true\)",
        r'assert\.Equal\(t,\s*"",\s*""\)',
        r"require\.True\(t,\s*true\)",
        r"require\.Equal\(t,\s*1,\s*1\)",
    ],
    "python": [
        r"\bassert\s+True\s*$",
        r"\bassert\s+True\s*[,#]",
        r"\bassert\s+1\s*==\s*1",
        r"\bassert\s+not\s+False",
        r'\bassert\s+""\s*==\s*""',
        r"\bself\.assertTrue\(\s*True\s*\)",
        r"\bself\.assertEqual\(\s*1\s*,\s*1\s*\)",
        r"\bself\.assertFalse\(\s*False\s*\)",
    ],
    "typescript": [
        r"expect\(\s*true\s*\)\.toBe\(\s*true\s*\)",
        r"expect\(\s*false\s*\)\.toBe\(\s*false\s*\)",
        r"expect\(\s*1\s*\)\.toBe\(\s*1\s*\)",
        r"expect\(\s*true\s*\)\.toBeTruthy\(\s*\)",
        r'expect\(\s*""\s*\)\.toBe\(\s*""\s*\)',
        r"expect\(\s*true\s*\)\.toEqual\(\s*true\s*\)",
    ],
}

# Maximum number of mock/patch calls per test function before warning
MOCK_THRESHOLD = 5

# ── Weakening replacements (strict → loose) ─────────────────────────────────

WEAKENING_PAIRS: dict[str, list[tuple[str, str]]] = {
    "go": [
        ("assert.Equal", "assert.NotNil"),
        ("assert.Equal", "assert.True"),
        ("require.Equal", "require.NotNil"),
        ("require.Equal", "assert.True"),
        ("assert.Equal", "assert.NotEmpty"),
        ("require.Equal", "require.NotEmpty"),
    ],
    "python": [
        ("assertEqual", "assertTrue"),
        ("assertIs", "assertTrue"),
        ("assertIn", "assertTrue"),
        ("assert .* == ", "assert .* is not None"),
    ],
    "typescript": [
        ("toEqual", "toBeTruthy"),
        ("toStrictEqual", "toBeTruthy"),
        ("toBe", "toBeTruthy"),
        ("toEqual", "toBeDefined"),
        ("toStrictEqual", "toBeDefined"),
        ("toBe", "toBeDefined"),
    ],
}


def _detect_test_language(file_path: str) -> str | None:
    """Detect the test language from a file path.

    Returns "go", "python", "typescript", or None if not a test file.
    """
    for pattern, lang in TEST_FILE_PATTERNS:
        if re.search(pattern, file_path):
            return lang
    return None


def _count_patterns(text: str, patterns: list[str]) -> int:
    """Count total matches of any pattern in the text."""
    total = 0
    for pattern in patterns:
        total += len(re.findall(pattern, text))
    return total


def _count_test_funcs(text: str, lang: str) -> int:
    """Count test function declarations in the text."""
    patterns = TEST_FUNC_PATTERNS.get(lang, [])
    return _count_patterns(text, patterns)


def _count_assertions(text: str, lang: str) -> int:
    """Count assertion statements in the text."""
    patterns = ASSERT_PATTERNS.get(lang, [])
    return _count_patterns(text, patterns)


def _count_skip_patterns(text: str, lang: str) -> int:
    """Count skip/disable patterns in the text."""
    patterns = SKIP_PATTERNS.get(lang, [])
    return _count_patterns(text, patterns)


def _find_new_skip_lines(old_text: str, new_text: str, lang: str) -> list[tuple[int, str]]:
    """Find lines in new_text that contain newly added skip patterns.

    Returns list of (line_number, line_content).
    """
    patterns = SKIP_PATTERNS.get(lang, [])
    if not patterns:
        return []

    results: list[tuple[int, str]] = []
    new_lines = new_text.split("\n")
    for i, line in enumerate(new_lines, 1):
        for pattern in patterns:
            if re.search(pattern, line):
                # Check this pattern wasn't in old_text at all
                # (Simple heuristic: if the exact line didn't exist before)
                if line.strip() not in {old_line.strip() for old_line in old_text.split("\n")}:
                    results.append((i, line.strip()))
                    break
    return results


def _count_mocks_in_func(func_text: str, lang: str) -> int:
    """Count mock/patch patterns within a single function body."""
    patterns = MOCK_PATTERNS.get(lang, [])
    return _count_patterns(func_text, patterns)


def _extract_test_functions(text: str, lang: str) -> list[tuple[str, str]]:
    """Extract (function_name, function_body) pairs from test file content.

    Returns a list of (name, body) tuples for each test function found.
    Uses simple heuristic parsing — not a full AST — sufficient for
    detecting mock overuse patterns.
    """
    lines = text.split("\n")
    extractors = {
        "python": _extract_python_tests,
        "go": _extract_go_tests,
        "typescript": _extract_ts_tests,
    }
    extractor = extractors.get(lang)
    return extractor(lines) if extractor else []


def _extract_python_tests(lines: list[str]) -> list[tuple[str, str]]:
    """Extract Python test functions by indentation-based parsing."""
    results: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)def\s+(test_\w+)\s*\(", lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        name = m.group(2)
        func_lines = [lines[i]]
        i += 1
        while i < len(lines):
            if lines[i].strip() == "":
                func_lines.append(lines[i])
                i += 1
                continue
            curr_indent = len(lines[i]) - len(lines[i].lstrip())
            if curr_indent <= indent and lines[i].strip():
                break
            func_lines.append(lines[i])
            i += 1
        results.append((name, "\n".join(func_lines)))
    return results


def _extract_go_tests(lines: list[str]) -> list[tuple[str, str]]:
    """Extract Go test functions by brace-depth tracking."""
    results: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^func\s+(Test\w+|Benchmark\w+)\s*\(", lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        func_lines, end = _collect_brace_block(lines, i)
        results.append((name, "\n".join(func_lines)))
        i = end + 1 if end > i else i + 1
    return results


def _extract_ts_tests(lines: list[str]) -> list[tuple[str, str]]:
    """Extract TypeScript it()/test() blocks by paren-depth tracking."""
    results: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = re.search(r'\b(it|test)\s*\(\s*["\']([^"\']+)["\']', lines[i])
        if not m:
            i += 1
            continue
        name = m.group(2)
        func_lines, end = _collect_paren_block(lines, i)
        results.append((name, "\n".join(func_lines)))
        i = end + 1 if end > i else i + 1
    return results


def _collect_brace_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect lines from start until braces balance."""
    brace_depth = 0
    func_lines: list[str] = []
    end = start
    for j in range(start, len(lines)):
        func_lines.append(lines[j])
        brace_depth += lines[j].count("{") - lines[j].count("}")
        if brace_depth <= 0 and j > start:
            end = j
            break
    return func_lines, end


def _collect_paren_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect lines from start until parentheses balance."""
    paren_depth = 0
    func_lines: list[str] = []
    end = start
    for j in range(start, len(lines)):
        func_lines.append(lines[j])
        paren_depth += lines[j].count("(") - lines[j].count(")")
        if paren_depth <= 0 and j > start:
            end = j
            break
    return func_lines, end


def _count_trivial_assertions(text: str, lang: str) -> list[tuple[int, str]]:
    """Find trivial assertions in text.

    Returns list of (line_number, matched_pattern_description).
    """
    patterns = TRIVIAL_ASSERT_PATTERNS.get(lang, [])
    if not patterns:
        return []

    results: list[tuple[int, str]] = []
    for i, line in enumerate(text.split("\n"), 1):
        for pattern in patterns:
            if re.search(pattern, line):
                results.append((i, line.strip()))
                break
    return results


class AiCheatingGuardValidator(BaseValidator):
    """V13: AI Cheating Guard — detect test deletion, disabling, weakening."""

    id = "V13-ai-cheating-guard"
    name = "AI Cheating Guard"
    file_patterns: list[str] = [
        "*_test.go",
        "test_*.py",
        "*_test.py",
        "*.test.ts",
        "*.test.tsx",
        "*.test.js",
        "*.test.jsx",
        "*.spec.ts",
        "*.spec.tsx",
        "*.spec.js",
        "*.spec.jsx",
        "*__tests__*",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        if file_path:
            lang = _detect_test_language(file_path)
            if lang:
                findings.extend(self._check_file(file_path, lang))

        return ValidationResult(validator_id=self.id, findings=findings)

    def _check_file(self, file_path: str, lang: str) -> list[Finding]:
        """Run all cheating checks on a single test file."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []

        # V13-TEST-DISABLED: look for skip patterns in the current file content
        findings.extend(self._check_skip_patterns(file_path, content, lang))

        # V13-MOCK-EVERYTHING: detect excessive mocking per test function
        findings.extend(self._check_excessive_mocks(file_path, content, lang))

        # V13-TRIVIAL-TEST: detect meaningless assertions
        findings.extend(self._check_trivial_assertions(file_path, content, lang))

        return findings

    def check_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> list[Finding]:
        """Check an Edit operation for cheating patterns.

        This is the primary check path — called when we have old_string/new_string
        from an Edit tool invocation. Compares the before/after to detect:
          - test function deletion
          - assertion removal
          - assertion weakening
          - skip pattern addition
        """
        lang = _detect_test_language(file_path)
        if not lang:
            return []

        findings: list[Finding] = []

        # V13-TEST-DELETED: test functions in old but not in new
        old_tests = _count_test_funcs(old_string, lang)
        new_tests = _count_test_funcs(new_string, lang)
        if old_tests > 0 and new_tests < old_tests:
            removed = old_tests - new_tests
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V13-TEST-DELETED",
                    message=f"{removed} test function(s) were deleted",
                    fix=(
                        f"Restore the deleted test function(s) in {file_path}. "
                        "If the test is genuinely obsolete, explain why before removing it."
                    ),
                )
            )

        # V13-ASSERTION-REMOVED: assertion count decreased
        old_asserts = _count_assertions(old_string, lang)
        new_asserts = _count_assertions(new_string, lang)
        if old_asserts > 0 and new_asserts < old_asserts:
            removed = old_asserts - new_asserts
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V13-ASSERTION-REMOVED",
                    message=f"{removed} assertion(s) were removed (had {old_asserts}, now {new_asserts})",
                    fix=(
                        f"Review the removed assertions in {file_path}. "
                        "If assertions were removed to make tests pass, restore them "
                        "and fix the underlying code instead."
                    ),
                )
            )

        # V13-TEST-DISABLED: new skip patterns added
        old_skips = _count_skip_patterns(old_string, lang)
        new_skips = _count_skip_patterns(new_string, lang)
        if new_skips > old_skips:
            added = new_skips - old_skips
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V13-TEST-DISABLED",
                    message=f"{added} test skip/disable annotation(s) were added",
                    fix=(
                        f"Remove the skip annotation(s) in {file_path}. Fix the failing test instead of disabling it."
                    ),
                )
            )

        # V13-TEST-WEAKENED: strict assertion replaced with loose one
        findings.extend(self._check_weakening(file_path, old_string, new_string, lang))

        return findings

    def _check_skip_patterns(self, file_path: str, content: str, lang: str) -> list[Finding]:
        """Check for skip patterns in file content (Write mode — no old_string)."""
        skip_lines = []
        patterns = SKIP_PATTERNS.get(lang, [])
        for i, line in enumerate(content.split("\n"), 1):
            for pattern in patterns:
                if re.search(pattern, line):
                    skip_lines.append((i, line.strip()))
                    break

        findings: list[Finding] = []
        for line_num, line_content in skip_lines:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V13-TEST-DISABLED",
                    message=f"Test skip/disable pattern found: {line_content}",
                    fix=(
                        f"Remove the skip annotation at {file_path}:{line_num}. "
                        "Fix the failing test instead of disabling it."
                    ),
                    line=line_num,
                )
            )
        return findings

    def _check_excessive_mocks(self, file_path: str, content: str, lang: str) -> list[Finding]:
        """V13-MOCK-EVERYTHING: detect test functions with excessive mock/patch usage.

        A test that mocks everything is not testing real behavior — it's testing
        that mock wiring works. Threshold: > MOCK_THRESHOLD mocks per test function.
        """
        findings: list[Finding] = []
        test_funcs = _extract_test_functions(content, lang)

        for func_name, func_body in test_funcs:
            mock_count = _count_mocks_in_func(func_body, lang)
            if mock_count > MOCK_THRESHOLD:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V13-MOCK-EVERYTHING",
                        message=(f"Test '{func_name}' has {mock_count} mock/patch calls (threshold: {MOCK_THRESHOLD})"),
                        fix=(
                            f"Reduce mocking in '{func_name}' in {file_path}. "
                            "Consider using real implementations or integration tests. "
                            "Excessive mocking can hide real bugs."
                        ),
                    )
                )

        return findings

    def _check_trivial_assertions(self, file_path: str, content: str, lang: str) -> list[Finding]:
        """V13-TRIVIAL-TEST: detect meaningless assertions that always pass.

        Patterns like `assert True`, `assert 1 == 1`, `expect(true).toBe(true)`
        don't verify any real behavior — they're placeholder tests.
        """
        findings: list[Finding] = []
        trivial_matches = _count_trivial_assertions(content, lang)

        for line_num, line_content in trivial_matches:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V13-TRIVIAL-TEST",
                    message=f"Trivial assertion found: {line_content}",
                    fix=(
                        f"Replace the trivial assertion at {file_path}:{line_num} "
                        "with a meaningful check that validates real behavior."
                    ),
                    line=line_num,
                )
            )

        return findings

    def _check_weakening(self, file_path: str, old_string: str, new_string: str, lang: str) -> list[Finding]:
        """Check if strict assertions were replaced with weaker ones."""
        pairs = WEAKENING_PAIRS.get(lang, [])
        findings: list[Finding] = []

        for strict, loose in pairs:
            # Use word-boundary-aware patterns to avoid substring matches
            # e.g., "toBe" should NOT match inside "toBeDefined"
            strict_pattern = re.escape(strict) + r"(?!\w)"
            loose_pattern = re.escape(loose) + r"(?!\w)"

            strict_in_old = len(re.findall(strict_pattern, old_string))
            strict_in_new = len(re.findall(strict_pattern, new_string))
            loose_in_old = len(re.findall(loose_pattern, old_string))
            loose_in_new = len(re.findall(loose_pattern, new_string))

            # Strict decreased AND loose increased → likely weakening
            if strict_in_old > strict_in_new and loose_in_new > loose_in_old:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V13-TEST-WEAKENED",
                        message=f"Strict assertion '{strict}' replaced with weaker '{loose}'",
                        fix=(
                            f"Revert the weakened assertion in {file_path}. "
                            f"Use '{strict}' instead of '{loose}' for precise validation."
                        ),
                    )
                )

        return findings


# ── Standalone execution (for skill frontmatter hooks) ───────────────────────


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

    lang = _detect_test_language(file_path)
    if not lang:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = AiCheatingGuardValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    all_findings: list[Finding] = []

    # For Edit operations, check old_string vs new_string
    if tool_name == "Edit":
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        if old_string or new_string:
            all_findings.extend(validator.check_edit(file_path, old_string, new_string))

    # Always run file-level checks
    result = validator.run(ctx, file_path, mode="post_tool_use")
    all_findings.extend(result.findings)

    output = format_output(all_findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
