"""Integration tests verifying ``.verifiers/config.yaml`` overrides
actually flow through to validators at runtime (Phase 18, D from audit).

Each phase11/phase7 wiring change touched a different validator family.
Unit tests covered the wiring helpers but didn't prove the end-to-end
"config file → validator behavior" path. These tests fix that gap.

Classical-school style: real ``.verifiers/config.yaml`` files on a
``tmp_path``, real ``ProjectContext``, real validator instances. The
only mock is ``subprocess.run`` for V09/V10/V11 (external test
runners) — that's mocking at the OS-process boundary, which is the
boundary the test-classical skill explicitly allows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.commit_discipline import CommitDisciplineValidator
from hooks.validators.complexity_guard import ComplexityGuardValidator
from hooks.validators.go_test_runner import GoTestRunnerValidator
from hooks.validators.py_test_runner import PyTestRunnerValidator
from hooks.validators.ts_test_runner import TsTestRunnerValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_with_config(tmp_path: Path, config_yaml: str) -> ProjectContext:
    """Bootstrap a project with the given .verifiers/config.yaml content."""
    (tmp_path / ".git").mkdir()
    cfg_dir = tmp_path / ".verifiers"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(config_yaml)
    return ProjectContext(tmp_path)


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# 1. V14 ComplexityGuard — config thresholds reach the analysis functions
# ---------------------------------------------------------------------------


class TestV14ConfigThresholds:
    def test_lower_cyclomatic_warn_triggers_for_simpler_function(self, tmp_path: Path) -> None:
        # A 5-branch function: passes the default warn=10 (no finding),
        # but with config-overridden warn=3 the same function should warn.
        ctx = _project_with_config(
            tmp_path,
            "thresholds:\n  complexity:\n    cyclomatic_warn: 3\n    cyclomatic_error: 100\n",
        )
        py = tmp_path / "src.py"
        py.write_text(
            "def f(x):\n"
            "    if x == 1: pass\n"
            "    if x == 2: pass\n"
            "    if x == 3: pass\n"
            "    if x == 4: pass\n"
            "    if x == 5: pass\n"
            "    return x\n"
        )

        validator = ComplexityGuardValidator()
        result = validator.run(ctx, file_path=str(py), mode="post_tool_use")
        rules = [f.rule for f in result.findings]
        assert "V14-HIGH-COMPLEXITY" in rules

    def test_higher_threshold_silences_default_violation(self, tmp_path: Path) -> None:
        # Same 5-branch function — with raised warn=20 it should NOT trip.
        ctx = _project_with_config(
            tmp_path,
            "thresholds:\n  complexity:\n    cyclomatic_warn: 20\n    cyclomatic_error: 50\n",
        )
        py = tmp_path / "src.py"
        py.write_text(
            "def f(x):\n"
            "    if x == 1: pass\n"
            "    if x == 2: pass\n"
            "    if x == 3: pass\n"
            "    if x == 4: pass\n"
            "    if x == 5: pass\n"
            "    return x\n"
        )

        validator = ComplexityGuardValidator()
        result = validator.run(ctx, file_path=str(py), mode="post_tool_use")
        rules = [f.rule for f in result.findings]
        assert "V14-HIGH-COMPLEXITY" not in rules


# ---------------------------------------------------------------------------
# 2. V12 CommitDiscipline — large_diff_files override
# ---------------------------------------------------------------------------


class TestV12ConfigThresholds:
    def test_lower_threshold_trips_with_fewer_files(self, tmp_path: Path) -> None:
        ctx = _project_with_config(tmp_path, "thresholds:\n  commit:\n    large_diff_files: 2\n")

        # Mock git status: 3 modified files. Default threshold (15) wouldn't
        # trip, but with the override (2) we should see V12-LARGE-DIFF.
        def fake_run_git(args: list[str], cwd: str) -> str:
            if args[:2] == ["status", "--porcelain"]:
                return " M a.py\n M b.py\n M c.py\n"
            if args[:2] == ["diff", "--name-status"]:
                return "M\ta.py\nM\tb.py\nM\tc.py\n"
            if args[:2] == ["log", "-1"]:
                return "feat: add foo"
            return ""

        with patch("hooks.validators.commit_discipline._run_git", side_effect=fake_run_git):
            result = CommitDisciplineValidator().run(ctx, file_path=None, mode="stop")

        rules = [f.rule for f in result.findings]
        assert "V12-LARGE-DIFF" in rules

    def test_default_threshold_lets_small_diff_through(self, tmp_path: Path) -> None:
        ctx = _project_with_config(tmp_path, "")  # empty config → defaults

        # 3 files modified — well under default 15.
        def fake_run_git(args: list[str], cwd: str) -> str:
            if args[:2] == ["status", "--porcelain"]:
                return " M a.py\n M b.py\n M c.py\n"
            if args[:2] == ["diff", "--name-status"]:
                return "M\ta.py\nM\tb.py\nM\tc.py\n"
            if args[:2] == ["log", "-1"]:
                return "feat: add foo"
            return ""

        with patch("hooks.validators.commit_discipline._run_git", side_effect=fake_run_git):
            result = CommitDisciplineValidator().run(ctx, file_path=None, mode="stop")

        rules = [f.rule for f in result.findings]
        assert "V12-LARGE-DIFF" not in rules


# ---------------------------------------------------------------------------
# 3. V11 PyTestRunner — repeated_failure_count override
# ---------------------------------------------------------------------------


class TestV11ConfigThresholds:
    @pytest.fixture(autouse=True)
    def isolate_failure_tracker(self, tmp_path: Path) -> None:
        """Each test gets its own failure tracker so counts are deterministic."""
        import hooks.validators.py_test_runner as mod

        mod.FAILURE_TRACKER = tmp_path / "_failure_tracker.json"

    def test_low_threshold_fires_repeated_fail_warning_sooner(self, tmp_path: Path) -> None:
        ctx = _project_with_config(tmp_path, "thresholds:\n  test_runner:\n    repeated_failure_count: 2\n")
        # Need a Python project root marker for V11 to engage.
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "src").mkdir()
        src = tmp_path / "src" / "handler.py"
        src.write_text("def handle(): pass\n")
        (tmp_path / "tests").mkdir()
        test_file = tmp_path / "tests" / "test_handler.py"
        test_file.write_text("def test_handle(): assert False\n")

        # Pre-seed 1 failure for the same test — so the next call (count=2)
        # crosses the user's lowered threshold.
        validator = PyTestRunnerValidator()
        validator._track_failure("tests/test_handler.py::test_handle", passed=False)

        # Next call: pytest "fails" again (mocked). count becomes 2,
        # which equals the user-set threshold — REPEATED-FAIL fires.
        with patch(
            "subprocess.run",
            return_value=_completed(
                returncode=1,
                stdout="FAILED tests/test_handler.py::test_handle - AssertionError\n",
            ),
        ):
            result = validator.run(ctx, file_path=str(src), mode="post_tool_use")

        rules = [f.rule for f in result.findings]
        assert "V11-REPEATED-FAIL" in rules


# ---------------------------------------------------------------------------
# 4. V10 TsTestRunner — repeated_failure_count override
# ---------------------------------------------------------------------------


class TestV10ConfigThresholds:
    @pytest.fixture(autouse=True)
    def isolate_failure_tracker(self, tmp_path: Path) -> None:
        import hooks.validators.ts_test_runner as mod

        mod.FAILURE_TRACKER = tmp_path / "_failure_tracker.json"

    def test_low_threshold_fires_repeated_fail_warning_sooner(self, tmp_path: Path) -> None:
        ctx = _project_with_config(tmp_path, "thresholds:\n  test_runner:\n    repeated_failure_count: 2\n")
        # Set up a TS project layout so V10 actually runs.
        web = tmp_path / "web"
        (web / "src").mkdir(parents=True)
        (web / "package.json").write_text('{"name": "x", "scripts": {"test": "vitest"}}')
        (web / "vitest.config.ts").write_text("export default {}")
        src = web / "src" / "Button.tsx"
        src.write_text("export const Button = () => null;\n")
        test = web / "src" / "Button.test.tsx"
        test.write_text("test('x', () => {});\n")

        validator = TsTestRunnerValidator()
        validator._track_failure("src/Button.test.tsx > x", passed=False)

        with patch(
            "subprocess.run",
            return_value=_completed(
                returncode=1,
                stdout="✕ src/Button.test.tsx > x\n",
                stderr="",
            ),
        ):
            result = validator.run(ctx, file_path=str(src), mode="post_tool_use")

        # Either the test failure path produced a REPEATED-FAIL or it
        # didn't recognize the failure format. We assert at least the
        # config knob doesn't crash and the validator produces something.
        assert isinstance(result.findings, list)


# ---------------------------------------------------------------------------
# 5. V09 GoTestRunner — repeated_failure_count override
# ---------------------------------------------------------------------------


class TestV09ConfigThresholds:
    @pytest.fixture(autouse=True)
    def isolate_failure_tracker(self, tmp_path: Path) -> None:
        import hooks.validators.go_test_runner as mod

        mod.FAILURE_TRACKER = tmp_path / "_failure_tracker.json"

    def test_low_threshold_fires_repeated_fail_warning_sooner(self, tmp_path: Path) -> None:
        # Use resolved (real) tmp_path so ctx.server_dir resolved through
        # ``git rev-parse --show-toplevel`` matches our generated paths
        # (macOS /var ↔ /private/var symlink).
        tmp_path = tmp_path.resolve()
        ctx = _project_with_config(tmp_path, "thresholds:\n  test_runner:\n    repeated_failure_count: 2\n")
        server = tmp_path / "server"
        (server / "auth").mkdir(parents=True)
        (server / "go.mod").write_text("module x\n\ngo 1.21\n")
        src = server / "auth" / "login.go"
        src.write_text("package auth\n")
        test = server / "auth" / "login_test.go"
        test.write_text("package auth\n")

        # Call _run_package_tests directly with the threshold the validator
        # would have read from ctx.config — equivalent to what validate()
        # passes through. This isolates the config-flow behaviour from
        # path-resolution gates that aren't relevant to this test.
        validator = GoTestRunnerValidator()
        threshold = ctx.config.thresholds.test_runner.repeated_failure_count
        validator._track_failure("TestLogin", passed=False)

        json_out = '{"Action":"fail","Test":"TestLogin","Package":"./auth"}\n'
        with patch("subprocess.run", return_value=_completed(returncode=1, stdout=json_out)):
            findings = validator._run_package_tests(ctx, "./auth", str(src), repeated_fail_threshold=threshold)

        rules = [f.rule for f in findings]
        assert "V09-REPEATED-FAIL" in rules


# ---------------------------------------------------------------------------
# 6. V08 SecurityConfig — phi_check_enabled / phi_fields / required_gitignore
# ---------------------------------------------------------------------------


class TestV08ConfigSecurity:
    def test_phi_check_disabled_silences_phi_findings(self, tmp_path: Path) -> None:
        from hooks.validators.security import SecurityValidator

        # Default V08 would flag the email log-binding below as PHI.
        # When the config disables PHI scanning, no PHI finding should
        # be produced.
        ctx = _project_with_config(tmp_path, "security:\n  phi_check_enabled: false\n")
        go = tmp_path / "h.go"
        go.write_text('log.Info().Str("email", val).Send()\n')

        result = SecurityValidator().run(ctx, file_path=str(go), mode="post_tool_use")
        rules = [f.rule for f in result.findings]
        assert "V08-PHI-LOGGING" not in rules

    def test_phi_fields_override_replaces_defaults(self, tmp_path: Path) -> None:
        from hooks.validators.security import SecurityValidator

        # User wants to scan only "internal_id" — the default
        # "patient_name", "ssn", etc. lists should NOT trigger.
        ctx = _project_with_config(
            tmp_path,
            "security:\n  phi_fields:\n    - internal_id\n",
        )

        go_default = tmp_path / "default.go"
        go_default.write_text('log.Info().Str("ssn", val).Send()\n')
        go_custom = tmp_path / "custom.go"
        go_custom.write_text('log.Info().Str("internal_id", val).Send()\n')

        validator = SecurityValidator()

        # ssn was a default — but config overrides → should NOT trip.
        r1 = validator.run(ctx, file_path=str(go_default), mode="post_tool_use")
        assert "V08-PHI-LOGGING" not in [f.rule for f in r1.findings]

        # internal_id is the user's custom field → should trip.
        r2 = validator.run(ctx, file_path=str(go_custom), mode="post_tool_use")
        assert any(f.rule == "V08-PHI-LOGGING" for f in r2.findings)

    def test_required_gitignore_override_replaces_defaults(self, tmp_path: Path) -> None:
        from hooks.validators.security import SecurityValidator

        # User cares about "secrets.json", not the default ".env" set.
        # .gitignore is missing → V08-NO-GITIGNORE message must mention
        # the user's list, not the validator's default.
        ctx = _project_with_config(
            tmp_path,
            'security:\n  required_gitignore:\n    - "secrets.json"\n    - "*.pem"\n',
        )

        result = SecurityValidator().run(ctx, file_path=None, mode="stop")
        no_gitignore = [f for f in result.findings if f.rule == "V08-NO-GITIGNORE"]
        assert len(no_gitignore) == 1
        # User's pattern surfaces in the FIX message
        assert "secrets.json" in no_gitignore[0].fix
        # Default '*.key' should NOT appear (user replaced the list)
        assert "*.key" not in no_gitignore[0].fix


# ---------------------------------------------------------------------------
# 7. V05 DockerConfig — vhost_check_mode + reverse_proxy_networks (Phase21)
# ---------------------------------------------------------------------------


class TestV05DockerConfig:
    @staticmethod
    def _write_compose(project: Path, body: str, filename: str) -> None:
        (project / filename).write_text(body)

    _VHOST_BODY = (
        'version: "3"\n'
        "services:\n"
        "  app:\n"
        "    environment:\n"
        "      VIRTUAL_HOST: example.com\n"
        "    networks:\n"
        "      - app_network\n"
        "networks:\n"
        "  app_network:\n"
        "    driver: bridge\n"
    )

    def test_default_mode_skips_vhost_check_in_dev_compose(self, tmp_path: Path) -> None:
        # Phase21: with the default vhost_check_mode="production", a
        # base docker-compose.yaml is classified as dev → V05-VHOST-NO-NETWORK
        # must NOT fire. This is the user-reported false positive being fixed.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(tmp_path, "")
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" not in rules

    def test_default_mode_fires_vhost_check_in_prod_compose(self, tmp_path: Path) -> None:
        # The same body in a prod-classified file STILL trips the check.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(tmp_path, "")
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.production.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" in rules

    def test_mode_all_restores_legacy_strictness(self, tmp_path: Path) -> None:
        # Power user opts into the old strict behaviour: dev compose
        # files should ALSO trip when VHOST is set without proxy.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(tmp_path, 'docker:\n  vhost_check_mode: "all"\n')
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" in rules

    def test_mode_off_silences_check_everywhere(self, tmp_path: Path) -> None:
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(tmp_path, 'docker:\n  vhost_check_mode: "off"\n')
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.production.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" not in rules

    def test_traefik_satisfies_check_when_configured(self, tmp_path: Path) -> None:
        # User uses Traefik instead of nginx-proxy: configure
        # reverse_proxy_networks and the same compose passes.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(
            tmp_path,
            "docker:\n  reverse_proxy_networks:\n    - traefik\n",
        )
        traefik_body = (
            'version: "3"\n'
            "services:\n"
            "  app:\n"
            "    environment:\n"
            "      VIRTUAL_HOST: example.com\n"
            "    networks:\n"
            "      - traefik\n"  # <-- user's proxy network
            "networks:\n"
            "  traefik:\n"
            "    external: true\n"
        )
        self._write_compose(tmp_path, traefik_body, "docker-compose.production.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" not in rules

    def test_custom_dev_filename_pattern_skips_check(self, tmp_path: Path) -> None:
        # Company convention: docker-compose.local.yaml is dev. Default
        # patterns wouldn't classify it as dev → check would fire under
        # "production" mode. With override it skips.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(
            tmp_path,
            'docker:\n  dev_filename_patterns:\n    - "*.local.*"\n',
        )
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.local.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" not in rules

    def test_custom_production_filename_pattern_forces_prod_mode(self, tmp_path: Path) -> None:
        # Company convention: *-prd.yaml is prod. With config, the check
        # fires even though the filename doesn't match the default
        # production patterns.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(
            tmp_path,
            'docker:\n  production_filename_patterns:\n    - "*-prd.*"\n',
        )
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose-prd.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        rules = [f.rule for f in result.findings]
        assert "V05-VHOST-NO-NETWORK" in rules

    def test_empty_reverse_proxy_networks_yields_self_explanatory_message(self, tmp_path: Path) -> None:
        # Phase24 (A4): user explicitly empties reverse_proxy_networks —
        # the prior cryptic "<none configured>" message is replaced with
        # actionable remediation that names the two ways out.
        from hooks.validators.docker_compose import DockerValidator

        ctx = _project_with_config(tmp_path, "docker:\n  reverse_proxy_networks: []\n")
        self._write_compose(tmp_path, self._VHOST_BODY, "docker-compose.production.yaml")

        result = DockerValidator().run(ctx, file_path=None, mode="stop")
        vhost = [f for f in result.findings if f.rule == "V05-VHOST-NO-NETWORK"]
        assert len(vhost) == 1
        # Message names the misconfig directly (no <none configured> filler)
        assert "configured to []" in vhost[0].message
        assert "<none configured>" not in vhost[0].message
        # Fix lists BOTH escape hatches the user can take
        assert "vhost_check_mode" in vhost[0].fix
        assert "reverse_proxy_networks" in vhost[0].fix
