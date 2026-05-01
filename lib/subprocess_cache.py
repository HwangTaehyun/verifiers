"""Subprocess result cache — Phase 61.C.

For tools that have NO native cache (`buf lint`, `buf breaking`),
hash the input files + tool version + cmd args and cache stdout /
stderr / returncode. Cache hit returns instantly without invoking
the subprocess.

## Scope

This module is for tools where:
  - The tool does NOT have a native cache flag (buf has none).
  - The output is a deterministic function of input files + cmd args.
  - Cache miss is safe (subprocess runs and writes new entry).

Do NOT use for:
  - `eslint` / `tsc` / `golangci-lint` — already cached natively.
  - `pytest` / `go test` — output depends on system state, time,
    randomness, env. Caching tests = silent regression hiding.

## Cache key

A SHA-256 hash of:
  1. Sorted concatenation of file_contents from `input_files`.
  2. Tool version string (caller passes via `tool_version`).
  3. The exact cmd args list (joined by null byte to avoid collision).
  4. Optional extra config files (caller passes via `config_files`).

If ANY of those change, cache miss. The hash is the only key — no
TTL fallback because TTL would mean "cache could be stale even with
identical inputs", which defeats determinism.

## Storage

`<project_root>/.verifiers/state/subprocess-cache/<key_label>.json`

One file per cache key label (e.g. ``buf-lint``, ``buf-breaking``).
Each file holds at most ``MAX_ENTRIES`` recent entries (FIFO).

## TTL — 7 days mtime FIFO

Cache files older than 7 days (mtime check) are deleted on every
``cached_run`` call to the same label. This caps disk usage even if
the project never invalidates entries naturally.

## Concurrency

Atomic write (`tmp file → os.replace`) so partial writes can't poison
the cache. Reads tolerate corrupt JSON by treating the file as miss
+ wiping it.

## Escape hatch

``VERIFIERS_NO_CACHE=1`` env var disables the cache entirely (every
call goes straight to subprocess).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Cache file age limit (FIFO cleanup): 7 days.
MAX_AGE_SECONDS = 7 * 24 * 3600

# Per-cache-file max entries (older entries dropped FIFO).
MAX_ENTRIES = 32


@dataclass
class CachedResult:
    """Mirrors subprocess.CompletedProcess for the parts we cache."""

    stdout: str
    stderr: str
    returncode: int

    @classmethod
    def from_completed(cls, cp: subprocess.CompletedProcess) -> "CachedResult":
        return cls(
            stdout=cp.stdout if isinstance(cp.stdout, str) else (cp.stdout.decode() if cp.stdout else ""),
            stderr=cp.stderr if isinstance(cp.stderr, str) else (cp.stderr.decode() if cp.stderr else ""),
            returncode=cp.returncode,
        )


def _cache_disabled() -> bool:
    """Honor VERIFIERS_NO_CACHE=1 escape hatch."""
    return os.environ.get("VERIFIERS_NO_CACHE", "0") == "1"


def _cache_root(project_root: Path | str) -> Path:
    """The directory that holds all subprocess-cache JSON files."""
    return Path(project_root) / ".verifiers" / "state" / "subprocess-cache"


def _compute_hash(
    input_files: list[Path],
    cmd: list[str],
    tool_version: str,
    config_files: list[Path] | None = None,
) -> str:
    """SHA-256 of all inputs that affect subprocess output.

    Reads file bytes (deterministic). Missing files are skipped
    gracefully (their absence is part of the hash via the empty
    contribution).
    """
    h = hashlib.sha256()
    h.update(b"tool_version=" + tool_version.encode("utf-8") + b"\n")
    h.update(b"cmd=")
    h.update(b"\0".join(arg.encode("utf-8") for arg in cmd))
    h.update(b"\n")

    all_files = sorted(input_files) + sorted(config_files or [])
    for fp in all_files:
        try:
            content = fp.read_bytes()
        except OSError:
            content = b""
        h.update(b"file=" + str(fp).encode("utf-8") + b"\n")
        h.update(b"len=" + str(len(content)).encode("utf-8") + b"\n")
        h.update(content)
        h.update(b"\n")

    return h.hexdigest()


def _read_cache(cache_file: Path) -> dict:
    """Load cache entries; return {} on any error (treats as miss)."""
    if not cache_file.is_file():
        return {}
    try:
        return json.loads(cache_file.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        # Corrupt — wipe and let caller re-populate.
        try:
            cache_file.unlink()
        except OSError:
            pass
        return {}


def _write_cache(cache_file: Path, entries: dict) -> None:
    """Atomic write: tmp → os.replace."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(entries, separators=(",", ":")))
        os.replace(str(tmp), str(cache_file))
    except OSError:
        # Best-effort cleanup of stale tmp.
        try:
            tmp.unlink()
        except OSError:
            pass


def _purge_old_files(cache_root: Path) -> None:
    """FIFO cleanup: delete cache files whose mtime is older than 7 days.

    Called on every ``cached_run`` so the cache directory caps its
    disk footprint without needing a separate cron job.
    """
    if not cache_root.is_dir():
        return
    now = time.time()
    for f in cache_root.iterdir():
        if not f.is_file():
            continue
        try:
            if now - f.stat().st_mtime > MAX_AGE_SECONDS:
                f.unlink()
        except OSError:
            continue


def cached_run(
    *,
    project_root: Path | str,
    label: str,
    cmd: list[str],
    cwd: Path | str,
    input_files: list[Path],
    tool_version: str,
    config_files: list[Path] | None = None,
    timeout: int = 60,
) -> CachedResult:
    """Run ``cmd`` with a cache layer, returning ``CachedResult``.

    On cache hit: returns instantly with stored stdout/stderr/returncode.
    On cache miss: runs subprocess, stores result, returns it.
    On VERIFIERS_NO_CACHE=1: bypasses cache entirely.

    Args:
        project_root: Repository root for ``<root>/.verifiers/state/`` cache dir.
        label: Cache key label (e.g. ``"buf-lint"``). One JSON file per label.
        cmd: Subprocess command list.
        cwd: Working directory for the subprocess.
        input_files: Files whose content affects subprocess output. The
            full content is hashed.
        tool_version: Tool version string (e.g. ``"buf 1.32.2"``). Bumping
            tool version invalidates all cache entries.
        config_files: Optional additional config file paths to hash
            (e.g. ``buf.yaml``, ``buf.gen.yaml``).
        timeout: subprocess timeout in seconds.

    Raises:
        subprocess.TimeoutExpired: subprocess exceeded ``timeout``.
        FileNotFoundError: ``cmd[0]`` not on PATH.
    """
    if _cache_disabled():
        cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return CachedResult.from_completed(cp)

    cache_root = _cache_root(project_root)
    _purge_old_files(cache_root)

    cache_file = cache_root / f"{label}.json"
    key = _compute_hash(input_files, cmd, tool_version, config_files)

    entries = _read_cache(cache_file)
    hit = entries.get(key)
    if hit is not None:
        # Cache hit: refresh ordering (mark recently used) by
        # rewriting the entry at the end of the dict, then save.
        # This keeps FIFO eviction approximately LRU.
        del entries[key]
        entries[key] = hit
        _write_cache(cache_file, entries)
        return CachedResult(
            stdout=hit["stdout"],
            stderr=hit["stderr"],
            returncode=hit["returncode"],
        )

    # Cache miss — run subprocess.
    cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    result = CachedResult.from_completed(cp)

    # Add to cache (FIFO eviction).
    entries[key] = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
    while len(entries) > MAX_ENTRIES:
        # Drop oldest (insertion-order is preserved in dict since 3.7).
        oldest = next(iter(entries))
        del entries[oldest]

    _write_cache(cache_file, entries)
    return result


@functools.lru_cache(maxsize=64)
def _detect_tool_version_cached(cmd_tuple: tuple[str, ...], cwd_str: str) -> str:
    """Phase 70: process-local memoization of tool version detection.

    Tool version is stable for the lifetime of a process — the user
    isn't installing a new node/bunx/madge mid-Stop-hook. cProfile
    on ax-finance-project showed 590 ms / Stop spent on a single
    ``bunx madge --version`` cold start, all of which is wasted on
    repeat invocations within the same process. lru_cache makes the
    second-and-onward calls O(1) hash lookup (~µs).
    """
    try:
        cp = subprocess.run(
            list(cmd_tuple),
            cwd=cwd_str,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return cp.stdout.strip().splitlines()[0] if cp.stdout else "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, IndexError):
        return "unknown"


def detect_tool_version(cmd: list[str], cwd: Path | str = ".") -> str:
    """Run ``<tool> --version`` and return its stdout (one-liner).

    Returns ``"unknown"`` if the tool isn't on PATH or the call fails.
    Used as a hash component so a tool upgrade invalidates the cache.

    Phase 70: results are cached per (cmd, cwd) for the process
    lifetime via :py:func:`_detect_tool_version_cached`. Tests that
    need a fresh detection can call
    ``_detect_tool_version_cached.cache_clear()``.
    """
    return _detect_tool_version_cached(tuple(cmd), str(cwd))


# Standalone smoke test
if __name__ == "__main__":
    print("subprocess_cache module loaded", file=sys.stderr)
