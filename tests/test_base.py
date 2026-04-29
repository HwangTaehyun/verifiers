"""Tests for hooks/validators/base.py — dataclasses, BaseValidator, and I/O helpers."""

from __future__ import annotations

import io
import json
from dataclasses import fields
from unittest.mock import patch

import pytest

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    _build_reason,
    _dedup_findings,
    format_output,
    read_hook_input,
    stdin_truncation_finding,
    write_hook_output,
)
from hooks.validators.base import _MAX_STDIN_BYTES, _TRUNCATED_SENTINEL_KEY
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def error_finding() -> Finding:
    return Finding(
        severity="error",
        file="/src/app.py",
        rule="V01-ENV-MISSING",
        message="Missing .env entry for DB_HOST",
        fix="Add DB_HOST=... to .env.example",
        line=42,
    )


@pytest.fixture
def warning_finding() -> Finding:
    return Finding(
        severity="warning",
        file="/src/config.py",
        rule="V02-CFG-DRIFT",
        message="Config key deprecated",
        fix="Remove deprecated key 'old_key'",
    )


@pytest.fixture
def info_finding() -> Finding:
    return Finding(
        severity="info",
        file="/src/utils.py",
        rule="V03-STYLE",
        message="Consider using f-string",
        fix="Replace .format() with f-string",
    )


# ---------------------------------------------------------------------------
# 1. Finding dataclass creation
# ---------------------------------------------------------------------------


class TestFindingDataclass:
    """Verify the Finding dataclass structure and defaults."""

    def test_required_fields(self, error_finding: Finding) -> None:
        assert error_finding.severity == "error"
        assert error_finding.file == "/src/app.py"
        assert error_finding.rule == "V01-ENV-MISSING"
        assert error_finding.message == "Missing .env entry for DB_HOST"
        assert error_finding.fix == "Add DB_HOST=... to .env.example"

    def test_optional_line_present(self, error_finding: Finding) -> None:
        assert error_finding.line == 42

    def test_optional_line_defaults_to_none(self, warning_finding: Finding) -> None:
        assert warning_finding.line is None

    def test_field_names(self) -> None:
        # Phase36 (A4 audit): ``kind`` was added to distinguish ordinary
        # findings from sentinels (V##-CRASHED, V##-TIMEOUT) so the Tier 3
        # exclude.paths filter can short-circuit on sentinels.
        names = {f.name for f in fields(Finding)}
        assert names == {"severity", "file", "rule", "message", "fix", "line", "kind"}

    def test_default_kind_is_finding(self) -> None:
        f = Finding("error", "/a.py", "R01", "msg", "fix")
        assert f.kind == "finding"

    def test_sentinel_kind_can_be_set(self) -> None:
        f = Finding("warning", "/proj", "V14-CRASHED", "boom", "see logs", kind="sentinel")
        assert f.kind == "sentinel"

    def test_equality(self) -> None:
        a = Finding("error", "/a.py", "R01", "msg", "fix", 1)
        b = Finding("error", "/a.py", "R01", "msg", "fix", 1)
        assert a == b

    def test_inequality_different_severity(self) -> None:
        a = Finding("error", "/a.py", "R01", "msg", "fix")
        b = Finding("warning", "/a.py", "R01", "msg", "fix")
        assert a != b


# ---------------------------------------------------------------------------
# 2. ValidationResult — has_errors / has_warnings properties
# ---------------------------------------------------------------------------


class TestValidationResult:
    """Verify has_errors and has_warnings computed properties."""

    def test_empty_findings_no_errors(self) -> None:
        result = ValidationResult(validator_id="test")
        assert result.has_errors is False
        assert result.has_warnings is False

    def test_has_errors_true(self, error_finding: Finding) -> None:
        result = ValidationResult(validator_id="test", findings=[error_finding])
        assert result.has_errors is True

    def test_has_warnings_true(self, warning_finding: Finding) -> None:
        result = ValidationResult(validator_id="test", findings=[warning_finding])
        assert result.has_warnings is True

    def test_has_errors_false_with_warnings_only(self, warning_finding: Finding) -> None:
        result = ValidationResult(validator_id="test", findings=[warning_finding])
        assert result.has_errors is False

    def test_has_warnings_false_with_errors_only(self, error_finding: Finding) -> None:
        result = ValidationResult(validator_id="test", findings=[error_finding])
        assert result.has_warnings is False

    def test_info_only_neither_errors_nor_warnings(self, info_finding: Finding) -> None:
        result = ValidationResult(validator_id="test", findings=[info_finding])
        assert result.has_errors is False
        assert result.has_warnings is False

    def test_mixed_findings(self, error_finding: Finding, warning_finding: Finding, info_finding: Finding) -> None:
        result = ValidationResult(
            validator_id="test",
            findings=[error_finding, warning_finding, info_finding],
        )
        assert result.has_errors is True
        assert result.has_warnings is True

    def test_findings_default_factory_is_independent(self) -> None:
        """Each instance gets its own list — no shared mutable default."""
        r1 = ValidationResult(validator_id="a")
        r2 = ValidationResult(validator_id="b")
        r1.findings.append(Finding("error", "/x.py", "R", "m", "f"))
        assert len(r2.findings) == 0


# ---------------------------------------------------------------------------
# 3. BaseValidator.should_run with various file patterns
# ---------------------------------------------------------------------------


class _DummyValidator(BaseValidator):
    """Concrete subclass so we can instantiate BaseValidator."""

    id = "dummy"
    name = "Dummy Validator"
    file_patterns: list[str] = []

    def validate(
        self, ctx: ProjectContext, file_path: str | None = None, mode: str = "post_tool_use"
    ) -> ValidationResult:
        return ValidationResult(validator_id=self.id)


class TestShouldRun:
    """Verify should_run pattern matching logic."""

    def test_empty_patterns_matches_everything(self, tmp_path) -> None:
        v = _DummyValidator()
        v.file_patterns = []
        assert v.should_run("/any/file.txt") is True
        assert v.should_run("") is True

    def test_exact_pattern_match(self, tmp_path) -> None:
        v = _DummyValidator()
        v.file_patterns = ["*.py"]
        assert v.should_run("app.py") is True

    def test_exact_pattern_no_match(self, tmp_path) -> None:
        v = _DummyValidator()
        v.file_patterns = ["*.py"]
        assert v.should_run("app.js") is False

    def test_multiple_patterns_any_matches(self) -> None:
        v = _DummyValidator()
        v.file_patterns = ["*.py", "*.go", "*.rs"]
        assert v.should_run("main.go") is True

    def test_multiple_patterns_none_match(self) -> None:
        v = _DummyValidator()
        v.file_patterns = ["*.py", "*.go"]
        assert v.should_run("style.css") is False

    def test_glob_star_directory(self) -> None:
        v = _DummyValidator()
        v.file_patterns = ["server/config/*.yaml"]
        assert v.should_run("server/config/app.yaml") is True
        # fnmatch's * matches path separators, so nested paths also match
        assert v.should_run("server/config/nested/deep.yaml") is True
        assert v.should_run("web/src/app.yaml") is False

    def test_wildcard_in_name(self) -> None:
        v = _DummyValidator()
        v.file_patterns = [".env*"]
        assert v.should_run(".env") is True
        assert v.should_run(".env.local") is True
        assert v.should_run(".env.production") is True
        assert v.should_run("other.env") is False

    def test_question_mark_pattern(self) -> None:
        v = _DummyValidator()
        v.file_patterns = ["?.txt"]
        assert v.should_run("a.txt") is True
        assert v.should_run("ab.txt") is False


# ---------------------------------------------------------------------------
# 4. format_output — PostToolUse mode
# ---------------------------------------------------------------------------


class TestFormatOutputPostToolUse:
    """format_output with mode='post_tool_use'."""

    def test_empty_findings_returns_empty_dict(self) -> None:
        result = format_output([], mode="post_tool_use")
        assert result == {}

    def test_error_blocks_with_reason(self, error_finding: Finding) -> None:
        """PostToolUse errors now block with a reason so Claude sees them."""
        result = format_output([error_finding], mode="post_tool_use")
        assert result["decision"] == "block"
        assert "reason" in result
        assert "V01-ENV-MISSING" in result["reason"]
        assert "additionalContext" in result

    def test_warning_only_no_block(self, warning_finding: Finding) -> None:
        """PostToolUse with only warnings should NOT block."""
        result = format_output([warning_finding], mode="post_tool_use")
        assert "decision" not in result
        assert "additionalContext" in result

    def test_info_only_no_block(self, info_finding: Finding) -> None:
        """PostToolUse with only info should NOT block."""
        result = format_output([info_finding], mode="post_tool_use")
        assert "decision" not in result
        assert "additionalContext" in result

    def test_context_contains_rule(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "V01-ENV-MISSING" in result["additionalContext"]

    def test_context_contains_file(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "/src/app.py" in result["additionalContext"]

    def test_context_contains_line_number(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "Line: 42" in result["additionalContext"]

    def test_context_omits_line_when_none(self, warning_finding: Finding) -> None:
        result = format_output([warning_finding], mode="post_tool_use")
        assert "Line:" not in result["additionalContext"]

    def test_context_contains_fix(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "FIX: Add DB_HOST=... to .env.example" in result["additionalContext"]

    def test_context_contains_issue_message(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "Issue: Missing .env entry for DB_HOST" in result["additionalContext"]

    def test_multiple_findings_all_present(self, error_finding: Finding, warning_finding: Finding) -> None:
        result = format_output([error_finding, warning_finding], mode="post_tool_use")
        ctx = result["additionalContext"]
        assert "V01-ENV-MISSING" in ctx
        assert "V02-CFG-DRIFT" in ctx

    def test_error_icon_in_context(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="post_tool_use")
        assert "\U0001f6ab" in result["additionalContext"]

    def test_warning_icon_in_context(self, warning_finding: Finding) -> None:
        result = format_output([warning_finding], mode="post_tool_use")
        assert "\u26a0\ufe0f" in result["additionalContext"]

    def test_info_icon_in_context(self, info_finding: Finding) -> None:
        result = format_output([info_finding], mode="post_tool_use")
        assert "\u2139\ufe0f" in result["additionalContext"]


# ---------------------------------------------------------------------------
# 5. format_output — Stop mode
# ---------------------------------------------------------------------------


class TestFormatOutputStop:
    """format_output with mode='stop'."""

    def test_empty_findings_approve(self) -> None:
        result = format_output([], mode="stop")
        assert result == {"decision": "approve"}

    def test_error_findings_block(self, error_finding: Finding) -> None:
        result = format_output([error_finding], mode="stop")
        assert result["decision"] == "block"
        assert "additionalContext" in result

    def test_error_findings_include_reason(self, error_finding: Finding) -> None:
        """Stop hook blocks must include a reason field (Claude Code protocol)."""
        result = format_output([error_finding], mode="stop")
        assert "reason" in result
        assert "V01-ENV-MISSING" in result["reason"]

    def test_reason_contains_fix_instruction(self, error_finding: Finding) -> None:
        """reason should tell Claude what to fix."""
        result = format_output([error_finding], mode="stop")
        reason = result["reason"]
        assert "Add DB_HOST=... to .env.example" in reason

    def test_reason_not_in_approve(self, warning_finding: Finding) -> None:
        """reason is only needed when decision is block."""
        result = format_output([warning_finding], mode="stop")
        assert result["decision"] == "approve"
        assert "reason" not in result

    def test_warning_only_approve_with_context(self, warning_finding: Finding) -> None:
        result = format_output([warning_finding], mode="stop")
        assert result["decision"] == "approve"
        assert "additionalContext" in result
        assert "V02-CFG-DRIFT" in result["additionalContext"]

    def test_info_only_approve_with_context(self, info_finding: Finding) -> None:
        result = format_output([info_finding], mode="stop")
        assert result["decision"] == "approve"
        assert "additionalContext" in result

    def test_mixed_error_and_warning_blocks(self, error_finding: Finding, warning_finding: Finding) -> None:
        result = format_output([error_finding, warning_finding], mode="stop")
        assert result["decision"] == "block"
        ctx = result["additionalContext"]
        assert "V01-ENV-MISSING" in ctx
        assert "V02-CFG-DRIFT" in ctx

    def test_mixed_reason_includes_warning_count(self, error_finding: Finding, warning_finding: Finding) -> None:
        """reason should mention warnings too."""
        result = format_output([error_finding, warning_finding], mode="stop")
        reason = result["reason"]
        assert "1 error(s)" in reason
        assert "1 warning(s)" in reason


# ---------------------------------------------------------------------------
# 5b. _build_reason — concise, actionable reason builder
# ---------------------------------------------------------------------------


class TestBuildReason:
    """Verify _build_reason produces actionable summaries."""

    def test_single_error_stop(self, error_finding: Finding) -> None:
        reason = _build_reason([error_finding], mode="stop")
        assert "1 error(s)" in reason
        assert "V01-ENV-MISSING" in reason
        assert error_finding.fix in reason

    def test_error_with_line_shows_location(self, error_finding: Finding) -> None:
        reason = _build_reason([error_finding], mode="stop")
        assert "/src/app.py:42" in reason

    def test_error_without_line_shows_file_only(self) -> None:
        f = Finding("error", "/a.py", "R01", "msg", "fix me")
        reason = _build_reason([f], mode="stop")
        assert "/a.py" in reason
        assert "/a.py:" not in reason  # no colon when no line

    def test_warnings_section(self, error_finding: Finding, warning_finding: Finding) -> None:
        reason = _build_reason([error_finding, warning_finding], mode="stop")
        assert "1 warning(s)" in reason
        assert "V02-CFG-DRIFT" in reason

    def test_many_errors_truncated_stop(self) -> None:
        """Stop mode: more than 10 errors should be truncated."""
        errors = [Finding("error", f"/f{i}.py", f"R{i:02d}", f"msg{i}", f"fix{i}") for i in range(15)]
        reason = _build_reason(errors, mode="stop")
        assert "5 more error(s)" in reason

    def test_many_warnings_truncated_stop(self) -> None:
        """Stop mode: more than 5 warnings should be truncated."""
        err = Finding("error", "/x.py", "R01", "msg", "fix")
        warnings = [Finding("warning", f"/w{i}.py", f"W{i:02d}", f"warn{i}", f"fix{i}") for i in range(8)]
        reason = _build_reason([err] + warnings, mode="stop")
        assert "3 more warning(s)" in reason

    def test_no_errors_only_warnings(self, warning_finding: Finding) -> None:
        """With only warnings (no errors), reason should still be coherent."""
        reason = _build_reason([warning_finding], mode="stop")
        assert "0 error(s)" in reason
        assert "1 warning(s)" in reason

    def test_post_tool_use_mode_shorter(self) -> None:
        """PostToolUse mode has tighter truncation (max 5 errors)."""
        errors = [Finding("error", f"/f{i}.py", f"R{i:02d}", f"msg{i}", f"fix{i}") for i in range(8)]
        reason = _build_reason(errors, mode="post_tool_use")
        assert "3 more error(s)" in reason  # 8 - 5 = 3 remaining
        assert "file you just edited" in reason

    def test_post_tool_use_warnings_truncated(self) -> None:
        """PostToolUse mode: max 3 warnings before truncation."""
        err = Finding("error", "/x.py", "R01", "msg", "fix")
        warnings = [Finding("warning", f"/w{i}.py", f"W{i:02d}", f"warn{i}", f"fix{i}") for i in range(5)]
        reason = _build_reason([err] + warnings, mode="post_tool_use")
        assert "2 more warning(s)" in reason  # 5 - 3 = 2 remaining


# ---------------------------------------------------------------------------
# 6. read_hook_input — mock stdin
# ---------------------------------------------------------------------------


class TestReadHookInput:
    """Verify read_hook_input parses JSON from stdin."""

    def test_valid_json(self) -> None:
        payload = {"tool_name": "Write", "file_path": "/tmp/x.py"}
        with patch("sys.stdin", new=io.StringIO(json.dumps(payload))):
            result = read_hook_input()
        assert result == payload

    def test_empty_stdin_returns_empty_dict(self) -> None:
        with patch("sys.stdin", new=io.StringIO("")):
            result = read_hook_input()
        assert result == {}

    def test_invalid_json_returns_empty_dict(self) -> None:
        with patch("sys.stdin", new=io.StringIO("not json at all")):
            result = read_hook_input()
        assert result == {}

    def test_nested_json(self) -> None:
        payload = {"tool_input": {"command": "ls", "args": ["-la"]}}
        with patch("sys.stdin", new=io.StringIO(json.dumps(payload))):
            result = read_hook_input()
        assert result == payload

    def test_unicode_json(self) -> None:
        payload = {"message": "Hello, world! You can use special characters."}
        with patch("sys.stdin", new=io.StringIO(json.dumps(payload, ensure_ascii=False))):
            result = read_hook_input()
        assert result == payload

    def test_under_cap_no_truncation_sentinel(self) -> None:
        # Phase38b: a payload that fits in the cap must not carry the
        # truncation sentinel key — that would make every hook call
        # emit a false-positive STDIN-TRUNCATED warning.
        payload = {"tool_name": "Edit"}
        with patch("sys.stdin", new=io.StringIO(json.dumps(payload))):
            result = read_hook_input()
        assert _TRUNCATED_SENTINEL_KEY not in result

    def test_oversized_stdin_marks_truncated(self) -> None:
        # Phase38b: when stdin holds more than _MAX_STDIN_BYTES, the
        # returned dict must carry the truncation sentinel so the
        # hook entry points emit a STDIN-TRUNCATED warning instead of
        # silent-passing on a partial JSON parse.
        oversize = "x" * (_MAX_STDIN_BYTES + 5)
        with patch("sys.stdin", new=io.StringIO(oversize)):
            result = read_hook_input()
        assert result.get(_TRUNCATED_SENTINEL_KEY) == _MAX_STDIN_BYTES

    def test_truncation_sentinel_independent_of_parse_success(self) -> None:
        # Even if the *capped slice* happens to be valid JSON (the
        # JSON has a complete root object inside the first N bytes),
        # the truncation flag must still be set — otherwise an
        # attacker could pad with junk after a clean ``{}`` to hide
        # the cap-hit.
        prefix = json.dumps({"tool_name": "Edit"})
        # ``prefix`` length is a few dozen bytes; pad to push past cap.
        payload = prefix + " " + "x" * (_MAX_STDIN_BYTES + 1 - len(prefix))
        with patch("sys.stdin", new=io.StringIO(payload)):
            result = read_hook_input()
        assert result.get(_TRUNCATED_SENTINEL_KEY) == _MAX_STDIN_BYTES


class TestStdinTruncationFinding:
    """Phase38b — stdin_truncation_finding factory shape."""

    def test_finding_is_warning_sentinel(self) -> None:
        f = stdin_truncation_finding(_MAX_STDIN_BYTES)
        assert f.severity == "warning"
        assert f.kind == "sentinel"
        assert f.rule == "VERIFIERS-STDIN-TRUNCATED"

    def test_finding_message_includes_cap(self) -> None:
        f = stdin_truncation_finding(123_456)
        assert "123,456" in f.message


# ---------------------------------------------------------------------------
# 7. write_hook_output — capture stdout
# ---------------------------------------------------------------------------


class TestWriteHookOutput:
    """Verify write_hook_output writes JSON to stdout."""

    def test_empty_dict(self, capsys) -> None:
        write_hook_output({})
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == {}

    def test_approve_decision(self, capsys) -> None:
        write_hook_output({"decision": "approve"})
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed == {"decision": "approve"}

    def test_block_with_context(self, capsys) -> None:
        output = {"decision": "block", "additionalContext": "Error details"}
        write_hook_output(output)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed == output

    def test_unicode_preserved(self, capsys) -> None:
        output = {"additionalContext": "Warning about special chars."}
        write_hook_output(output)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed == output

    def test_output_is_single_line(self, capsys) -> None:
        output = {"decision": "approve", "additionalContext": "line1\nline2\nline3"}
        write_hook_output(output)
        captured = capsys.readouterr()
        # print adds a trailing newline, but the JSON itself should be one line
        lines = captured.out.strip().split("\n")
        assert len(lines) == 1

    def test_roundtrip_read_write(self, capsys) -> None:
        """write_hook_output output can be read back as valid JSON."""
        original = {"decision": "block", "additionalContext": "Fix the issue"}
        write_hook_output(original)
        captured = capsys.readouterr()
        roundtripped = json.loads(captured.out.strip())
        assert roundtripped == original


# ---------------------------------------------------------------------------
# 7. _dedup_findings — P1-7 cross-tier finding deduplication
# ---------------------------------------------------------------------------


class TestDedupFindings:
    """Findings duplicated across Tier 1 + Tier 3 should be collapsed."""

    @staticmethod
    def _f(rule: str, file: str, line: int | None = 1, message: str = "x") -> Finding:
        return Finding(severity="error", file=file, rule=rule, message=message, fix="fix", line=line)

    def test_empty_input(self) -> None:
        assert _dedup_findings([]) == []

    def test_distinct_findings_preserved(self) -> None:
        a = self._f("V08-HARDCODED-SECRET", "/a.go", 1)
        b = self._f("V08-CORS-WILDCARD", "/a.go", 7)
        assert _dedup_findings([a, b]) == [a, b]

    def test_collapses_identical_finding(self) -> None:
        a = self._f("V08-HARDCODED-SECRET", "/a.go", 5, "AWS key")
        a_again = self._f("V08-HARDCODED-SECRET", "/a.go", 5, "AWS key")
        result = _dedup_findings([a, a_again])
        assert len(result) == 1
        # First occurrence preserved (deterministic)
        assert result[0] is a

    def test_different_lines_not_collapsed(self) -> None:
        a = self._f("V08-HARDCODED-SECRET", "/a.go", 5)
        b = self._f("V08-HARDCODED-SECRET", "/a.go", 12)
        assert len(_dedup_findings([a, b])) == 2

    def test_different_messages_not_collapsed(self) -> None:
        a = self._f("V08-HARDCODED-SECRET", "/a.go", 5, "AWS key")
        b = self._f("V08-HARDCODED-SECRET", "/a.go", 5, "GitHub token")
        # Same rule+file+line but different message — keep both since
        # the underlying detection differs.
        assert len(_dedup_findings([a, b])) == 2

    def test_format_output_uses_dedup(self) -> None:
        # When format_output sees a duplicate finding it should appear once
        # in additionalContext.
        f = self._f("V08-HARDCODED-SECRET", "/a.go", 3, "AWS key")
        out = format_output([f, f, f], mode="post_tool_use")
        assert out["additionalContext"].count("V08-HARDCODED-SECRET") == 1
