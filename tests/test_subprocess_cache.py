"""Tests for lib/subprocess_cache.py — Phase 61.C.

Verifies cache hit/miss semantics, hash invalidation on input
changes, 7-day mtime FIFO cleanup, atomic write, corrupt-file
recovery, and VERIFIERS_NO_CACHE escape hatch.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.subprocess_cache import (
    MAX_AGE_SECONDS,
    MAX_ENTRIES,
    CachedResult,
    cached_run,
)


def _write(p: Path, body: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ── Cache hit / miss ───────────────────────────────────────────────────


class TestCacheHitMiss:
    def test_first_call_runs_subprocess(self, tmp_path: Path) -> None:
        """Cache miss on first invocation."""
        in_file = _write(tmp_path / "x.proto", 'syntax = "proto3";\n')

        with patch("lib.subprocess_cache.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "CP",
                (),
                {
                    "stdout": "ok",
                    "stderr": "",
                    "returncode": 0,
                },
            )()
            result = cached_run(
                project_root=tmp_path,
                label="test-tool",
                cmd=["echo", "x"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )
            assert mock_run.called
            assert result.stdout == "ok"
            assert result.returncode == 0

    def test_second_call_same_input_returns_from_cache(self, tmp_path: Path) -> None:
        """Cache hit on identical inputs."""
        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "CP",
                (),
                {
                    "stdout": "first",
                    "stderr": "",
                    "returncode": 0,
                },
            )()
            cached_run(
                project_root=tmp_path,
                label="test-tool",
                cmd=["echo", "x"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )
            assert mock_run.call_count == 1

        # Second call: subprocess should NOT be invoked (cache hit).
        with patch("lib.subprocess_cache.subprocess.run") as mock_run2:
            result = cached_run(
                project_root=tmp_path,
                label="test-tool",
                cmd=["echo", "x"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )
            assert mock_run2.call_count == 0
            assert result.stdout == "first"  # cached value returned

    def test_input_change_invalidates_cache(self, tmp_path: Path) -> None:
        """Edit input file → cache miss."""
        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as m1:
            m1.return_value = type("CP", (), {"stdout": "v1-out", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )

        # Modify input → hash changes → miss.
        in_file.write_text("v2")

        with patch("lib.subprocess_cache.subprocess.run") as m2:
            m2.return_value = type("CP", (), {"stdout": "v2-out", "stderr": "", "returncode": 0})()
            result = cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )
            assert m2.call_count == 1
            assert result.stdout == "v2-out"

    def test_tool_version_change_invalidates_cache(self, tmp_path: Path) -> None:
        """Tool upgrade (different version string) → cache miss."""
        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as m1:
            m1.return_value = type("CP", (), {"stdout": "old", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1.0.0",
                timeout=5,
            )

        with patch("lib.subprocess_cache.subprocess.run") as m2:
            m2.return_value = type("CP", (), {"stdout": "new", "stderr": "", "returncode": 0})()
            result = cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="2.0.0",  # bumped
                timeout=5,
            )
            assert m2.call_count == 1
            assert result.stdout == "new"

    def test_cmd_args_change_invalidates_cache(self, tmp_path: Path) -> None:
        """Different cmd args → cache miss."""
        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as m1:
            m1.return_value = type("CP", (), {"stdout": "lint", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["buf", "lint"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )

        with patch("lib.subprocess_cache.subprocess.run") as m2:
            m2.return_value = type("CP", (), {"stdout": "breaking", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["buf", "breaking"],  # different
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )
            assert m2.call_count == 1


# ── 7-day TTL FIFO cleanup ─────────────────────────────────────────────


class TestTTLCleanup:
    def test_old_cache_files_purged(self, tmp_path: Path) -> None:
        """Cache files older than 7 days are deleted."""
        cache_dir = tmp_path / ".verifiers" / "state" / "subprocess-cache"
        cache_dir.mkdir(parents=True)

        old_file = cache_dir / "old.json"
        old_file.write_text("{}")
        old_mtime = time.time() - (MAX_AGE_SECONDS + 100)
        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = cache_dir / "recent.json"
        recent_file.write_text("{}")  # mtime = now

        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as m:
            m.return_value = type("CP", (), {"stdout": "", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )

        # Old file should be gone, recent file should remain.
        assert not old_file.exists()
        assert recent_file.exists()

    def test_recent_cache_files_kept(self, tmp_path: Path) -> None:
        """Files within 7 days are not purged."""
        cache_dir = tmp_path / ".verifiers" / "state" / "subprocess-cache"
        cache_dir.mkdir(parents=True)
        recent = cache_dir / "yesterday.json"
        recent.write_text("{}")
        # mtime = 1 day ago (well within 7-day window)
        recent_mtime = time.time() - 86400
        os.utime(recent, (recent_mtime, recent_mtime))

        in_file = _write(tmp_path / "x.proto", "v1")
        with patch("lib.subprocess_cache.subprocess.run") as m:
            m.return_value = type("CP", (), {"stdout": "", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )

        assert recent.exists()


# ── FIFO entry eviction ────────────────────────────────────────────────


class TestEntryEviction:
    def test_oldest_entry_dropped_when_max_exceeded(self, tmp_path: Path) -> None:
        """At MAX_ENTRIES + 1, oldest insertion is dropped."""
        in_file = _write(tmp_path / "x.proto", "base")

        # Insert MAX_ENTRIES + 5 distinct entries.
        for i in range(MAX_ENTRIES + 5):
            with patch("lib.subprocess_cache.subprocess.run") as m:
                m.return_value = type(
                    "CP",
                    (),
                    {
                        "stdout": f"out{i}",
                        "stderr": "",
                        "returncode": 0,
                    },
                )()
                cached_run(
                    project_root=tmp_path,
                    label="t",
                    cmd=["echo", str(i)],  # unique cmd → unique hash
                    cwd=tmp_path,
                    input_files=[in_file],
                    tool_version="1",
                    timeout=5,
                )

        cache_file = tmp_path / ".verifiers" / "state" / "subprocess-cache" / "t.json"
        entries = json.loads(cache_file.read_text())
        assert len(entries) <= MAX_ENTRIES


# ── Corrupt-file recovery ──────────────────────────────────────────────


class TestCorruptRecovery:
    def test_corrupt_json_treated_as_miss(self, tmp_path: Path) -> None:
        """Bad JSON → treated as miss, file wiped, subprocess invoked."""
        cache_dir = tmp_path / ".verifiers" / "state" / "subprocess-cache"
        cache_dir.mkdir(parents=True)
        bad = cache_dir / "t.json"
        bad.write_text("{not valid json")

        in_file = _write(tmp_path / "x.proto", "v1")
        with patch("lib.subprocess_cache.subprocess.run") as m:
            m.return_value = type("CP", (), {"stdout": "ok", "stderr": "", "returncode": 0})()
            result = cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )
            # Corrupt cache wiped, subprocess called, result = subprocess result.
            assert m.call_count == 1
            assert result.stdout == "ok"


# ── VERIFIERS_NO_CACHE escape hatch ────────────────────────────────────


class TestEscapeHatch:
    def test_no_cache_env_bypasses_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """VERIFIERS_NO_CACHE=1 → never reads/writes cache file."""
        monkeypatch.setenv("VERIFIERS_NO_CACHE", "1")
        in_file = _write(tmp_path / "x.proto", "v1")

        with patch("lib.subprocess_cache.subprocess.run") as m1:
            m1.return_value = type("CP", (), {"stdout": "first", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )

        # Even with same inputs, second call should re-run subprocess (no cache used).
        with patch("lib.subprocess_cache.subprocess.run") as m2:
            m2.return_value = type("CP", (), {"stdout": "second", "stderr": "", "returncode": 0})()
            result = cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )
            assert m2.call_count == 1
            assert result.stdout == "second"

        # Cache file should NOT have been created.
        cache_file = tmp_path / ".verifiers" / "state" / "subprocess-cache" / "t.json"
        assert not cache_file.exists()


# ── Atomic write ───────────────────────────────────────────────────────


class TestAtomicWrite:
    def test_no_partial_tmp_file_on_disk_after_normal_run(self, tmp_path: Path) -> None:
        """Normal run leaves no .tmp leftover."""
        in_file = _write(tmp_path / "x.proto", "v1")
        with patch("lib.subprocess_cache.subprocess.run") as m:
            m.return_value = type("CP", (), {"stdout": "ok", "stderr": "", "returncode": 0})()
            cached_run(
                project_root=tmp_path,
                label="t",
                cmd=["echo"],
                cwd=tmp_path,
                input_files=[in_file],
                tool_version="1",
                timeout=5,
            )

        cache_dir = tmp_path / ".verifiers" / "state" / "subprocess-cache"
        leftovers = list(cache_dir.glob("*.tmp"))
        assert leftovers == []


# ── CachedResult.from_completed ─────────────────────────────────────────


class TestCachedResult:
    def test_from_completed_string_io(self) -> None:
        cp = type("CP", (), {"stdout": "out", "stderr": "err", "returncode": 1})()
        r = CachedResult.from_completed(cp)
        assert r.stdout == "out"
        assert r.stderr == "err"
        assert r.returncode == 1

    def test_from_completed_bytes_io(self) -> None:
        cp = type("CP", (), {"stdout": b"out", "stderr": b"err", "returncode": 0})()
        r = CachedResult.from_completed(cp)
        assert r.stdout == "out"
        assert r.stderr == "err"
