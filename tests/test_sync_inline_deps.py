"""Tests for scripts/sync_inline_deps.py — PEP 723 drift gate (P1-6)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_inline_deps.py"


@pytest.fixture(scope="module")
def sync_module():
    """Import scripts/sync_inline_deps.py as a module so we can call helpers."""
    spec = importlib.util.spec_from_file_location("sync_inline_deps", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_inline_deps"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1. _parse_inline_list — list-literal parser
# ---------------------------------------------------------------------------


class TestParseInlineList:
    def test_empty_list(self, sync_module) -> None:
        assert sync_module._parse_inline_list("[]") == []

    def test_single_item(self, sync_module) -> None:
        assert sync_module._parse_inline_list('["pyyaml>=6.0"]') == ["pyyaml>=6.0"]

    def test_multiple_items(self, sync_module) -> None:
        result = sync_module._parse_inline_list('["pyyaml>=6.0", "click>=8.0"]')
        assert result == ["pyyaml>=6.0", "click>=8.0"]

    def test_single_quotes(self, sync_module) -> None:
        assert sync_module._parse_inline_list("['pyyaml>=6.0']") == ["pyyaml>=6.0"]

    def test_extra_whitespace(self, sync_module) -> None:
        assert sync_module._parse_inline_list('[ "pyyaml>=6.0" ,  "x" ]') == ["pyyaml>=6.0", "x"]


# ---------------------------------------------------------------------------
# 2. _format_inline_list — round-trip rendering
# ---------------------------------------------------------------------------


class TestFormatInlineList:
    def test_empty(self, sync_module) -> None:
        assert sync_module._format_inline_list([]) == "[]"

    def test_single(self, sync_module) -> None:
        assert sync_module._format_inline_list(["pyyaml>=6.0"]) == '["pyyaml>=6.0"]'

    def test_multiple(self, sync_module) -> None:
        assert sync_module._format_inline_list(["a", "b"]) == '["a", "b"]'


# ---------------------------------------------------------------------------
# 3. _sync_file — drift detection on synthetic hook files
# ---------------------------------------------------------------------------


def _write_hook(path: Path, deps_line: str) -> None:
    """Create a minimal hook file with the given inline deps line."""
    path.write_text(
        '#!/usr/bin/env python3\n"""Test hook."""\n'
        "# /// script\n"
        '# requires-python = ">=3.11"\n'
        f"# dependencies = {deps_line}\n"
        "# ///\n\n"
        'print("hello")\n'
    )


class TestSyncFile:
    @pytest.fixture
    def canonical(self) -> dict[str, str]:
        return {"pyyaml": "pyyaml>=6.0", "click": "click>=8.1"}

    def test_in_sync_returns_no_drift(self, sync_module, tmp_path: Path, canonical) -> None:
        f = tmp_path / "hook.py"
        _write_hook(f, '["pyyaml>=6.0"]')
        drifted, _ = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted is False

    def test_empty_inline_list_is_subset_ok(self, sync_module, tmp_path: Path, canonical) -> None:
        # security_hook.py-style: regex only, no deps. Must NOT count as drift.
        f = tmp_path / "hook.py"
        _write_hook(f, "[]")
        drifted, _ = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted is False

    def test_version_mismatch_is_drift(self, sync_module, tmp_path: Path, canonical) -> None:
        f = tmp_path / "hook.py"
        _write_hook(f, '["pyyaml>=5.0"]')
        drifted, msg = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted is True
        assert msg is not None
        assert "version mismatch" in msg

    def test_unknown_package_is_drift(self, sync_module, tmp_path: Path, canonical) -> None:
        f = tmp_path / "hook.py"
        _write_hook(f, '["unknown-pkg>=1.0"]')
        drifted, msg = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted is True
        assert msg is not None
        assert "not in pyproject" in msg

    def test_no_pep_723_block_is_no_drift(self, sync_module, tmp_path: Path, canonical) -> None:
        f = tmp_path / "hook.py"
        f.write_text('#!/usr/bin/env python3\nprint("no script block")\n')
        drifted, _ = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted is False

    def test_auto_fix_writes_canonical_version(self, sync_module, tmp_path: Path, canonical) -> None:
        f = tmp_path / "hook.py"
        _write_hook(f, '["pyyaml>=5.0"]')
        drifted, _ = sync_module._sync_file(f, canonical, check_only=False)
        assert drifted is True
        # File should now carry the canonical version
        assert "pyyaml>=6.0" in f.read_text()
        # Re-running --check should now pass
        drifted_after, _ = sync_module._sync_file(f, canonical, check_only=True)
        assert drifted_after is False

    def test_auto_fix_skips_unknown_package(self, sync_module, tmp_path: Path, canonical) -> None:
        # Unknown packages are not auto-fixed — they need human review.
        f = tmp_path / "hook.py"
        original = '["unknown-pkg>=1.0"]'
        _write_hook(f, original)
        drifted, msg = sync_module._sync_file(f, canonical, check_only=False)
        assert drifted is True
        assert msg is not None
        assert "not in pyproject" in msg
        # File remains unchanged
        assert original in f.read_text()
