"""Tests for V56 — Prometheus Metrics Endpoint Presence."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.prometheus_metrics_endpoint import PrometheusMetricsEndpointValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> PrometheusMetricsEndpointValidator:
    return PrometheusMetricsEndpointValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


_GO_MOD_WITH_PROM = """\
module testproject

go 1.21

require (
    github.com/prometheus/client_golang v1.19.0
    github.com/lib/pq v1.10.0
)
"""

_GO_MOD_WITHOUT_PROM = """\
module testproject

go 1.21

require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
)
"""

_MAIN_GO_WITH_METRICS = """\
package main

import (
    "net/http"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
    mux := http.NewServeMux()
    mux.Handle("/metrics", promhttp.Handler())
    http.ListenAndServe(":8080", mux)
}
"""

_MAIN_GO_WITHOUT_METRICS = """\
package main

import (
    "net/http"
)

func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(200)
    })
    http.ListenAndServe(":8080", mux)
}
"""

_MAIN_GO_CHI_METRICS = """\
package main

import (
    "net/http"
    "github.com/go-chi/chi/v5"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
    r := chi.NewRouter()
    r.Handle("/metrics", promhttp.Handler())
    http.ListenAndServe(":8080", r)
}
"""


# ── 1. SDK present + /metrics route → no findings ───────────────────────────


class TestSdkAndRoutePresentPasses:
    def test_sdk_and_route_present_passes(self, validator, tmp_project, project_ctx):
        """go.mod has client_golang AND main.go registers /metrics → no findings."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITH_METRICS)
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. No SDK → V56-NO-PROMETHEUS-SDK ───────────────────────────────────────


class TestNoSdkWarns:
    def test_no_sdk_warns(self, validator, tmp_project, project_ctx):
        """go.mod missing prometheus → V56-NO-PROMETHEUS-SDK warning."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITHOUT_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V56-NO-PROMETHEUS-SDK" in rules
        sdk_finding = next(f for f in findings if f.rule == "V56-NO-PROMETHEUS-SDK")
        assert sdk_finding.severity == "warning"
        # SDK absent → do not also emit NOT-WIRED
        assert "V56-PROMETHEUS-NOT-WIRED" not in rules


# ── 3. SDK present but no /metrics → V56-PROMETHEUS-NOT-WIRED ───────────────


class TestSdkPresentNoRouteWarns:
    def test_sdk_present_no_route_warns(self, validator, tmp_project, project_ctx):
        """go.mod has SDK but main.go has no /metrics → V56-PROMETHEUS-NOT-WIRED."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V56-PROMETHEUS-NOT-WIRED" in rules
        assert "V56-NO-PROMETHEUS-SDK" not in rules
        wired_finding = next(f for f in findings if f.rule == "V56-PROMETHEUS-NOT-WIRED")
        assert wired_finding.severity == "warning"


# ── 4. /metrics registered in non-main file → passes ────────────────────────


class TestSdkPresentRouteInOtherFilePasses:
    def test_sdk_present_route_in_other_file_passes(self, validator, tmp_project, project_ctx):
        """/metrics registered in handlers.go (not main.go) → no findings."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        # main.go has no /metrics
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        # handlers.go in same cmd/ tree registers /metrics
        _write(
            tmp_project / "server" / "cmd" / "server" / "handlers.go",
            """\
package main

import (
    "net/http"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func registerMetrics(mux *http.ServeMux) {
    mux.Handle("/metrics", promhttp.Handler())
}
""",
        )
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 5. /metrics only in _test.go → still warns ──────────────────────────────


class TestRouteInTestFileDoesNotSatisfy:
    def test_route_in_test_file_does_not_satisfy(self, validator, tmp_project, project_ctx):
        """Only a _test.go file registers /metrics → still emit V56-PROMETHEUS-NOT-WIRED."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        _write(
            tmp_project / "server" / "cmd" / "server" / "main_test.go",
            """\
package main_test

import (
    "net/http"
    "testing"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func TestMetricsEndpoint(t *testing.T) {
    mux := http.NewServeMux()
    mux.Handle("/metrics", promhttp.Handler())
}
""",
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V56-PROMETHEUS-NOT-WIRED" in rules


# ── 6. No server/ dir → no findings ─────────────────────────────────────────


class TestNoServerDirReturnsEmpty:
    def test_no_server_dir_returns_empty(self, validator, tmp_path):
        """No server/ directory → not applicable, return []."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 7. server/ exists but no cmd/ → no findings ─────────────────────────────


class TestNoCmdDirReturnsEmpty:
    def test_no_cmd_dir_returns_empty(self, validator, tmp_project, project_ctx):
        """server/ exists with go.mod but no cmd/ subdir → return []."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        # Ensure no cmd/ directory exists
        cmd_dir = tmp_project / "server" / "cmd"
        if cmd_dir.exists():
            import shutil

            shutil.rmtree(cmd_dir)
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 8. validate_file triggers full check (Tier 2 path) ──────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_triggers_check_no_sdk(self, validator, tmp_project, project_ctx):
        """Tier 2: editing a go.mod without prometheus should emit V56-NO-PROMETHEUS-SDK."""
        go_mod_path = tmp_project / "server" / "go.mod"
        _write(go_mod_path, _GO_MOD_WITHOUT_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        findings = validator.validate_file(project_ctx, str(go_mod_path))
        rules = [f.rule for f in findings]
        assert "V56-NO-PROMETHEUS-SDK" in rules

    def test_validate_file_passes_full_setup(self, validator, tmp_project, project_ctx):
        """Tier 2: editing go.mod with SDK + /metrics route → no findings."""
        go_mod_path = tmp_project / "server" / "go.mod"
        _write(go_mod_path, _GO_MOD_WITH_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITH_METRICS)
        findings = validator.validate_file(project_ctx, str(go_mod_path))
        assert findings == []


# ── 9. Chi router /metrics route → passes ───────────────────────────────────


class TestRouteWithChiRouterPasses:
    def test_route_with_chi_router_passes(self, validator, tmp_project, project_ctx):
        """`r.Handle("/metrics", ...)` chi-style route registration → no findings."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_PROM)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_CHI_METRICS)
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 10. Corrupt go.mod → no crash ───────────────────────────────────────────


class TestInvalidGoModHandledGracefully:
    def test_invalid_go_mod_handled_gracefully(self, validator, tmp_project, project_ctx):
        """Corrupt / unparseable go.mod → no crash, returns findings or empty."""
        _write(
            tmp_project / "server" / "go.mod",
            "this is not a valid go.mod @@@ !!!",
        )
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITHOUT_METRICS)
        # Should not raise; may return SDK-missing warning (corrupt mod has no prom dep)
        try:
            findings = validator.validate_project(project_ctx)
        except Exception as exc:
            pytest.fail(f"validate_project raised unexpectedly: {exc}")
        # A corrupt go.mod won't contain the prometheus dep, so we expect a warning
        # but not a crash.
        assert isinstance(findings, list)
