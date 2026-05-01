"""Tests for lib/per_file_cache.py — Phase 64.4 per-file findings cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.validators.base import Finding
from lib.per_file_cache import (
    CACHE_VERSION,
    MAX_ENTRIES,
    PerFileCache,
    _cache_dir,
    _cache_file,
    _cache_disabled,
    clear_cache,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


def _f(rule: str, file: str = "/x.go", line: int | None = 1) -> Finding:
    return Finding(
        severity="warning",
        file=file,
        rule=rule,
        message="m",
        fix="f",
        line=line,
    )


# ── Load / save round-trip ──────────────────────────────────────────────


def test_load_returns_empty_when_no_file(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert cache.size == 0


def test_put_then_get_round_trips(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    findings = [_f("V14-LONG"), _f("V14-DEEP", line=10)]
    cache.put("/x.go", mtime_ns=12345, findings=findings)

    out = cache.get("/x.go", mtime_ns=12345)
    assert out is not None
    assert len(out) == 2
    assert {f.rule for f in out} == {"V14-LONG", "V14-DEEP"}


def test_get_miss_on_mtime_mismatch(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    cache.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    assert cache.get("/x.go", mtime_ns=200) is None


def test_save_then_load_persists(project: Path) -> None:
    c1 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    c1.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    c1.save()

    c2 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert c2.size == 1
    out = c2.get("/x.go", mtime_ns=100)
    assert out is not None
    assert out[0].rule == "V14-X"


def test_save_no_op_when_clean(project: Path) -> None:
    """Loading + saving without put() should not write a file."""
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    cache.save()
    cache_file = _cache_file(project, "V14-complexity")
    assert not cache_file.exists()


def test_save_creates_directory(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    cache.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    cache.save()
    assert (project / ".verifiers" / "state" / "per-file-cache" / "V14.json").exists()


def test_save_atomic_no_tmp_leftover(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    cache.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    cache.save()

    cache_dir = _cache_dir(project)
    files = sorted(f.name for f in cache_dir.iterdir())
    assert files == ["V14.json"]


# ── Config fingerprint invalidation ─────────────────────────────────────


def test_config_fingerprint_change_invalidates(project: Path) -> None:
    """Different fingerprint on load → empty cache."""
    c1 = PerFileCache.load(project, "V14-complexity", config_fingerprint="old")
    c1.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    c1.save()

    # Same validator, different fingerprint → cache wiped on load.
    c2 = PerFileCache.load(project, "V14-complexity", config_fingerprint="new")
    assert c2.size == 0
    assert c2.get("/x.go", mtime_ns=100) is None


def test_same_fingerprint_preserves(project: Path) -> None:
    c1 = PerFileCache.load(project, "V14-complexity", config_fingerprint="same")
    c1.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    c1.save()

    c2 = PerFileCache.load(project, "V14-complexity", config_fingerprint="same")
    assert c2.size == 1


# ── Robustness ──────────────────────────────────────────────────────────


def test_corrupt_json_yields_empty_cache(project: Path) -> None:
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "V14.json").write_text("{not valid")

    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert cache.size == 0
    # Corrupt file should be wiped.
    assert not (cache_dir / "V14.json").exists()


def test_wrong_version_yields_empty_cache(project: Path) -> None:
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION + 1, "files": {"/x.go": {"mtime_ns": 1, "findings": []}}}
    (cache_dir / "V14.json").write_text(json.dumps(payload))

    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert cache.size == 0


def test_missing_files_key_handled(project: Path) -> None:
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION, "config_fingerprint": "abc"}  # no "files"
    (cache_dir / "V14.json").write_text(json.dumps(payload))

    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert cache.size == 0


def test_malformed_finding_dict_skipped(project: Path) -> None:
    """One bad finding entry shouldn't poison the rest of the file's cache."""
    cache_dir = _cache_dir(project)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "config_fingerprint": "abc",
        "files": {
            "/x.go": {
                "mtime_ns": 100,
                "findings": [
                    "not a dict",
                    {"severity": "warning", "rule": "V14-OK", "file": "/x.go", "message": "m", "fix": "f"},
                ],
            }
        },
    }
    (cache_dir / "V14.json").write_text(json.dumps(payload))

    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    out = cache.get("/x.go", mtime_ns=100)
    assert out is not None
    # The malformed string entry is dropped; the good one survives.
    assert len(out) == 1
    assert out[0].rule == "V14-OK"


# ── Eviction ────────────────────────────────────────────────────────────


def test_eviction_caps_at_max_entries(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    # Insert MAX_ENTRIES + 100 entries.
    for i in range(MAX_ENTRIES + 100):
        cache.put(f"/file_{i}.go", mtime_ns=i, findings=[_f(f"V14-R{i}")])
    cache.save()

    c2 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    assert c2.size == MAX_ENTRIES


# ── Escape hatch ────────────────────────────────────────────────────────


def test_escape_hatch_disables_lookup(project: Path) -> None:
    cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    cache.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    cache.save()

    with patch.dict(os.environ, {"VERIFIERS_NO_PER_FILE_CACHE": "1"}):
        assert _cache_disabled() is True
        c2 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
        assert c2.size == 0
        assert c2.get("/x.go", mtime_ns=100) is None


def test_escape_hatch_disables_record(project: Path) -> None:
    with patch.dict(os.environ, {"VERIFIERS_NO_PER_FILE_CACHE": "1"}):
        cache = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
        cache.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
        cache.save()
    # Outside the patch, cache file shouldn't have been written.
    assert not _cache_file(project, "V14-complexity").exists()


# ── clear_cache ─────────────────────────────────────────────────────────


def test_clear_cache_removes_one_validator(project: Path) -> None:
    c1 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    c1.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    c1.save()
    c2 = PerFileCache.load(project, "V15-deps", config_fingerprint="abc")
    c2.put("/y.go", mtime_ns=200, findings=[_f("V15-Y")])
    c2.save()

    clear_cache(project, validator_id="V14-complexity")
    assert not _cache_file(project, "V14-complexity").exists()
    assert _cache_file(project, "V15-deps").exists()


def test_clear_cache_removes_all_when_no_validator(project: Path) -> None:
    c1 = PerFileCache.load(project, "V14-complexity", config_fingerprint="abc")
    c1.put("/x.go", mtime_ns=100, findings=[_f("V14-X")])
    c1.save()
    c2 = PerFileCache.load(project, "V15-deps", config_fingerprint="abc")
    c2.put("/y.go", mtime_ns=200, findings=[_f("V15-Y")])
    c2.save()

    clear_cache(project)
    cache_dir = _cache_dir(project)
    assert len(list(cache_dir.iterdir())) == 0


def test_clear_cache_no_op_when_dir_missing(project: Path) -> None:
    """Should not raise even when nothing exists."""
    clear_cache(project)


# ── End-to-end — cache integration with V14 ─────────────────────────────


def test_v14_uses_cache_when_file_unchanged(project: Path) -> None:
    """V14's _analyze_file_cached returns cached findings on mtime match."""
    from hooks.validators.complexity_guard import (
        ComplexityGuardValidator,
        _complexity_fingerprint,
    )
    from lib.config_loader import ComplexityThresholds

    src = project / "x.py"
    src.write_text("def f():\n    pass\n")

    thresholds = ComplexityThresholds()
    cache = PerFileCache.load(project, "V14-complexity-guard", config_fingerprint=_complexity_fingerprint(thresholds))
    validator = ComplexityGuardValidator()

    # First call — analyze + cache.
    fresh = validator._analyze_file_cached(str(src), thresholds, cache)
    # Second call with same mtime — should hit cache (return the same).
    cached = validator._analyze_file_cached(str(src), thresholds, cache)
    assert [f.rule for f in fresh] == [f.rule for f in cached]


def test_v14_invalidates_cache_when_file_modified(project: Path) -> None:
    from hooks.validators.complexity_guard import (
        ComplexityGuardValidator,
        _complexity_fingerprint,
    )
    from lib.config_loader import ComplexityThresholds

    src = project / "x.py"
    src.write_text("def f():\n    pass\n")

    thresholds = ComplexityThresholds()
    cache = PerFileCache.load(project, "V14-complexity-guard", config_fingerprint=_complexity_fingerprint(thresholds))
    validator = ComplexityGuardValidator()

    validator._analyze_file_cached(str(src), thresholds, cache)
    initial_size = cache.size

    # Modify the file → mtime changes → cache misses + records new entry.
    import time

    time.sleep(0.02)
    src.write_text("def f():\n    if True:\n        pass\n")
    validator._analyze_file_cached(str(src), thresholds, cache)
    # The cached entry got overwritten with the new mtime, size unchanged.
    assert cache.size == initial_size
