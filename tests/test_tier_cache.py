"""Tests for lib/tier_cache.py — Phase63 Tier 2 ↔ Tier 3 dedup."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.tier_cache import (
    TIER_CACHE_INELIGIBLE,
    CacheEntry,
    _cache_dir,
    _cache_file,
    _cache_disabled,
    clear_cache,
    compute_input_hash,
    is_cacheable,
    lookup_recent_pass,
    record_pass,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Empty project root for cache tests."""
    return tmp_path


# ── Eligibility ──────────────────────────────────────────────────────────


def test_is_cacheable_allows_safe_validators() -> None:
    assert is_cacheable("V01-env-config") is True
    assert is_cacheable("V07-ts-quality") is True
    assert is_cacheable("V14-complexity-guard") is True


def test_is_cacheable_rejects_ineligible_validators() -> None:
    # Test runners + git-state-aware are non-deterministic given file inputs.
    for vid_prefix in ("V06", "V09", "V10", "V11", "V12", "V21", "V37"):
        assert is_cacheable(f"{vid_prefix}-something") is False


def test_ineligible_set_matches_documented_list() -> None:
    """Hard-coded list must include every validator with system-state deps."""
    expected = {"V06", "V09", "V10", "V11", "V12", "V21", "V37"}
    assert TIER_CACHE_INELIGIBLE == expected


# ── compute_input_hash ───────────────────────────────────────────────────


def test_compute_input_hash_empty_patterns(project: Path) -> None:
    """Validators with no file_patterns get an empty hash (always run)."""
    assert compute_input_hash([], project) == ""


def test_compute_input_hash_no_matching_files(project: Path) -> None:
    """A pattern with zero matches still yields a stable hash (not empty)."""
    h = compute_input_hash(["**/*.nonexistent"], project)
    # No matches → hashlib.sha256() of empty string.
    assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_compute_input_hash_deterministic(project: Path) -> None:
    """Same inputs → same hash, repeatedly."""
    (project / "a.go").write_text("package main\n")
    (project / "b.go").write_text("package x\n")
    h1 = compute_input_hash(["**/*.go"], project)
    h2 = compute_input_hash(["**/*.go"], project)
    assert h1 == h2
    assert h1 != ""


def test_compute_input_hash_changes_on_content_modify(project: Path) -> None:
    """File modification → different mtime → different hash."""
    f = project / "a.go"
    f.write_text("package main\n")
    h1 = compute_input_hash(["**/*.go"], project)

    # Forward mtime to ensure ns-precision change.
    time.sleep(0.01)
    f.write_text("package main\n// changed\n")
    h2 = compute_input_hash(["**/*.go"], project)
    assert h1 != h2


def test_compute_input_hash_changes_on_file_added(project: Path) -> None:
    (project / "a.go").write_text("package main\n")
    h1 = compute_input_hash(["**/*.go"], project)

    (project / "b.go").write_text("package x\n")
    h2 = compute_input_hash(["**/*.go"], project)
    assert h1 != h2


def test_compute_input_hash_changes_on_file_removed(project: Path) -> None:
    (project / "a.go").write_text("package main\n")
    (project / "b.go").write_text("package x\n")
    h1 = compute_input_hash(["**/*.go"], project)

    (project / "b.go").unlink()
    h2 = compute_input_hash(["**/*.go"], project)
    assert h1 != h2


def test_compute_input_hash_dedups_across_patterns(project: Path) -> None:
    """Overlapping patterns shouldn't double-count the same file."""
    (project / "a.go").write_text("package main\n")

    h_dup = compute_input_hash(["**/*.go", "**/*.go"], project)
    h_single = compute_input_hash(["**/*.go"], project)
    assert h_dup == h_single


def test_compute_input_hash_skips_directories(project: Path) -> None:
    """Glob that matches a directory shouldn't crash or include it."""
    (project / "subdir").mkdir()
    (project / "subdir" / "a.go").write_text("package x\n")

    # Glob `*` matches both subdir and any top-level files.
    h = compute_input_hash(["*"], project)
    # Should not raise; result determined by top-level files (none here).
    assert isinstance(h, str)


def test_compute_input_hash_handles_invalid_pattern(project: Path) -> None:
    """Malformed glob shouldn't crash the hook."""
    (project / "a.go").write_text("x")
    # Path.glob doesn't actually error on most weird strings, but ensure
    # the OSError/ValueError swallow doesn't wreck the rest.
    h = compute_input_hash(["**/*.go", "[invalid"], project)
    assert isinstance(h, str)


# ── CacheEntry ───────────────────────────────────────────────────────────


def test_cache_entry_freshness_within_ttl() -> None:
    entry = CacheEntry(ts=time.time() - 10, input_hash="abc")
    assert entry.is_fresh(max_age_seconds=300) is True


def test_cache_entry_freshness_expired() -> None:
    entry = CacheEntry(ts=time.time() - 600, input_hash="abc")
    assert entry.is_fresh(max_age_seconds=300) is False


# ── lookup_recent_pass ───────────────────────────────────────────────────


def test_lookup_miss_when_no_cache_file(project: Path) -> None:
    assert lookup_recent_pass(project, "V07-ts-quality", "abc") is False


def test_lookup_hit_after_record(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "hash-xyz")
    assert lookup_recent_pass(project, "V07-ts-quality", "hash-xyz") is True


def test_lookup_miss_on_hash_mismatch(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "hash-old")
    assert lookup_recent_pass(project, "V07-ts-quality", "hash-new") is False


def test_lookup_miss_on_ttl_expired(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "abc")
    # Backdate the cache file so its `ts` is older than max_age_seconds.
    cache_file = _cache_file(project, "V07-ts-quality")
    payload = json.loads(cache_file.read_text())
    payload["ts"] = time.time() - 1000
    cache_file.write_text(json.dumps(payload))

    assert lookup_recent_pass(project, "V07-ts-quality", "abc", max_age_seconds=300) is False


def test_lookup_ineligible_validator_always_misses(project: Path) -> None:
    """V06 must never cache regardless of recorded state."""
    # Even if we somehow wrote a stale entry, lookup must refuse to use it.
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "V06.json").write_text(json.dumps({"ts": time.time(), "input_hash": "abc"}))

    assert lookup_recent_pass(project, "V06-go-quality", "abc") is False


def test_lookup_handles_corrupt_json(project: Path) -> None:
    """Corrupt cache file → wipe + miss, no crash."""
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "V07.json"
    cache_file.write_text("{not valid json")

    assert lookup_recent_pass(project, "V07-ts-quality", "abc") is False
    # Corrupt file should be removed.
    assert not cache_file.exists()


def test_lookup_handles_missing_keys(project: Path) -> None:
    """JSON with wrong schema → miss, no crash."""
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "V07.json"
    cache_file.write_text(json.dumps({"unrelated": "data"}))

    assert lookup_recent_pass(project, "V07-ts-quality", "abc") is False


# ── record_pass ──────────────────────────────────────────────────────────


def test_record_creates_cache_dir(project: Path) -> None:
    assert not (project / ".verifiers").exists()
    record_pass(project, "V07-ts-quality", "abc")
    assert (project / ".verifiers" / "state" / "tier-cache" / "V07.json").exists()


def test_record_atomic_write(project: Path) -> None:
    """Tmp file is replaced, not left behind."""
    record_pass(project, "V07-ts-quality", "abc")
    cache_dir = _cache_dir(project)
    files = list(cache_dir.iterdir())
    # Exactly one file, no .json.tmp leftovers.
    assert len(files) == 1
    assert files[0].name == "V07.json"


def test_record_skips_ineligible(project: Path) -> None:
    """V06 must never write a cache file."""
    record_pass(project, "V06-go-quality", "abc")
    cache_dir = _cache_dir(project)
    assert not cache_dir.exists() or not list(cache_dir.iterdir())


def test_record_overwrites_previous_entry(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "old")
    record_pass(project, "V07-ts-quality", "new")

    cache_file = _cache_file(project, "V07-ts-quality")
    payload = json.loads(cache_file.read_text())
    assert payload["input_hash"] == "new"


# ── Escape hatch ─────────────────────────────────────────────────────────


def test_escape_hatch_disables_lookup(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "abc")
    with patch.dict("os.environ", {"VERIFIERS_NO_TIER_CACHE": "1"}):
        assert _cache_disabled() is True
        assert lookup_recent_pass(project, "V07-ts-quality", "abc") is False


def test_escape_hatch_disables_record(project: Path) -> None:
    with patch.dict("os.environ", {"VERIFIERS_NO_TIER_CACHE": "1"}):
        record_pass(project, "V07-ts-quality", "abc")
    cache_dir = _cache_dir(project)
    assert not cache_dir.exists() or not list(cache_dir.iterdir())


def test_escape_hatch_off_when_env_unset(project: Path) -> None:
    """Default state: cache fully active."""
    # patch.dict + clear=False just ensures we don't accidentally inherit
    # VERIFIERS_NO_TIER_CACHE=1 from the outer env.
    import os

    saved = os.environ.pop("VERIFIERS_NO_TIER_CACHE", None)
    try:
        assert _cache_disabled() is False
    finally:
        if saved is not None:
            os.environ["VERIFIERS_NO_TIER_CACHE"] = saved


# ── clear_cache ──────────────────────────────────────────────────────────


def test_clear_cache_removes_all_entries(project: Path) -> None:
    record_pass(project, "V07-ts-quality", "abc")
    record_pass(project, "V14-complexity-guard", "def")

    cache_dir = _cache_dir(project)
    assert len(list(cache_dir.iterdir())) == 2

    clear_cache(project)
    assert len(list(cache_dir.iterdir())) == 0


def test_clear_cache_no_op_when_dir_missing(project: Path) -> None:
    """Should not raise when there's nothing to clear."""
    clear_cache(project)  # no exception


# ── End-to-end: real file changes drive cache invalidation ───────────────


def test_e2e_cache_invalidates_on_file_change(project: Path) -> None:
    """Edit a tracked file → previously cached PASS no longer hits."""
    (project / "a.ts").write_text("export const x = 1;\n")
    h1 = compute_input_hash(["**/*.ts"], project)
    record_pass(project, "V07-ts-quality", h1)

    assert lookup_recent_pass(project, "V07-ts-quality", h1) is True

    # Modify the file → input hash changes → cache miss.
    time.sleep(0.01)
    (project / "a.ts").write_text("export const x = 2;\n")
    h2 = compute_input_hash(["**/*.ts"], project)
    assert h1 != h2
    assert lookup_recent_pass(project, "V07-ts-quality", h2) is False


def test_e2e_cache_holds_when_unrelated_file_changes(project: Path) -> None:
    """Editing a .py file shouldn't invalidate a V07 (.ts) cache."""
    (project / "a.ts").write_text("export const x = 1;\n")
    h1 = compute_input_hash(["**/*.ts"], project)
    record_pass(project, "V07-ts-quality", h1)

    # Add an unrelated .py file.
    time.sleep(0.01)
    (project / "b.py").write_text("x = 1\n")
    h2 = compute_input_hash(["**/*.ts"], project)
    assert h1 == h2  # .ts hash unchanged
    assert lookup_recent_pass(project, "V07-ts-quality", h2) is True
