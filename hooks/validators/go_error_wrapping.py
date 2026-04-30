"""V34: Go Error Wrapping (%w).

Checks that every bare ``return err`` (or ``return foo, err``) in non-generated
Go files under cmd/ and internal/ is preceded by a wrapping call such as
``fmt.Errorf("...: %w", err)``, ``errors.New(...)``, or
``connect.NewError(code, err)``.

This check is intentionally heuristic: it looks only at the previous
non-empty, non-comment line.  False positives are expected in pass-through
helpers and leaf functions.  Severity is ``warning`` (not ``error``) for this
reason.  Suppress individual false positives with a trailing comment:
    return err  //nolint:V34 // already wrapped at call site
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches lines of the form:
#   return err
#   return foo, err
#   return foo, bar, err
# The \b after err avoids matching `errFoo` variables.
_BARE_RETURN = re.compile(r"^\s*return\s+(?:[^,\s]+\s*,\s*)*err\s*$")

# Patterns that indicate the error was already given context on the preceding
# statement.
_WRAPPING_PATTERNS = (
    r"\bfmt\.Errorf\s*\(",
    r"\berrors\.New\s*\(",
    r"\bconnect\.NewError\s*\(",
)
_WRAPPING_RE = re.compile("|".join(_WRAPPING_PATTERNS))

# Inline nolint directive: //nolint:V34
_NOLINT_RE = re.compile(r"//\s*nolint\s*:.*\bV34\b")

# Generated file markers found in the first 5 lines
_CODE_GENERATED_RE = re.compile(r"//\s*(Code generated|DO NOT EDIT)")


def _is_generated(file_path: Path, lines: list[str]) -> bool:
    """Return True if the file is machine-generated and should be skipped."""
    # Path-based: file is under a gen/ directory
    if "/gen/" in str(file_path):
        return True
    # Name-based: file ends with .generated.go
    if file_path.name.endswith(".generated.go"):
        return True
    # Header-based: first 5 lines contain a code-generated marker
    for line in lines[:5]:
        if _CODE_GENERATED_RE.search(line):
            return True
    return False


def _is_eligible(file_path: Path) -> bool:
    """Return True if the file should be checked at all."""
    if file_path.suffix != ".go":
        return False
    # Skip test files
    if file_path.name.endswith("_test.go"):
        return False
    return True


def _prev_nonblank_line(lines: list[str], index: int) -> str:
    """Return the nearest preceding non-empty, non-comment-only line."""
    for i in range(index - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("//"):
            return lines[i]
    return ""


def _check_bare_returns(file_path: Path, src: str) -> list[Finding]:
    lines = src.splitlines()

    if _is_generated(file_path, lines):
        return []

    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if not _BARE_RETURN.search(line):
            continue
        # Honor inline nolint directive on the same line
        if _NOLINT_RE.search(line):
            continue
        # Check the return line itself for wrapping (e.g. return fmt.Errorf(...))
        if _WRAPPING_RE.search(line):
            continue
        # Check the preceding non-blank, non-comment line
        prev = _prev_nonblank_line(lines, i)
        if _WRAPPING_RE.search(prev):
            continue
        findings.append(
            Finding(
                severity="warning",
                file=str(file_path),
                line=i + 1,
                rule="V34-BARE-ERROR-RETURN",
                message=(
                    "Bare `return err` loses call-site context. The caller cannot distinguish "
                    "where this error originated, defeating errors.Is/As inspection."
                ),
                fix=(
                    "Wrap with %w to preserve the error chain:\n"
                    '    return fmt.Errorf("describe what failed: %w", err)\n'
                    "If the error is already wrapped at the call site immediately above "
                    "this return, mark this validator's known false-positive with "
                    "//nolint:V34 // already wrapped"
                ),
            )
        )
    return findings


def _scan_file(file_path: Path) -> list[Finding]:
    if not _is_eligible(file_path):
        return []
    try:
        src = file_path.read_text(errors="replace")
    except OSError:
        return []
    return _check_bare_returns(file_path, src)


class GoErrorWrappingValidator(BaseValidator):
    """V34: Go Error Wrapping (%w).

    Heuristic check — severity is warning because pass-through helpers and
    leaf functions legitimately bare-return errors.  False positives can be
    suppressed per-line with ``//nolint:V34 // already wrapped``.
    """

    id = "V34-go-error-wrapping"
    name = "Go Error Wrapping (%w)"
    file_patterns: list[str] = [
        "server/cmd/**/*.go",
        "server/internal/**/*.go",
        "**/cmd/**/*.go",
        "**/internal/**/*.go",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): scan the single edited Go file."""
        return _scan_file(Path(file_path))

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): walk all matching Go files under server_dir (or cwd)."""
        root = ctx.server_dir if ctx.server_dir is not None else ctx.project_root
        if root is None:
            return []
        findings: list[Finding] = []
        for candidate in root.rglob("*.go"):
            if _is_eligible(candidate):
                findings.extend(_scan_file(candidate))
        return findings


# ── Standalone execution ──────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoErrorWrappingValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
