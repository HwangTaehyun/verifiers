"""Tests for V55 — Error Tracking SDK Presence (Sentry / GlitchTip)."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from hooks.validators.error_tracking_sdk import ErrorTrackingSdkValidator
from lib.project_context import ProjectContext


# ── Helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> ErrorTrackingSdkValidator:
    return ErrorTrackingSdkValidator()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


_GO_MOD_WITH_SENTRY = """\
module testproject

go 1.21

require (
    github.com/getsentry/sentry-go v0.27.0
    github.com/lib/pq v1.10.0
)
"""

_GO_MOD_WITHOUT_SENTRY = """\
module testproject

go 1.21

require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
)
"""

_INTERNAL_GO_FILE = """\
package handler

import "net/http"

func Health(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
}
"""


# ── 1. Go: go.mod has sentry-go → no V55-NO-GO finding ───────────────────────


class TestGoWithSentryPasses:
    def test_go_with_sentry_passes(self, validator, tmp_project, project_ctx):
        """go.mod has getsentry/sentry-go → no V55-NO-GO-ERROR-TRACKING."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITH_SENTRY)
        _write(tmp_project / "server" / "internal" / "handler.go", _INTERNAL_GO_FILE)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-GO-ERROR-TRACKING" not in rules


# ── 2. Go: go.mod missing sentry-go + internal has .go → V55-NO-GO ───────────


class TestGoWithoutSentryErrors:
    def test_go_without_sentry_errors(self, validator, tmp_project, project_ctx):
        """go.mod has no Sentry, server/internal has .go → V55-NO-GO-ERROR-TRACKING."""
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITHOUT_SENTRY)
        _write(tmp_project / "server" / "internal" / "handler.go", _INTERNAL_GO_FILE)
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-GO-ERROR-TRACKING" in rules
        go_finding = next(f for f in findings if f.rule == "V55-NO-GO-ERROR-TRACKING")
        assert go_finding.severity == "error"


# ── 3. No server/ dir → skip Go check ────────────────────────────────────────


class TestNoServerDirSkipsGoCheck:
    def test_no_server_dir_skips_go_check(self, validator, tmp_path):
        """Empty project with no server/ → no V55-NO-GO-ERROR-TRACKING."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-GO-ERROR-TRACKING" not in rules


# ── 4. server/go.mod exists but no internal/*.go → skip Go check ──────────────


class TestNoInternalFilesSkipsGoCheck:
    def test_no_internal_files_skips_go_check(self, validator, tmp_project, project_ctx):
        """server/go.mod exists but no .go files under internal/ → no V55-NO-GO finding.

        An empty starter project without business logic should not be forced to
        add error tracking yet.
        """
        _write(tmp_project / "server" / "go.mod", _GO_MOD_WITHOUT_SENTRY)
        # Ensure internal/ has no .go files (conftest creates the dir but no files)
        internal = tmp_project / "server" / "internal"
        for f in internal.rglob("*.go"):
            f.unlink()
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-GO-ERROR-TRACKING" not in rules


# ── 5. Web: package.json has @sentry/react in dependencies → passes ───────────


class TestWebWithSentryReactPasses:
    def test_web_with_sentry_react_passes(self, validator, tmp_project, project_ctx):
        """package.json has @sentry/react in dependencies → no V55-NO-WEB finding."""
        _write_json(
            tmp_project / "web" / "package.json",
            {"dependencies": {"@sentry/react": "^7.0.0", "react": "^18.0.0"}},
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" not in rules


# ── 6. Web: @sentry/browser also satisfies ───────────────────────────────────


class TestWebWithSentryBrowserPasses:
    def test_web_with_sentry_browser_passes(self, validator, tmp_project, project_ctx):
        """@sentry/browser in dependencies is an acceptable alternative SDK."""
        _write_json(
            tmp_project / "web" / "package.json",
            {"dependencies": {"@sentry/browser": "^7.0.0"}},
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" not in rules


# ── 7. Web: package.json has no Sentry → V55-NO-WEB-ERROR-TRACKING ───────────


class TestWebWithoutSentryErrors:
    def test_web_without_sentry_errors(self, validator, tmp_project, project_ctx):
        """package.json has no Sentry SDK → V55-NO-WEB-ERROR-TRACKING error."""
        _write_json(
            tmp_project / "web" / "package.json",
            {"dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"}},
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" in rules
        web_finding = next(f for f in findings if f.rule == "V55-NO-WEB-ERROR-TRACKING")
        assert web_finding.severity == "error"


# ── 8. Web: Sentry in devDependencies counts ──────────────────────────────────


class TestWebSentryInDevDependenciesPasses:
    def test_web_sentry_in_devDependencies_passes(self, validator, tmp_project, project_ctx):
        """@sentry/react in devDependencies also satisfies (build-time inclusion)."""
        _write_json(
            tmp_project / "web" / "package.json",
            {
                "dependencies": {"react": "^18.0.0"},
                "devDependencies": {"@sentry/react": "^7.0.0"},
            },
        )
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" not in rules


# ── 9. No web/ dir → skip web check ─────────────────────────────────────────


class TestNoWebDirSkipsWebCheck:
    def test_no_web_dir_skips_web_check(self, validator, tmp_path):
        """No web/ directory → no V55-NO-WEB-ERROR-TRACKING."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" not in rules


# ── 10. Neither server nor web → empty findings ──────────────────────────────


class TestBothDirsMissingReturnsEmpty:
    def test_both_dirs_missing_returns_empty(self, validator, tmp_path):
        """Neither server/ nor web/ → no findings at all."""
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)
        findings = validator.validate_project(ctx)
        assert findings == []


# ── 11. Malformed package.json → no crash ────────────────────────────────────


class TestInvalidJsonHandledGracefully:
    def test_invalid_json_handled_gracefully(self, validator, tmp_project, project_ctx):
        """Malformed package.json must not crash the validator — returns no web finding."""
        pkg_json = tmp_project / "web" / "package.json"
        pkg_json.parent.mkdir(parents=True, exist_ok=True)
        pkg_json.write_text("{ this is not valid json !!!")
        # Should not raise; finding may or may not be emitted but no exception
        findings = validator.validate_project(project_ctx)
        rules = [f.rule for f in findings]
        assert "V55-NO-WEB-ERROR-TRACKING" not in rules


# ── 12. validate_file runs both checks (Tier 2 path) ─────────────────────────


class TestValidateFileRunsBothChecks:
    def test_validate_file_runs_both_checks(self, validator, tmp_project, project_ctx):
        """Tier 2: editing go.mod without sentry + package.json without sentry → both findings."""
        go_mod_path = tmp_project / "server" / "go.mod"
        _write(go_mod_path, _GO_MOD_WITHOUT_SENTRY)
        _write(tmp_project / "server" / "internal" / "handler.go", _INTERNAL_GO_FILE)
        _write_json(
            tmp_project / "web" / "package.json",
            {"dependencies": {"react": "^18.0.0"}},
        )
        findings = validator.validate_file(project_ctx, str(go_mod_path))
        rules = [f.rule for f in findings]
        assert "V55-NO-GO-ERROR-TRACKING" in rules
        assert "V55-NO-WEB-ERROR-TRACKING" in rules
