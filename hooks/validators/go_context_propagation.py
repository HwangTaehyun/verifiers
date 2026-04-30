"""V35: Go Context Propagation.

Checks that ``context.Background()`` and ``context.TODO()`` are not called
mid-flow inside ``internal/`` Go files (non-test).  Mid-flow background
contexts silently ignore the caller's deadline / cancellation, causing
resource leaks and misrouted timeouts.

Exemptions
----------
* Files containing ``signal.NotifyContext(`` — goroutine-root daemons.
* Lines that are ``var <name> = context.Background()`` at package scope
  (cron-like loops that legitimately hold a long-lived background context).
* Test files (``*_test.go``).
* Files outside ``internal/``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches context.Background() or context.TODO()
_BACKGROUND_CALL = re.compile(r"\bcontext\.(Background|TODO)\(\)")

# Matches package-scope var declaration: var <ident> = context.Background()
# (leading whitespace optional, but no leading identifier before "var")
_VAR_PKG_SCOPE = re.compile(r"^\s*var\s+\w+\s*=\s*context\.(Background|TODO)\(\)")


def _is_eligible(file_path: Path) -> bool:
    """Return True for non-test Go files under an ``internal/`` directory."""
    path_str = str(file_path)
    return file_path.suffix == ".go" and "/internal/" in path_str and not file_path.name.endswith("_test.go")


def _has_goroutine_root_exemption(text: str) -> bool:
    """True when the file is a goroutine / process root.

    ``signal.NotifyContext(`` indicates the file sets up OS-signal handling,
    which is the conventional goroutine-root pattern for long-lived daemons.
    """
    return "signal.NotifyContext(" in text


def _scan_file(file_path: Path) -> list[Finding]:
    try:
        text = file_path.read_text(errors="replace")
    except OSError:
        return []

    if _has_goroutine_root_exemption(text):
        return []

    findings: list[Finding] = []
    lines = text.splitlines()

    for i, line in enumerate(lines):
        m = _BACKGROUND_CALL.search(line)
        if not m:
            continue

        call_name = m.group(1)  # "Background" or "TODO"

        # Package-scope var declaration exemption
        if _VAR_PKG_SCOPE.match(line):
            continue

        line_no = i + 1
        findings.append(
            Finding(
                severity="error",
                file=str(file_path),
                line=line_no,
                rule="V35-MID-FLOW-BACKGROUND-CTX",
                message=(
                    f"`context.{call_name}()` called inside internal/. "
                    "Mid-flow contexts ignore caller cancellation — client disconnect "
                    "or upstream timeout is silently dropped. The function should accept "
                    "and propagate `ctx context.Context`."
                ),
                fix=(
                    "Replace with the caller's ctx:\n"
                    "    ctx, cancel := context.WithTimeout(parentCtx, 60*time.Second)\n"
                    "    defer cancel()\n"
                    "If the function legitimately runs in its own background goroutine (cron, "
                    "worker loop), wire `signal.NotifyContext` at the goroutine root and "
                    "document the exemption with `//nolint:V35` + comment."
                ),
            )
        )

    return findings


class GoContextPropagationValidator(BaseValidator):
    """V35: Go Context Propagation."""

    id = "V35-go-context-propagation"
    name = "Go Context Propagation"
    file_patterns: list[str] = [
        "server/internal/**/*.go",
        "**/internal/**/*.go",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): scan the single edited file."""
        path = Path(file_path)
        if not _is_eligible(path):
            return []
        return _scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): walk all internal/**/*.go under server_dir."""
        if ctx.server_dir is None:
            return []
        findings: list[Finding] = []
        for candidate in ctx.server_dir.rglob("*.go"):
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
    validator = GoContextPropagationValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
