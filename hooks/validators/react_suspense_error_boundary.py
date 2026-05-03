"""V72: React Suspense + ErrorBoundary pairing — heuristic.

A ``<Suspense fallback={...}>`` boundary catches the suspended state
of a child (data-fetching promise, lazy import). It does **not**
catch errors. If the child's promise *rejects* — network failure,
schema mismatch, server error — the throw propagates up the tree
until something catches it. Without an ``<ErrorBoundary>`` ancestor,
the throw escapes the React root and unmounts the page.

Result: a single component's data-fetch failure shows the user a
blank page. At scale (100k user × 1% network failure = 1k blank
screens / day), that's user-facing damage.

V72 enforces that any file using ``<Suspense>`` either:

  1. Has a sibling ``<ErrorBoundary>`` element in the same file
     (parent-most heuristic, easy to verify), OR
  2. Is rendered under a project-wide layout file that already wraps
     children in ``<ErrorBoundary>``. Layout detection: ``app/layout.tsx``,
     ``app/layout.ts``, ``_app.tsx``, files whose basename ends in
     ``Layout.tsx``.

Rules:
  - V72-SUSPENSE-NO-EB — Suspense found in this file, ErrorBoundary
    not in this file AND not in any project-level layout (warning).

Limitations: this is a heuristic, not an AST analyzer. False
positives possible when EB sits in an intermediate component the
heuristic doesn't recognize as a layout. False negatives possible
when EB exists in the same file but doesn't actually wrap the
Suspense (sibling tree). v1 trades precision for low cost; AST
upgrade is Phase 74 candidate.

Escape hatch: same-line ``// verifier:suspense-eb-elsewhere REASON``
on the ``<Suspense`` line.

Reference: [React Suspense docs "Showing an error to users with an
error boundary"](https://react.dev/reference/react/Suspense#showing-an-error-to-users-with-an-error-boundary)
(continuously updated, retrieved 2026-05-03). [react-error-boundary](https://github.com/bvaughn/react-error-boundary)
(continuously developed since 2018).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

RE_SUSPENSE = re.compile(r"<Suspense\b")
RE_ERROR_BOUNDARY = re.compile(r"<(?:ErrorBoundary|FallbackBoundary)\b")
RE_VERIFIER_OK = re.compile(r"//\s*verifier:suspense-eb-elsewhere\b")

_LAYOUT_BASENAMES: tuple[str, ...] = (
    "layout.tsx",
    "layout.jsx",
    "layout.ts",
    "_app.tsx",
    "_app.jsx",
    "_document.tsx",
    "root.tsx",
    "RootLayout.tsx",
)

_EXCLUDE_HINTS: tuple[str, ...] = (
    ".gen.",
    "__generated__",
    "/dist/",
    "/build/",
    "/.next/",
    "/node_modules/",
)


def _is_layout_file(path: Path) -> bool:
    name = path.name
    if name in _LAYOUT_BASENAMES:
        return True
    return name.endswith("Layout.tsx") or name.endswith("Layout.jsx")


class ReactSuspenseErrorBoundaryValidator(BaseValidator):
    """V72: warn when <Suspense> appears without an <ErrorBoundary> ancestor."""

    id = "V72-react-suspense-eb"
    name = "React Suspense + ErrorBoundary pairing"
    file_patterns: list[str] = ["**/*.tsx", "**/*.jsx"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        if not path.is_file() or any(h in file_path for h in _EXCLUDE_HINTS):
            return []
        # For Tier 2 we don't have the project-wide layout cache. Scan one
        # file: only fire if the file is self-contained (no EB).
        return self._scan_file(path, has_layout_eb=self._project_has_layout_eb(ctx))

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        has_layout_eb = self._project_has_layout_eb(ctx)
        findings: list[Finding] = []
        for tsx_file in ctx.file_index.find_by_pattern("*.tsx", "*.jsx"):
            if any(h in str(tsx_file) for h in _EXCLUDE_HINTS):
                continue
            findings.extend(self._scan_file(tsx_file, has_layout_eb=has_layout_eb))
        return findings

    def _project_has_layout_eb(self, ctx: ProjectContext) -> bool:
        """Return True if ANY layout file in the project contains <ErrorBoundary>."""
        for tsx_file in ctx.file_index.find_by_pattern("*.tsx", "*.jsx"):
            if not _is_layout_file(tsx_file):
                continue
            try:
                src = tsx_file.read_text(errors="replace")
            except OSError:
                continue
            if RE_ERROR_BOUNDARY.search(src):
                return True
        return False

    def _scan_file(self, file_path: Path, *, has_layout_eb: bool) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []
        if not RE_SUSPENSE.search(src):
            return []
        if RE_ERROR_BOUNDARY.search(src):
            return []  # Same-file EB satisfies the rule.
        if has_layout_eb:
            return []  # Project-level layout already wraps everything.

        # Find the first <Suspense in the file for line-number reporting.
        suspense_match = RE_SUSPENSE.search(src)
        if suspense_match is None:  # impossible — we just searched — but mypy
            return []
        line_no = src.count("\n", 0, suspense_match.start()) + 1
        lines = src.splitlines()
        line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
        if RE_VERIFIER_OK.search(line_text):
            return []

        return [
            Finding(
                severity="warning",
                file=str(file_path),
                line=line_no,
                rule="V72-SUSPENSE-NO-EB",
                message=(
                    "<Suspense> used here but no <ErrorBoundary> in this file "
                    "or in any project layout. A rejected promise inside the "
                    "Suspense subtree will escape and unmount the entire page."
                ),
                fix=(
                    "Wrap this <Suspense> with <ErrorBoundary fallback={...}>: \n"
                    "  <ErrorBoundary fallback={<ErrorMsg/>}>\n"
                    "    <Suspense fallback={<Spinner/>}>...</Suspense>\n"
                    "  </ErrorBoundary>\n"
                    "Or place an <ErrorBoundary> in app/layout.tsx (or your "
                    "project's root layout) so all routes inherit the catch. "
                    "If EB lives in an upstream component the heuristic doesn't "
                    "recognize, add `// verifier:suspense-eb-elsewhere REASON`."
                ),
            )
        ]


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = ReactSuspenseErrorBoundaryValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
