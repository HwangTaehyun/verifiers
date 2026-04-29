"""Tests for stop_validator.py — Stop hook with circuit breaker.

Covers:
  - Normal validation flow (approve/block)
  - reason field presence when blocking
  - stop_hook_active circuit breaker
  - Block count file management
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def disable_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the sequential fallback for these tests.

    The stop_validator integration tests rely on
    ``mock.patch("hooks.validators.base.BaseValidator.run", ...)`` to
    inject fake findings, but mocks don't propagate into subprocess
    workers spawned by ``ProcessPoolExecutor`` — running parallel here
    would call the REAL validators against an empty tmp_path, producing
    legitimate (and unwanted) findings. The parallel runner has its own
    test file (``test_parallel_runner.py``).
    """
    monkeypatch.setenv("VERIFIERS_PARALLEL", "0")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _run_stop_validator(input_data: dict | None) -> dict:
    """Run stop_validator main() and capture JSON output."""
    from hooks.stop_validator import main

    stdin_data = json.dumps(input_data) if input_data else ""
    captured: list[str] = []

    with mock.patch("sys.stdin", mock.Mock(read=mock.Mock(return_value=stdin_data))):
        with mock.patch(
            "builtins.print",
            side_effect=lambda *args, **kwargs: captured.append(
                " ".join(str(a) for a in args) + kwargs.get("end", "\n"),
            ),
        ):
            main()

    raw = "".join(captured).strip()
    return json.loads(raw) if raw else {}


# ============================================================================
# 1. Basic flow
# ============================================================================


class TestBasicFlow:
    def test_empty_input_approves(self) -> None:
        output = _run_stop_validator(None)
        assert output.get("decision") == "approve"

    def test_no_findings_approve(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()

        # Mock all validators to return no findings
        def no_findings(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id=self.id)

        with mock.patch("hooks.validators.base.BaseValidator.run", no_findings):
            output = _run_stop_validator({"cwd": str(tmp_path)})
        assert output.get("decision") == "approve"

    def test_error_findings_block_with_reason(self, tmp_path: Path) -> None:
        """When errors exist, output must include both decision:block and reason."""
        (tmp_path / ".git").mkdir()

        from hooks.validators.base import Finding

        fake_finding = Finding(
            severity="error",
            file=str(tmp_path / "secret.py"),
            rule="V08-HARDCODED-SECRET",
            message="Hardcoded API key",
            fix="Move to environment variable",
        )

        def fake_validator_run(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id="fake", findings=[fake_finding])

        with mock.patch(
            "hooks.validators.base.BaseValidator.run",
            fake_validator_run,
        ):
            output = _run_stop_validator({"cwd": str(tmp_path)})

        assert output.get("decision") == "block"
        assert "reason" in output, "Stop hook must include 'reason' when blocking"
        assert "V08-HARDCODED-SECRET" in output["reason"]
        assert "additionalContext" in output


# ============================================================================
# 2. Circuit breaker (stop_hook_active)
# ============================================================================


class TestCircuitBreaker:
    def test_first_block_increments_counter(self, tmp_path: Path) -> None:
        """On first stop_hook_active block, a counter file is created."""
        (tmp_path / ".git").mkdir()

        from hooks.validators.base import Finding

        fake_finding = Finding(
            severity="error",
            file=str(tmp_path / "x.py"),
            rule="V01-TEST",
            message="test",
            fix="fix",
        )

        def fake_run(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id="fake", findings=[fake_finding])

        with mock.patch("hooks.validators.base.BaseValidator.run", fake_run):
            output = _run_stop_validator(
                {
                    "cwd": str(tmp_path),
                    "stop_hook_active": True,
                }
            )

        # Should still block (first retry)
        assert output.get("decision") == "block"
        # Counter file should exist
        counter_file = tmp_path / ".verifiers" / "state" / "verifier-block-count"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        assert counter_file.exists()
        assert counter_file.read_text().strip() == "1"

    def test_circuit_breaker_approves_after_max(self, tmp_path: Path) -> None:
        """After MAX consecutive blocks, approve to prevent infinite loop."""
        (tmp_path / ".git").mkdir()

        # Pre-set counter to MAX - 1 (so next block triggers circuit breaker)
        from hooks.stop_validator import _MAX_CONSECUTIVE_BLOCKS

        counter_file = tmp_path / ".verifiers" / "state" / "verifier-block-count"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        counter_file.write_text(str(_MAX_CONSECUTIVE_BLOCKS - 1))

        from hooks.validators.base import Finding

        fake_finding = Finding(
            severity="error",
            file=str(tmp_path / "x.py"),
            rule="V01-TEST",
            message="test",
            fix="fix",
        )

        def fake_run(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id="fake", findings=[fake_finding])

        with mock.patch("hooks.validators.base.BaseValidator.run", fake_run):
            output = _run_stop_validator(
                {
                    "cwd": str(tmp_path),
                    "stop_hook_active": True,
                }
            )

        # Circuit breaker: should approve despite errors
        assert output.get("decision") == "approve"
        assert "CIRCUIT BREAKER" in output.get("additionalContext", "")
        # Counter file should be cleaned up
        assert not counter_file.exists()

    def test_counter_resets_on_approve(self, tmp_path: Path) -> None:
        """When validation passes, the counter file is cleaned up."""
        (tmp_path / ".git").mkdir()
        counter_file = tmp_path / ".verifiers" / "state" / "verifier-block-count"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        counter_file.write_text("2")

        # Mock all validators to return no findings
        def no_findings(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id=self.id)

        with mock.patch("hooks.validators.base.BaseValidator.run", no_findings):
            output = _run_stop_validator({"cwd": str(tmp_path)})
        assert output.get("decision") == "approve"
        assert not counter_file.exists()

    def test_counter_resets_when_not_in_loop(self, tmp_path: Path) -> None:
        """Counter resets when stop_hook_active is false (fresh turn)."""
        (tmp_path / ".git").mkdir()
        counter_file = tmp_path / ".verifiers" / "state" / "verifier-block-count"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        counter_file.write_text("2")

        from hooks.validators.base import Finding

        fake_finding = Finding(
            severity="error",
            file=str(tmp_path / "x.py"),
            rule="V01-TEST",
            message="test",
            fix="fix",
        )

        def fake_run(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id="fake", findings=[fake_finding])

        with mock.patch("hooks.validators.base.BaseValidator.run", fake_run):
            output = _run_stop_validator(
                {
                    "cwd": str(tmp_path),
                    "stop_hook_active": False,
                }
            )

        # Still blocks (errors exist), but counter should be reset
        assert output.get("decision") == "block"
        assert not counter_file.exists()

    def test_circuit_breaker_reason_removed_on_approve(self, tmp_path: Path) -> None:
        """When circuit breaker fires, reason field should be removed."""
        (tmp_path / ".git").mkdir()

        from hooks.stop_validator import _MAX_CONSECUTIVE_BLOCKS

        counter_file = tmp_path / ".verifiers" / "state" / "verifier-block-count"
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        counter_file.write_text(str(_MAX_CONSECUTIVE_BLOCKS - 1))

        from hooks.validators.base import Finding

        fake_finding = Finding(
            severity="error",
            file=str(tmp_path / "x.py"),
            rule="V01-TEST",
            message="test",
            fix="fix",
        )

        def fake_run(self, ctx, file_path=None, mode="post_tool_use"):
            from hooks.validators.base import ValidationResult

            return ValidationResult(validator_id="fake", findings=[fake_finding])

        with mock.patch("hooks.validators.base.BaseValidator.run", fake_run):
            output = _run_stop_validator(
                {
                    "cwd": str(tmp_path),
                    "stop_hook_active": True,
                }
            )

        assert output.get("decision") == "approve"
        assert "reason" not in output  # reason removed when approving


# ============================================================================
# 4. _apply_exclude_filters — Tier 3 honours config.exclude (Phase17)
# ============================================================================


class TestApplyExcludeFilters:
    """Stop hook post-filters validator findings against ctx.config.exclude.

    Without this filter, validators that scan the project themselves
    (V14 complexity, V20 hasura, V18 mock-data, etc.) would re-surface
    violations that the user globally excluded — defeating the point of
    .verifiers/config.yaml in stop mode.

    Classical-school test style: real Findings, real ProjectContext,
    real on-disk YAML config. No mocks for internal collaborators.
    """

    @staticmethod
    def _setup_project(tmp_path: Path, yaml_body: str | None) -> "ProjectContext":  # noqa: F821
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir()
        if yaml_body is not None:
            cfg_dir = tmp_path / ".verifiers"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.yaml").write_text(yaml_body)
        return ProjectContext(tmp_path)

    @staticmethod
    def _finding(file: Path | str, rule: str = "V14-HIGH-COMPLEXITY") -> "Finding":  # noqa: F821
        from hooks.validators.base import Finding

        return Finding(
            severity="warning",
            file=str(file),
            rule=rule,
            message="x",
            fix="y",
            line=1,
        )

    def test_no_config_passes_findings_through(self, tmp_path: Path) -> None:
        from hooks.stop_validator import _apply_exclude_filters

        ctx = self._setup_project(tmp_path, None)
        findings = [self._finding(tmp_path / "src" / "main.py")]
        assert _apply_exclude_filters(findings, ctx) == findings

    def test_global_exclude_drops_finding(self, tmp_path: Path) -> None:
        from hooks.stop_validator import _apply_exclude_filters

        ctx = self._setup_project(tmp_path, 'exclude:\n  paths:\n    - "legacy/**"\n')
        bad = self._finding(tmp_path / "legacy" / "old.py")
        good = self._finding(tmp_path / "src" / "main.py")
        out = _apply_exclude_filters([bad, good], ctx)
        assert out == [good]

    def test_per_validator_exclude_drops_only_that_validator(self, tmp_path: Path) -> None:
        from hooks.stop_validator import _apply_exclude_filters

        ctx = self._setup_project(
            tmp_path,
            "exclude:\n  per_validator:\n    V14:\n      - 'legacy/**'\n",
        )
        v14 = self._finding(tmp_path / "legacy" / "old.py", rule="V14-HIGH-COMPLEXITY")
        v08 = self._finding(tmp_path / "legacy" / "old.py", rule="V08-HARDCODED-SECRET")
        out = _apply_exclude_filters([v14, v08], ctx)
        # V14 drops, V08 stays.
        rules_left = [f.rule for f in out]
        assert "V14-HIGH-COMPLEXITY" not in rules_left
        assert "V08-HARDCODED-SECRET" in rules_left

    def test_findings_without_file_path_pass_through(self, tmp_path: Path) -> None:
        from hooks.stop_validator import _apply_exclude_filters
        from hooks.validators.base import Finding

        # Findings with file="" (project-level summaries) can't be path-
        # filtered and must not be silently dropped.
        ctx = self._setup_project(tmp_path, 'exclude:\n  paths:\n    - "legacy/**"\n')
        project_level = Finding(
            severity="info",
            file="",
            rule="V03-PROTO-SUMMARY",
            message="3 protos checked",
            fix="—",
        )
        out = _apply_exclude_filters([project_level], ctx)
        assert out == [project_level]

    def test_full_id_in_per_validator_also_works(self, tmp_path: Path) -> None:
        from hooks.stop_validator import _apply_exclude_filters

        # User wrote the full id ("V20-hasura-graphql") instead of the
        # prefix. Both forms must be accepted.
        ctx = self._setup_project(
            tmp_path,
            "exclude:\n  per_validator:\n    V20-hasura-graphql:\n      - 'sql/**'\n",
        )
        f = self._finding(tmp_path / "sql" / "raw.go", rule="V20-RAW-SQL-FORBIDDEN")
        out = _apply_exclude_filters([f], ctx)
        assert out == []
