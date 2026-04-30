"""V55: Error Tracking SDK Presence (Sentry / GlitchTip).

Checks that a Go project has ``getsentry/sentry-go`` declared in server/go.mod
(V55-NO-GO-ERROR-TRACKING) and that the web project has a Sentry JavaScript SDK
(``@sentry/react``, ``@sentry/browser``, ``@sentry/nextjs``, or ``@sentry/vue``)
in web/package.json (V55-NO-WEB-ERROR-TRACKING).

Design choices:
  - Go check only fires when server/go.mod exists AND at least one .go file lives
    under server/internal/ — an empty starter without business logic should not
    be forced to add error tracking yet.
  - Web check only fires when web/package.json exists.
  - Both ``dependencies`` and ``devDependencies`` satisfy the web check because
    Sentry SDKs are tree-shaken at build time and legitimately live in either bucket.
  - Malformed package.json is caught and logged; the validator returns no findings
    rather than crashing.
  - Neither server/ nor web/ present → return [] (project is not applicable).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

logger = logging.getLogger(__name__)

# Matches getsentry/sentry-go in a go.mod require block.
_SENTRY_GO_MODULE = re.compile(r"github\.com/getsentry/sentry-go\b")

# Accepted Sentry JavaScript SDK package names.
_SENTRY_JS_PACKAGES = frozenset(
    [
        "@sentry/react",
        "@sentry/browser",
        "@sentry/nextjs",
        "@sentry/vue",
    ]
)


def _find_go_mod(ctx: ProjectContext) -> Path | None:
    """Return path to server/go.mod if it exists."""
    if ctx.server_dir is not None:
        candidate = ctx.server_dir / "go.mod"
        if candidate.exists():
            return candidate
    # Fallback: any go.mod directly under <root>/server/
    server_candidate = ctx.project_root / "server" / "go.mod"
    if server_candidate.exists():
        return server_candidate
    return None


def _has_internal_go_files(go_mod_path: Path) -> bool:
    """Return True if any .go file exists under the sibling internal/ directory."""
    internal = go_mod_path.parent / "internal"
    if not internal.exists():
        return False
    return any(internal.rglob("*.go"))


def _find_package_json(ctx: ProjectContext) -> Path | None:
    """Return path to web/package.json if it exists."""
    web_candidate = ctx.project_root / "web" / "package.json"
    if web_candidate.exists():
        return web_candidate
    return None


def _check(ctx: ProjectContext) -> list[Finding]:
    """Run all V55 checks and return findings."""
    findings: list[Finding] = []

    # ── Go check ──────────────────────────────────────────────────────────────
    go_mod_path = _find_go_mod(ctx)
    if go_mod_path is not None:
        if _has_internal_go_files(go_mod_path):
            try:
                go_mod_text = go_mod_path.read_text(errors="replace")
            except OSError:
                go_mod_text = ""
            if not _SENTRY_GO_MODULE.search(go_mod_text):
                findings.append(
                    Finding(
                        severity="error",
                        file=str(go_mod_path),
                        rule="V55-NO-GO-ERROR-TRACKING",
                        message=(
                            "server/go.mod has no error-tracking SDK (`getsentry/sentry-go`). "
                            "Production panics + handler-level errors are invisible until users complain. "
                            "Medical/finance projects need structured error capture for compliance trails."
                        ),
                        fix=(
                            "Add to server/:\n"
                            "  go get github.com/getsentry/sentry-go@latest\n"
                            "Initialize in cmd/server/main.go:\n"
                            '  sentry.Init(sentry.ClientOptions{ Dsn: os.Getenv("SENTRY_DSN"), TracesSampleRate: 0.1 })\n'
                            "  defer sentry.Flush(2 * time.Second)\n"
                            "Wrap mux: handler := sentryhttp.New(sentryhttp.Options{}).Handle(mux)"
                        ),
                    )
                )

    # ── Web check ─────────────────────────────────────────────────────────────
    package_json_path = _find_package_json(ctx)
    if package_json_path is not None:
        try:
            pkg = json.loads(package_json_path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.exception("V55: failed to parse %s: %s", package_json_path, exc)
            pkg = None

        if pkg is not None:
            deps: dict[str, str] = {}
            deps.update(pkg.get("dependencies") or {})
            deps.update(pkg.get("devDependencies") or {})
            has_sentry = bool(_SENTRY_JS_PACKAGES & set(deps.keys()))
            if not has_sentry:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(package_json_path),
                        rule="V55-NO-WEB-ERROR-TRACKING",
                        message=(
                            "web/package.json has no error-tracking SDK (`@sentry/react` etc.). "
                            "Frontend errors (unhandled promise rejections, React boundary catches) are invisible."
                        ),
                        fix=(
                            "Run in web/:\n"
                            "  bun add @sentry/react\n"
                            "Initialize in web/src/main.tsx (before React render):\n"
                            "  import * as Sentry from '@sentry/react';\n"
                            "  Sentry.init({ dsn: import.meta.env.VITE_SENTRY_DSN, integrations: [Sentry.browserTracingIntegration()] });"
                        ),
                    )
                )

    return findings


class ErrorTrackingSdkValidator(BaseValidator):
    """V55: Error Tracking SDK Presence (Sentry / GlitchTip)."""

    id = "V55-error-tracking-sdk"
    name = "Error Tracking SDK Presence (Sentry / GlitchTip)"
    file_patterns: list[str] = [
        "server/go.mod",
        "**/go.mod",
        "web/package.json",
        "**/package.json",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): run full check triggered by an edit."""
        return _check(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): project-level check."""
        return _check(ctx)


# ── Standalone execution ──────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = ErrorTrackingSdkValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
