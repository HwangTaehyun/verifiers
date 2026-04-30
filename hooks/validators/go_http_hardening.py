"""V36: Go HTTP Server Hardening.

Checks that every ``&http.Server{...}`` literal in cmd/**/main.go sets
both ``ReadHeaderTimeout`` and ``WriteTimeout`` (V36-NO-HTTP-TIMEOUTS),
and that the file contains graceful-shutdown wiring via
``signal.NotifyContext`` or ``srv.Shutdown`` (V36-NO-GRACEFUL-SHUTDOWN).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches &http.Server{ or http.Server{
_HTTP_SERVER_LITERAL = re.compile(r"&?http\.Server\s*\{")

# Graceful shutdown indicators anywhere in the file
_GRACEFUL_SHUTDOWN = re.compile(r"signal\.NotifyContext\s*\(|srv\.Shutdown\s*\(|server\.Shutdown\s*\(")


def _is_eligible(file_path: Path) -> bool:
    """Only cmd/**/main.go files (not under gen/)."""
    path_str = str(file_path)
    return file_path.name == "main.go" and "/cmd/" in path_str and "/gen/" not in path_str


def _extract_struct_body(src: str, start_pos: int) -> str:
    """Extract text from the opening ``{`` through its matching ``}``.

    ``start_pos`` should be at or before the ``{``. Scans forward to find
    the first ``{`` then tracks brace depth. Returns the full substring
    including both braces, or a 2000-char fallback if unbalanced.
    """
    brace_depth = 0
    in_literal = False
    for i in range(start_pos, len(src)):
        if src[i] == "{":
            brace_depth += 1
            in_literal = True
        elif src[i] == "}" and in_literal:
            brace_depth -= 1
            if brace_depth == 0:
                return src[start_pos : i + 1]
    return src[start_pos : start_pos + 2000]


def _check_http_timeouts(file_path: Path, src: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _HTTP_SERVER_LITERAL.finditer(src):
        body = _extract_struct_body(src, match.start())
        has_read_header = "ReadHeaderTimeout:" in body
        has_write = "WriteTimeout:" in body
        if not (has_read_header and has_write):
            line_no = src[: match.start()].count("\n") + 1
            findings.append(
                Finding(
                    severity="error",
                    file=str(file_path),
                    line=line_no,
                    rule="V36-NO-HTTP-TIMEOUTS",
                    message="http.Server literal missing ReadHeaderTimeout and/or WriteTimeout",
                    fix=(
                        "Add `ReadHeaderTimeout: 5*time.Second, WriteTimeout: 60*time.Second,"
                        " IdleTimeout: 120*time.Second` to the struct literal. Also wire"
                        " signal.NotifyContext + server.Shutdown for graceful termination."
                    ),
                )
            )
    return findings


def _check_graceful_shutdown(file_path: Path, src: str) -> list[Finding]:
    # Only warn when the file actually creates an http.Server
    if not _HTTP_SERVER_LITERAL.search(src):
        return []
    if _GRACEFUL_SHUTDOWN.search(src):
        return []
    return [
        Finding(
            severity="warning",
            file=str(file_path),
            rule="V36-NO-GRACEFUL-SHUTDOWN",
            message=(
                "http.Server created but no signal.NotifyContext / server.Shutdown detected;"
                " in-flight requests may be interrupted on process stop"
            ),
            fix=(
                "Wire graceful shutdown: `ctx, cancel := signal.NotifyContext(context.Background(),"
                " os.Interrupt, syscall.SIGTERM)` and call `server.Shutdown(ctx)` in a goroutine"
                " that waits on `<-ctx.Done()`."
            ),
        )
    ]


def _scan_file(file_path: Path) -> list[Finding]:
    try:
        src = file_path.read_text(errors="replace")
    except OSError:
        return []
    findings: list[Finding] = []
    findings.extend(_check_http_timeouts(file_path, src))
    findings.extend(_check_graceful_shutdown(file_path, src))
    return findings


class GoHttpHardeningValidator(BaseValidator):
    """V36: Go HTTP Server Hardening."""

    id = "V36-go-http-hardening"
    name = "Go HTTP Server Hardening"
    file_patterns: list[str] = [
        "server/cmd/**/main.go",
        "**/cmd/**/main.go",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): scan the single edited main.go."""
        path = Path(file_path)
        if not _is_eligible(path):
            return []
        return _scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): walk all cmd/*/main.go under server_dir."""
        if ctx.server_dir is None:
            return []
        findings: list[Finding] = []
        for candidate in ctx.server_dir.rglob("main.go"):
            if _is_eligible(candidate):
                findings.extend(_scan_file(candidate))
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoHttpHardeningValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
