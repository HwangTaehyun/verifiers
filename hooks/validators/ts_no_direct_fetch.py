"""V66: TypeScript no-direct-fetch — Client Components must route HTTP via service layer.

When a React Client Component calls ``fetch()`` or ``axios.get()``
directly, several costs accumulate at scale:

  1. Error / loading / retry handling is reimplemented per component
  2. Auth headers / telemetry / base URL are scattered
  3. Tests need to mock ``fetch`` per-component
  4. Switching the HTTP client (fetch → axios → ky) is a grep-and-replace

The canonical fix is a service layer: ``src/services/user_service.ts``
exports ``fetchUser(id)`` which the component consumes via a typed
hook (``useUser``) — service layer owns the cross-cutting concerns.

V66 enforces this for Client Components. **Server Components (RSC)
are exempt** — `await fetch(...)` is canonical there ([Next.js docs](https://nextjs.org/docs/app/building-your-application/data-fetching/fetching),
continuously updated). V66 detects Client Components by:

  - File contains a ``'use client'`` / ``"use client"`` directive in
    its first ~200 chars
  - File path is under ``src/components/`` (convention-based)
  - File uses ``useState`` / ``useEffect`` / ``useReducer`` (Client-only hooks)

Rules:
  - V66-COMPONENT-DIRECT-FETCH — Client Component calls ``fetch(`` /
    ``axios.<method>(`` directly (warning).

Escape hatch: same-line ``// verifier:fetch-ok REASON`` (e.g. one-shot
analytics ping that doesn't need a service layer).

Reference: [React Server Components RFC](https://github.com/reactjs/rfcs/blob/main/text/0188-server-components.md)
(published 2020-12). [Dan Abramov "You Might Not Need an Effect"](https://react.dev/learn/you-might-not-need-an-effect)
(last updated 2024-02, retrieved 2026-05-03).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Match raw fetch() and axios.<method>() calls. Identifier-based — won't
# match library wrappers like ``api.fetch(...)``.
RE_RAW_FETCH = re.compile(
    r"\b(?:fetch|axios\.(?:get|post|put|delete|patch|head|options))\s*\(",
)

RE_CLIENT_DIRECTIVE = re.compile(r"""['"]use client['"]""")
RE_SERVER_DIRECTIVE = re.compile(r"""['"]use server['"]""")
RE_CLIENT_HOOKS = re.compile(r"\buse(?:State|Effect|Reducer|Context|Ref|LayoutEffect)\s*\(")

# Same-line escape hatch.
RE_VERIFIER_OK = re.compile(r"//\s*verifier:fetch-ok\b")

_EXCLUDE_HINTS: tuple[str, ...] = (
    ".gen.",
    "__generated__",
    "/dist/",
    "/build/",
    "/.next/",
    "/node_modules/",
    "/services/",
    "/api/",
    "/lib/",
)


def _is_client_component(file_path: Path, src: str) -> bool:
    """Heuristic: True when the file behaves like a Client Component.

    Three independent signals; any one is enough:
      1. ``'use client'`` directive in the first ~200 chars
      2. File path contains ``/components/`` (convention)
      3. File uses one of the client-only React hooks
    """
    head = src[:200]
    if RE_CLIENT_DIRECTIVE.search(head):
        return True
    if RE_SERVER_DIRECTIVE.search(head):
        return False  # Explicit server component → not in scope.
    path_str = str(file_path).replace("\\", "/")
    if "/components/" in path_str:
        return True
    if RE_CLIENT_HOOKS.search(src):
        return True
    return False


class TsNoDirectFetchValidator(BaseValidator):
    """V66: forbid raw fetch / axios calls inside Client Components."""

    id = "V66-ts-no-direct-fetch"
    name = "TS No Direct Fetch in Client Components"
    file_patterns: list[str] = ["**/*.tsx", "**/*.ts"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        if not path.is_file() or any(h in file_path for h in _EXCLUDE_HINTS):
            return []
        return self._scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        findings: list[Finding] = []
        for ts_file in ctx.file_index.find_by_pattern("*.ts", "*.tsx"):
            if any(h in str(ts_file) for h in _EXCLUDE_HINTS):
                continue
            findings.extend(self._scan_file(ts_file))
        return findings

    def _scan_file(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []
        if "fetch(" not in src and "axios" not in src:
            return []
        if not _is_client_component(file_path, src):
            return []

        findings: list[Finding] = []
        lines = src.splitlines()
        for match in RE_RAW_FETCH.finditer(src):
            line_no = src.count("\n", 0, match.start()) + 1
            line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            if RE_VERIFIER_OK.search(line_text):
                continue
            call_name = match.group(0).rstrip("(").strip()
            findings.append(
                Finding(
                    severity="warning",
                    file=str(file_path),
                    line=line_no,
                    rule="V66-COMPONENT-DIRECT-FETCH",
                    message=(
                        f"Client Component calls `{call_name}` directly. "
                        "Route HTTP through a service layer (e.g. "
                        "`src/services/<domain>.ts`) so error / loading / "
                        "auth / telemetry concerns live in one place."
                    ),
                    fix=(
                        f"Extract the call into a service module: "
                        f"`export async function fetchX(...) {{ const r = await {call_name}...); ... }}`. "
                        "Then import the typed function (or wrap in a "
                        "react-query hook) and call it from this component. "
                        "Justified one-off? Add `// verifier:fetch-ok REASON` "
                        "to the line."
                    ),
                )
            )
        return findings


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = TsNoDirectFetchValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
