"""Tests for lib/router_cache.py — Tier 2 router content-hash cache (P2-1).

Classical-school style: every test exercises the real JSON file on a
``tmp_path`` and verifies the observable result (cache contents, hash
match decisions). No internal call-order mocking; the cache is pure
I/O so it lends itself naturally.
"""

from __future__ import annotations

from pathlib import Path

from lib.router_cache import (
    cache_path,
    file_content_hash,
    load_cache,
    record_hit,
    save_cache,
    should_skip,
)


# ---------------------------------------------------------------------------
# 1. cache_path — canonical location
# ---------------------------------------------------------------------------


class TestCachePath:
    def test_path_under_verifiers_state(self, tmp_path: Path) -> None:
        assert cache_path(tmp_path) == tmp_path / ".verifiers" / "state" / "router-cache.json"


# ---------------------------------------------------------------------------
# 2. file_content_hash — sha256 of bytes
# ---------------------------------------------------------------------------


class TestFileContentHash:
    def test_same_path_same_content_same_hash(self, tmp_path: Path) -> None:
        # Phase37 (S3 audit): the digest now binds the path, so the
        # same path with the same content remains stable run-to-run.
        a = tmp_path / "a.txt"
        a.write_bytes(b"hello")
        assert file_content_hash(str(a)) == file_content_hash(str(a))

    def test_same_content_different_paths_different_hash(self, tmp_path: Path) -> None:
        # Phase37 (S3 audit): identical bytes at different paths must
        # produce different hashes — otherwise an attacker can
        # pre-record ``router-cache.json`` with ``src/auth.py → <hash
        # of malicious bytes>`` and Claude later writing those bytes
        # would skip V08. Path binding makes the pre-record useless.
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello")
        b.write_bytes(b"hello")
        assert file_content_hash(str(a)) != file_content_hash(str(b))

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello")
        b.write_bytes(b"world")
        assert file_content_hash(str(a)) != file_content_hash(str(b))

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert file_content_hash(str(tmp_path / "nonexistent.txt")) is None


# ---------------------------------------------------------------------------
# 3. load_cache — defensive parsing
# ---------------------------------------------------------------------------


class TestLoadCache:
    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_cache(tmp_path) == {}

    def test_round_trip(self, tmp_path: Path) -> None:
        original = {"/abs/file.go": "abc123", "/abs/other.py": "def456"}
        save_cache(tmp_path, original)
        assert load_cache(tmp_path) == original

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        path = cache_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        assert load_cache(tmp_path) == {}

    def test_top_level_list_returns_empty(self, tmp_path: Path) -> None:
        path = cache_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('["a", "b"]')
        assert load_cache(tmp_path) == {}

    def test_filters_non_string_entries(self, tmp_path: Path) -> None:
        path = cache_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Mixed types — only str→str pairs survive.
        path.write_text('{"good/path": "abc", "with-int-value": 123, "list": [1]}')
        assert load_cache(tmp_path) == {"good/path": "abc"}


# ---------------------------------------------------------------------------
# 4. should_skip — main routing decision
# ---------------------------------------------------------------------------


class TestShouldSkip:
    def test_match_returns_true(self) -> None:
        cache = {"/abs/foo.py": "abc"}
        assert should_skip(cache, "/abs/foo.py", "abc") is True

    def test_mismatch_returns_false(self) -> None:
        cache = {"/abs/foo.py": "abc"}
        assert should_skip(cache, "/abs/foo.py", "xyz") is False

    def test_unknown_file_returns_false(self) -> None:
        assert should_skip({}, "/abs/foo.py", "abc") is False

    def test_none_hash_returns_false(self) -> None:
        cache = {"/abs/foo.py": "abc"}
        # When the file can't be hashed (e.g. just deleted), we never skip.
        assert should_skip(cache, "/abs/foo.py", None) is False


# ---------------------------------------------------------------------------
# 5. record_hit + persistence
# ---------------------------------------------------------------------------


class TestRecordHit:
    def test_records_new_entry(self) -> None:
        cache: dict[str, str] = {}
        record_hit(cache, "/abs/foo.py", "abc")
        assert cache == {"/abs/foo.py": "abc"}

    def test_overwrites_existing(self) -> None:
        cache = {"/abs/foo.py": "old"}
        record_hit(cache, "/abs/foo.py", "new")
        assert cache["/abs/foo.py"] == "new"

    def test_re_insert_moves_to_end(self) -> None:
        cache: dict[str, str] = {}
        record_hit(cache, "/a.py", "h1")
        record_hit(cache, "/b.py", "h2")
        record_hit(cache, "/a.py", "h3")  # re-touch the older key
        # /a.py should now be at the end of insertion order, /b.py first.
        assert list(cache.keys()) == ["/b.py", "/a.py"]

    def test_none_hash_is_noop(self) -> None:
        cache = {"/foo.py": "abc"}
        record_hit(cache, "/foo.py", None)
        assert cache == {"/foo.py": "abc"}

    def test_persists_across_reload(self, tmp_path: Path) -> None:
        cache: dict[str, str] = load_cache(tmp_path)
        record_hit(cache, "/foo.py", "abc")
        save_cache(tmp_path, cache)

        reloaded = load_cache(tmp_path)
        assert reloaded == {"/foo.py": "abc"}


# ---------------------------------------------------------------------------
# 6. save_cache — FIFO eviction at the cap
# ---------------------------------------------------------------------------


class TestSaveCacheEviction:
    def test_within_cap_no_eviction(self, tmp_path: Path) -> None:
        cache = {f"/file{i}.py": f"hash{i}" for i in range(10)}
        save_cache(tmp_path, cache)
        assert load_cache(tmp_path) == cache

    def test_over_cap_drops_oldest(self, tmp_path: Path) -> None:
        # 1100 entries — the cap is 1000, eviction target is 800.
        # After save_cache, expect the LAST 800 entries to remain.
        cache: dict[str, str] = {}
        for i in range(1100):
            record_hit(cache, f"/file{i:04d}.py", f"hash{i}")

        save_cache(tmp_path, cache)
        reloaded = load_cache(tmp_path)

        # 800 entries left
        assert len(reloaded) == 800
        # The newest entries survive (file0300..file1099)
        assert "/file1099.py" in reloaded
        assert "/file0300.py" in reloaded
        # The oldest entries are gone
        assert "/file0000.py" not in reloaded
        assert "/file0299.py" not in reloaded


# ---------------------------------------------------------------------------
# 7. Integration — what the router actually does end-to-end
# ---------------------------------------------------------------------------


class TestRouterCacheFlow:
    """Mirrors the exact sequence in hooks/router.py.

    No mocks: real files, real cache, real hashes. Verifies the
    behaviour the router relies on: 'don't run validators twice on
    identical content'.
    """

    def test_first_run_no_skip_then_repeat_skips(self, tmp_path: Path) -> None:
        src = tmp_path / "main.py"
        src.write_text("print('hello')\n")

        # First run — cache empty, no skip.
        cache = load_cache(tmp_path)
        h1 = file_content_hash(str(src))
        assert should_skip(cache, str(src), h1) is False

        # Router would now run validators, then record + save.
        record_hit(cache, str(src), h1)
        save_cache(tmp_path, cache)

        # Second run with identical content — should skip.
        cache2 = load_cache(tmp_path)
        h2 = file_content_hash(str(src))
        assert should_skip(cache2, str(src), h2) is True

    def test_content_change_invalidates_skip(self, tmp_path: Path) -> None:
        src = tmp_path / "main.py"
        src.write_text("print('hello')\n")

        cache = load_cache(tmp_path)
        h1 = file_content_hash(str(src))
        record_hit(cache, str(src), h1)
        save_cache(tmp_path, cache)

        # Modify the file
        src.write_text("print('world')\n")

        cache2 = load_cache(tmp_path)
        h2 = file_content_hash(str(src))
        assert should_skip(cache2, str(src), h2) is False
