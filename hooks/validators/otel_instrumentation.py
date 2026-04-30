"""V49: OpenTelemetry Instrumentation.

Checks that a Go project has ``go.opentelemetry.io/otel`` declared as a
direct dependency in go.mod (V49-NO-OTEL-SDK) and that at least one
``cmd/**/*.go`` file (non-test) imports ``otelhttp`` (V49-OTEL-NOT-WIRED).

Two-part check rationale:
  1. go.mod must declare the root ``go.opentelemetry.io/otel`` package — not
     a sub-package like ``go.opentelemetry.io/otel/trace``.
  2. The HTTP mux must actually be wrapped; having the SDK in go.mod but never
     calling ``otelhttp.NewHandler`` leaves the server untraced.

Design choices:
  - ``*_test.go`` files are skipped for the otelhttp check — test-only imports
    do not satisfy the production wiring requirement.
  - If no ``cmd/`` directory exists at all under the discovered root, we still
    emit V49-OTEL-NOT-WIRED (the wiring is absent by definition).
  - Only the root module ``go.opentelemetry.io/otel`` (followed by whitespace
    and a version) is accepted; sub-packages alone (e.g. ``/otel/trace``) are
    not sufficient because they don't pull in the SDK initialisation surface.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches the root OTel SDK module in a go.mod require block.
# Requires exactly ``go.opentelemetry.io/otel`` followed by whitespace then ``v``
# so that sub-packages (``/otel/trace``, ``/otel/exporters/...``) do NOT match.
_OTEL_ROOT_MODULE = re.compile(r"go\.opentelemetry\.io/otel\s+v")


def _find_go_mod(ctx: ProjectContext) -> Path | None:
    """Return path to go.mod, preferring server_dir then project root."""
    if ctx.server_dir is not None:
        candidate = ctx.server_dir / "go.mod"
        if candidate.exists():
            return candidate
    # Fallback: project root
    root_candidate = ctx.project_root / "go.mod"
    if root_candidate.exists():
        return root_candidate
    return None


def _has_otel_root(go_mod_text: str) -> bool:
    """Return True if go.mod contains the root OTel SDK direct dependency."""
    return bool(_OTEL_ROOT_MODULE.search(go_mod_text))


def _cmd_dir(go_mod_path: Path) -> Path:
    """Return the cmd/ directory sibling to go.mod."""
    return go_mod_path.parent / "cmd"


def _has_otelhttp_in_cmd(go_mod_path: Path) -> bool:
    """Return True if any non-test .go file under cmd/ contains ``otelhttp``."""
    cmd = _cmd_dir(go_mod_path)
    if not cmd.exists():
        return False
    for go_file in cmd.rglob("*.go"):
        if go_file.name.endswith("_test.go"):
            continue
        try:
            content = go_file.read_text(errors="replace")
        except OSError:
            continue
        if "otelhttp" in content:
            return True
    return False


def _all_checks(go_mod_path: Path) -> list[Finding]:
    """Run both V49 checks against the resolved go.mod path."""
    findings: list[Finding] = []
    try:
        go_mod_text = go_mod_path.read_text(errors="replace")
    except OSError:
        return []

    if not _has_otel_root(go_mod_text):
        findings.append(
            Finding(
                severity="warning",
                file=str(go_mod_path),
                rule="V49-NO-OTEL-SDK",
                message=(
                    "go.mod has no `go.opentelemetry.io/otel` direct dependency. "
                    "Production has zero distributed tracing — DB query hotspots and "
                    "Connect-RPC handler latency are invisible at scale."
                ),
                fix=(
                    "Add to go.mod:\n"
                    "  go.opentelemetry.io/otel\n"
                    "  go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc\n"
                    "  go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp\n"
                    "  github.com/exaring/otelpgx  // for pgx/v5 query spans\n"
                    "Run `go mod tidy` then wire `otelhttp.NewHandler(mux, ...)` in cmd/server/main.go."
                ),
            )
        )
        # SDK absent — wiring check is not meaningful; still return early.
        return findings

    # SDK is present; verify that cmd/**/*.go actually wires otelhttp.
    if not _has_otelhttp_in_cmd(go_mod_path):
        findings.append(
            Finding(
                severity="warning",
                file=str(go_mod_path),
                rule="V49-OTEL-NOT-WIRED",
                message=(
                    "go.opentelemetry.io/otel is in go.mod but no cmd/**/*.go file imports otelhttp. "
                    "SDK is declared but the HTTP mux isn't traced."
                ),
                fix=(
                    'Wrap your mux: `otelHandler := otelhttp.NewHandler(mux, "server")`. '
                    "Set propagator: `otel.SetTextMapPropagator(propagation.TraceContext{})`."
                ),
            )
        )

    return findings


class OtelInstrumentationValidator(BaseValidator):
    """V49: OpenTelemetry Instrumentation."""

    id = "V49-otel-instrumentation"
    name = "OpenTelemetry Instrumentation"
    file_patterns: list[str] = [
        "**/go.mod",
        "server/cmd/**/*.go",
        "**/cmd/**/*.go",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): run full check triggered by an edit."""
        go_mod_path = _find_go_mod(ctx)
        if go_mod_path is None:
            return []
        return _all_checks(go_mod_path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): project-level check."""
        go_mod_path = _find_go_mod(ctx)
        if go_mod_path is None:
            return []
        return _all_checks(go_mod_path)


# ── Standalone execution ──────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = OtelInstrumentationValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
