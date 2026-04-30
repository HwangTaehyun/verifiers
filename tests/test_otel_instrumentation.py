"""Tests for V49 — OpenTelemetry Instrumentation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hooks.validators.otel_instrumentation import OtelInstrumentationValidator
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> OtelInstrumentationValidator:
    return OtelInstrumentationValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


_GO_MOD_WITH_OTEL = """\
module testproject

go 1.21

require (
    go.opentelemetry.io/otel v1.24.0
    go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc v1.24.0
    go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.49.0
)
"""

_GO_MOD_WITHOUT_OTEL = """\
module testproject

go 1.21

require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
)
"""

_MAIN_GO_WITH_OTELHTTP = """\
package main

import (
    "net/http"
    "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

func main() {
    mux := http.NewServeMux()
    otelHandler := otelhttp.NewHandler(mux, "server")
    http.ListenAndServe(":8080", otelHandler)
}
"""

_MAIN_GO_WITHOUT_OTELHTTP = """\
package main

import (
    "net/http"
)

func main() {
    mux := http.NewServeMux()
    http.ListenAndServe(":8080", mux)
}
"""


# ── 1. Full setup passes ─────────────────────────────────────────────────────


class TestFullSetupPasses:
    def test_otel_in_go_mod_and_otelhttp_imported_passes(self, validator, tmp_project, project_ctx):
        """go.mod has root otel + cmd file imports otelhttp → no findings."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_OTEL)
        _write(tmp_project / "server" / "cmd" / "server" / "main.go", _MAIN_GO_WITH_OTELHTTP)
        findings = validator.validate_project(project_ctx)
        assert findings == []


# ── 2. No go.mod → no findings ───────────────────────────────────────────────


class TestNoGoModNoFindings:
    def test_no_go_mod_no_findings(self, validator, tmp_path):
        """Empty project (no go.mod at all) → not a Go project, skip silently."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 3. go.mod exists but no otel → V49-NO-OTEL-SDK ──────────────────────────


class TestGoModNoOtelWarns:
    def test_go_mod_no_otel_warns(self, validator, tmp_project, project_ctx):
        """go.mod present but missing otel SDK → V49-NO-OTEL-SDK warning."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITHOUT_OTEL)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-NO-OTEL-SDK" in rules
        sdk_finding = next(f for f in findings if f.rule == "V49-NO-OTEL-SDK")
        assert sdk_finding.severity == "warning"


# ── 4. otel in go.mod but no otelhttp in cmd → V49-OTEL-NOT-WIRED ───────────


class TestOtelInGoModButNoOtelhttpInCmdWarns:
    def test_otel_in_go_mod_but_no_otelhttp_in_cmd_warns(self, validator, tmp_project, project_ctx):
        """SDK declared in go.mod but cmd/ has no otelhttp import → V49-OTEL-NOT-WIRED."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_OTEL)
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            _MAIN_GO_WITHOUT_OTELHTTP,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-OTEL-NOT-WIRED" in rules
        assert "V49-NO-OTEL-SDK" not in rules
        wired_finding = next(f for f in findings if f.rule == "V49-OTEL-NOT-WIRED")
        assert wired_finding.severity == "warning"


# ── 5. otelhttp only in *_test.go → still flag V49-OTEL-NOT-WIRED ───────────


class TestOtelhttpInTestFileDoesNotSatisfy:
    def test_otelhttp_in_test_file_does_not_satisfy(self, validator, tmp_project, project_ctx):
        """otelhttp in a _test.go file must not count as production wiring."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_OTEL)
        _write(
            tmp_project / "server" / "cmd" / "server" / "main_test.go",
            """\
package main_test

import (
    "testing"
    "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

func TestOtelSetup(t *testing.T) {
    _ = otelhttp.NewHandler(nil, "test")
}
""",
        )
        # Non-test main.go without otelhttp
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            _MAIN_GO_WITHOUT_OTELHTTP,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-OTEL-NOT-WIRED" in rules


# ── 6. otelhttp in internal/ does not satisfy wiring requirement ─────────────


class TestOtelhttpInInternalDoesNotSatisfy:
    def test_otelhttp_in_internal_does_not_satisfy(self, validator, tmp_project, project_ctx):
        """otelhttp import under internal/ must not satisfy the cmd/ wiring check."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_OTEL)
        _write(
            tmp_project / "server" / "internal" / "middleware" / "otel.go",
            """\
package middleware

import "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

func Wrap(h http.Handler) http.Handler {
    return otelhttp.NewHandler(h, "internal")
}
""",
        )
        # cmd/ exists but no otelhttp there
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            _MAIN_GO_WITHOUT_OTELHTTP,
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-OTEL-NOT-WIRED" in rules


# ── 7. Sub-packages alone do not satisfy V49-NO-OTEL-SDK ────────────────────


class TestOtelSubpackagesDoNotMatch:
    def test_otel_v_prefix_other_packages_do_not_match(self, validator, tmp_project, project_ctx):
        """go.opentelemetry.io/otel/trace alone is not the root SDK — must flag."""
        _write(
            tmp_project / "server" / "go.mod",
            """\
module testproject

go 1.21

require (
    go.opentelemetry.io/otel/trace v1.24.0
    go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc v1.24.0
)
""",
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-NO-OTEL-SDK" in rules


# ── 8. No cmd/ directory → V49-OTEL-NOT-WIRED (wiring absent by definition) ──


class TestNoCmdDirSkipsWiringCheck:
    def test_no_cmd_dir_emits_wired_warning(self, validator, tmp_project, project_ctx):
        """go.mod has otel but there is no cmd/ dir → V49-OTEL-NOT-WIRED emitted.

        Rationale: if there's no cmd/ at all, the HTTP mux is definitely not
        instrumented in a cmd binary. We flag it rather than silently skipping
        so the team knows they need to add the wiring when they add a main.go.
        """
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_OTEL)
        # Ensure cmd/ does NOT exist
        cmd_dir = tmp_project / "server" / "cmd"
        if cmd_dir.exists():
            import shutil

            shutil.rmtree(cmd_dir)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V49-OTEL-NOT-WIRED" in rules


# ── 9. validate_file runs full check (Tier 2 path) ───────────────────────────


class TestValidateFileRunsFullCheck:
    def test_validate_file_runs_full_check(self, validator, tmp_project, project_ctx):
        """Tier 2: editing a go.mod without otel should emit V49-NO-OTEL-SDK."""
        go_mod_path = tmp_project / "server" / "go.mod"
        _write(go_mod_path, _GO_MOD_WITHOUT_OTEL)
        findings = validator.validate_file(project_ctx, str(go_mod_path))
        rules = [f.rule for f in findings]
        assert "V49-NO-OTEL-SDK" in rules

    def test_validate_file_passes_full_setup(self, validator, tmp_project, project_ctx):
        """Tier 2: editing go.mod with otel + otelhttp in cmd → no findings."""
        go_mod_path = tmp_project / "server" / "go.mod"
        _write(go_mod_path, _GO_MOD_WITH_OTEL)
        _write(
            tmp_project / "server" / "cmd" / "server" / "main.go",
            _MAIN_GO_WITH_OTELHTTP,
        )
        findings = validator.validate_file(project_ctx, str(go_mod_path))
        assert findings == []
