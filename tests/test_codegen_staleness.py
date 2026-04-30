"""Tests for lib/codegen_staleness.py — Phase51 extraction.

The two-step (hash + mtime) algorithm was duplicated between
V02 (graphql_gen) and V03 (proto_connect). Phase51 extracted it to a
shared lib so future codegen-driven validators can reuse it.

These tests pin the contract: the extraction must produce False for
the four "skip" cases and True only when both gates trip.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lib.codegen_staleness import is_codegen_stale
from lib.hash_cache import HashCache


@pytest.fixture
def cache(tmp_path: Path) -> HashCache:
    return HashCache(cache_file=tmp_path / "cache.json")


def _write(p: Path, body: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _set_mtime(p: Path, ts: float) -> None:
    os.utime(p, (ts, ts))


# ── Skip cases (return False without tripping cache) ──────────────────


class TestSkipCases:
    """The four "no work to do" exits."""

    def test_empty_input_files_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[],
            generated_files=[_write(tmp_path / "out.go")],
        )
        assert result is False

    def test_no_existing_inputs_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        # Globs gave us paths but none of them actually exist on disk.
        ghost_inputs = [tmp_path / "ghost.proto", tmp_path / "phantom.proto"]
        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=ghost_inputs,
            generated_files=[_write(tmp_path / "out.go")],
        )
        assert result is False

    def test_empty_generated_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        # Codegen layer absent — should NOT flag staleness.
        # A different validator (e.g. V02-MISSING-FUNCTION) catches that.
        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[_write(tmp_path / "in.proto")],
            generated_files=[],
        )
        assert result is False

    def test_no_existing_generated_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[_write(tmp_path / "in.proto")],
            generated_files=[tmp_path / "ghost.go"],
        )
        assert result is False


# ── Hash gate ──────────────────────────────────────────────────────────


class TestHashGate:
    """Step 1: hash unchanged means no work, regardless of mtimes."""

    def test_unchanged_hash_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        in_file = _write(tmp_path / "in.proto", "v1")
        out_file = _write(tmp_path / "out.go", "out")

        # Prime the cache with current hash.
        is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )

        # Even with input mtime ahead of output, hash hasn't changed
        # since priming → not stale.
        _set_mtime(in_file, 1_000_000.0)
        _set_mtime(out_file, 999_999.0)

        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        assert result is False


# ── mtime gate ─────────────────────────────────────────────────────────


class TestMtimeGate:
    """Step 2: even if hash differs, output-newer-than-input means
    `make generate` ran (cache wipe / fresh checkout false-positive)."""

    def test_hash_changed_but_generated_newer_returns_false(self, cache: HashCache, tmp_path: Path) -> None:
        in_file = _write(tmp_path / "in.proto", "v1")
        out_file = _write(tmp_path / "out.go", "out")

        # Cache is empty — first call sees hash as "changed".
        # But generated mtime is AHEAD of input mtime → cache-wipe
        # false-positive should be suppressed.
        _set_mtime(in_file, 1_000.0)
        _set_mtime(out_file, 2_000.0)

        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        assert result is False


# ── Both gates pass → stale ────────────────────────────────────────────


class TestBothGatesTrip:
    def test_hash_changed_and_input_newer_returns_true(self, cache: HashCache, tmp_path: Path) -> None:
        in_file = _write(tmp_path / "in.proto", "v1")
        out_file = _write(tmp_path / "out.go", "out")

        # First call primes the cache.
        is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )

        # Now genuinely edit the input and ensure mtime > output.
        in_file.write_text("v2-changed")
        _set_mtime(in_file, 2_000.0)
        _set_mtime(out_file, 1_000.0)

        result = is_codegen_stale(
            cache=cache,
            category="x",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        assert result is True

    def test_distinct_categories_have_distinct_cache_keys(self, cache: HashCache, tmp_path: Path) -> None:
        """V02 (category='graphql') and V03 (category='proto') in the
        same project must not collide in the cache.

        HashCache.has_changed is store-and-report-False on first
        encounter (fresh-checkout safety), so we prime each category
        with a known stale hash, then mutate the input to force the
        hash gate, and verify each category resolves independently.
        """
        in_file = _write(tmp_path / "in.proto", "v1")
        out_file = _write(tmp_path / "out.go", "out")

        # Prime BOTH categories at the v1 hash.
        is_codegen_stale(
            cache=cache,
            category="a",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        is_codegen_stale(
            cache=cache,
            category="b",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )

        # Now mutate the input so v2 hash differs from v1 cached hash,
        # and ensure step 2 (mtime) also trips.
        in_file.write_text("v2-changed")
        _set_mtime(in_file, 2_000.0)
        _set_mtime(out_file, 1_000.0)

        # Both categories should now report stale (independent cache entries).
        result_a = is_codegen_stale(
            cache=cache,
            category="a",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        # Reset mtime since previous call may have re-stat'd
        _set_mtime(in_file, 2_000.0)
        _set_mtime(out_file, 1_000.0)
        result_b = is_codegen_stale(
            cache=cache,
            category="b",
            project="p",
            input_files=[in_file],
            generated_files=[out_file],
        )
        assert result_a is True
        assert result_b is True
