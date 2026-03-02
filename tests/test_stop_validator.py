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
            output = _run_stop_validator({
                "cwd": str(tmp_path),
                "stop_hook_active": True,
            })

        # Should still block (first retry)
        assert output.get("decision") == "block"
        # Counter file should exist
        counter_file = tmp_path / ".verifier-block-count"
        assert counter_file.exists()
        assert counter_file.read_text().strip() == "1"

    def test_circuit_breaker_approves_after_max(self, tmp_path: Path) -> None:
        """After MAX consecutive blocks, approve to prevent infinite loop."""
        (tmp_path / ".git").mkdir()

        # Pre-set counter to MAX - 1 (so next block triggers circuit breaker)
        from hooks.stop_validator import _MAX_CONSECUTIVE_BLOCKS

        counter_file = tmp_path / ".verifier-block-count"
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
            output = _run_stop_validator({
                "cwd": str(tmp_path),
                "stop_hook_active": True,
            })

        # Circuit breaker: should approve despite errors
        assert output.get("decision") == "approve"
        assert "CIRCUIT BREAKER" in output.get("additionalContext", "")
        # Counter file should be cleaned up
        assert not counter_file.exists()

    def test_counter_resets_on_approve(self, tmp_path: Path) -> None:
        """When validation passes, the counter file is cleaned up."""
        (tmp_path / ".git").mkdir()
        counter_file = tmp_path / ".verifier-block-count"
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
        counter_file = tmp_path / ".verifier-block-count"
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
            output = _run_stop_validator({
                "cwd": str(tmp_path),
                "stop_hook_active": False,
            })

        # Still blocks (errors exist), but counter should be reset
        assert output.get("decision") == "block"
        assert not counter_file.exists()

    def test_circuit_breaker_reason_removed_on_approve(self, tmp_path: Path) -> None:
        """When circuit breaker fires, reason field should be removed."""
        (tmp_path / ".git").mkdir()

        from hooks.stop_validator import _MAX_CONSECUTIVE_BLOCKS

        counter_file = tmp_path / ".verifier-block-count"
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
            output = _run_stop_validator({
                "cwd": str(tmp_path),
                "stop_hook_active": True,
            })

        assert output.get("decision") == "approve"
        assert "reason" not in output  # reason removed when approving
