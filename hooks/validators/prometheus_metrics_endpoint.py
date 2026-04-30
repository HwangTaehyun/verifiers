"""V56: Prometheus Metrics Endpoint Presence.

Checks that a Go project:
  1. Declares ``github.com/prometheus/client_golang`` in go.mod.
  2. Registers a ``/metrics`` route in at least one non-test ``cmd/**/*.go`` file.

Two-step check rationale:
  1. go.mod must declare ``github.com/prometheus/client_golang`` — without the
     SDK the process can't expose Prometheus metrics at all.
  2. The SDK alone is not enough; a ``/metrics`` route must actually be wired
     so the Prometheus scraper has an endpoint to call.

Design choices:
  - ``*_test.go`` files are excluded — test-only route registrations do not
    satisfy the production wiring requirement.
  - If ``server/`` does not exist OR no ``cmd/**/*.go`` exists the check
    returns [] (not applicable — may be a worker-only project with no HTTP
    server).
  - V56 is complementary to V49 (OTel traces). V49 enforces distributed
    tracing; V56 enforces metrics (the RED method: Rate / Errors / Duration).
    They address separate observability concerns and should both be present.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Matches the prometheus client_golang module in a go.mod require block.
_PROM_SDK_RE = re.compile(r"github\.com/prometheus/client_golang")

# Route registration patterns that register the /metrics path.
# Covers: mux.Handle, mux.HandleFunc, r.Get, r.Handle, http.Handle (chi / stdlib)
_METRICS_ROUTE_RE = re.compile(
    r"(?:mux|r|router|http|s|srv)"
    r'\.(?:Handle(?:Func)?|Get|Post)\s*\(\s*"/metrics"'
)

# Fallback: any string literal "/metrics" in a file that imports client_golang.
_METRICS_LITERAL_RE = re.compile(r'"/metrics"')
_PROM_IMPORT_RE = re.compile(r'"github\.com/prometheus/client_golang')


def _find_go_mod(ctx: ProjectContext) -> Path | None:
    """Return path to go.mod, preferring server_dir then project root."""
    if ctx.server_dir is not None:
        candidate = ctx.server_dir / "go.mod"
        if candidate.exists():
            return candidate
    # Fallback: look for server/go.mod relative to project_root
    server_candidate = ctx.project_root / "server" / "go.mod"
    if server_candidate.exists():
        return server_candidate
    root_candidate = ctx.project_root / "go.mod"
    if root_candidate.exists():
        return root_candidate
    return None


def _has_prometheus_sdk(go_mod_text: str) -> bool:
    """Return True if go.mod contains the prometheus/client_golang dependency."""
    return bool(_PROM_SDK_RE.search(go_mod_text))


def _cmd_dir(go_mod_path: Path) -> Path:
    """Return the cmd/ directory sibling to go.mod."""
    return go_mod_path.parent / "cmd"


def _has_metrics_route_in_cmd(go_mod_path: Path) -> bool:
    """Return True if any non-test .go file under cmd/ registers /metrics."""
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
        # Direct route-registration match
        if _METRICS_ROUTE_RE.search(content):
            return True
        # Fallback: file imports client_golang AND contains "/metrics" literal
        if _PROM_IMPORT_RE.search(content) and _METRICS_LITERAL_RE.search(content):
            return True
    return False


def _first_cmd_go_file(go_mod_path: Path) -> Path | None:
    """Return the first non-test .go file under cmd/, or None."""
    cmd = _cmd_dir(go_mod_path)
    if not cmd.exists():
        return None
    for go_file in sorted(cmd.rglob("*.go")):
        if not go_file.name.endswith("_test.go"):
            return go_file
    return None


def _check(ctx: ProjectContext) -> list[Finding]:
    """Run both V56 checks against the resolved go.mod path."""
    go_mod_path = _find_go_mod(ctx)
    if go_mod_path is None:
        return []

    # Determine whether cmd/**/*.go exists (not applicable for worker-only repos)
    cmd = _cmd_dir(go_mod_path)
    has_cmd_go = cmd.exists() and any(f for f in cmd.rglob("*.go") if not f.name.endswith("_test.go"))
    if not has_cmd_go:
        return []

    try:
        go_mod_text = go_mod_path.read_text(errors="replace")
    except OSError:
        return []

    findings: list[Finding] = []

    if not _has_prometheus_sdk(go_mod_text):
        findings.append(
            Finding(
                severity="warning",
                file=str(go_mod_path),
                rule="V56-NO-PROMETHEUS-SDK",
                message=(
                    "server/go.mod has no `prometheus/client_golang`. Without metrics SDK, "
                    "RED-method instrumentation (Rate / Errors / Duration) is impossible. "
                    "V49 (OTel) gives traces; V56 enforces metrics — separate concern."
                ),
                fix=(
                    "Add to server/:\n"
                    "  go get github.com/prometheus/client_golang/prometheus/promhttp\n"
                    "Then register /metrics endpoint in cmd/server/main.go:\n"
                    '  mux.Handle("/metrics", promhttp.Handler())\n'
                    "See https://prometheus.io/docs/practices/naming/ for metric naming conventions."
                ),
            )
        )
        # SDK absent — route check is not meaningful.
        return findings

    # SDK is present; verify /metrics is actually wired.
    if not _has_metrics_route_in_cmd(go_mod_path):
        first_go = _first_cmd_go_file(go_mod_path)
        findings.append(
            Finding(
                severity="warning",
                file=str(first_go) if first_go else str(go_mod_path),
                rule="V56-PROMETHEUS-NOT-WIRED",
                message=(
                    "prometheus/client_golang is in go.mod but `/metrics` endpoint isn't registered "
                    "in any cmd/**/*.go. SDK present but Prometheus scraper has nothing to scrape."
                ),
                fix=(
                    "Register the handler in main.go after creating the mux:\n"
                    '  import "github.com/prometheus/client_golang/prometheus/promhttp"\n'
                    '  mux.Handle("/metrics", promhttp.Handler())\n'
                    "Place behind a separate listener or auth middleware if /metrics shouldn't be public."
                ),
            )
        )

    return findings


class PrometheusMetricsEndpointValidator(BaseValidator):
    """V56: Prometheus Metrics Endpoint Presence."""

    id = "V56-prometheus-metrics-endpoint"
    name = "Prometheus Metrics Endpoint Presence"
    file_patterns: list[str] = [
        "server/go.mod",
        "**/go.mod",
        "server/cmd/**/*.go",
        "**/cmd/**/*.go",
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
    validator = PrometheusMetricsEndpointValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
