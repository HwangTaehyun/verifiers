"""Tier 3 (Stop hook) result cache — Phase 63.

Caches validator PASS state (no findings) keyed by the project's
file-state hash. On the next Stop hook invocation, if a validator's
inputs haven't changed AND the cache entry is younger than ``max_age``,
skip the validator entirely.

## Why this exists

Stop hook latency profile (after Phase 61):
  - V06 go-quality (build + lint + test parallel)        ~30-90s
  - V07 ts-quality (eslint + tsc + madge + knip)         ~10-30s
  - V21 pytest                                            ~5-180s
  - 39 other validators                                   <500ms each

A common workflow pattern:
  1. user edits a .ts file → Tier 2 V07 runs (fast, single file)
  2. user types "stop" → Tier 3 runs ALL validators, including
     full project V07 (~30s)
  3. user immediately edits another .ts file → Tier 2 V07
  4. user "stop" → Tier 3 again (~30s) on essentially the same project
                   minus a single file change

If between two Stop hooks, only the .ts files changed, then the
Go side validators (V06, V25, V27, V34-V39, V47, V49, V50)
ran for nothing — their inputs didn't change. Phase 63 skips them
on second+ invocation.

## Algorithm

1. **Compute input hash per validator**: hash (path, size, mtime) of
   all files matching ``validator.file_patterns`` in ``ctx.project_root``.
   Stat-based (no content read) to keep this fast — ~10ms per validator
   even with 2000+ matching files.

2. **Lookup cache**: read ``<root>/.verifiers/state/tier-cache/<V##>.json``.
   If exists AND ``now - cached.ts <= max_age_seconds`` AND
   ``cached.input_hash == current_hash`` → cache HIT, skip validator.

3. **Run remaining validators** via the normal parallel runner.

4. **Record PASS**: for each validator that ran AND produced 0 findings,
   write a fresh cache entry. Validators that produced findings (or
   timed-out / crashed sentinels) are NOT cached — they should re-run
   next time so the user keeps seeing the issue until fixed.

## Per-validator opt-out

Some validators are inherently non-deterministic given file inputs
(test runners, git-state-dependent checks). They MUST NOT be cached
because a PASS today doesn't mean PASS tomorrow with no file changes.

Hard-coded exclusion list in ``TIER_CACHE_INELIGIBLE``:

  V06   go-quality        — `go test` is system-state-dependent
  V09   go-test-runner    — test execution
  V10   ts-test-runner    — test execution
  V11   py-test-runner    — test execution
  V21   py-pytest         — test execution
  V12   commit-discipline — depends on `git log` state
  V37   go-test-race      — workflow CHECK, but git-state aware

## TTL

Default 5 minutes (300s). Configurable via
``.verifiers/config.yaml``:

  tier_cache:
    enabled: true
    max_age_seconds: 300

The TTL caps stale-cache risk: even if the file hash mismatches don't
catch a change (e.g. clock skew, NFS mtime weirdness), entries
auto-expire within minutes.

## Storage

  <root>/.verifiers/state/tier-cache/<V##>.json
    {"ts": 1730000000.0, "input_hash": "<sha256>"}

One file per validator. Atomic write (tmp → os.replace).
Corrupt file → wipe + treat as miss.

## Concurrency

Two Stop hooks racing (rare) → last-write-wins. No corruption.
Per-file atomicity via os.replace.

## Escape hatch

``VERIFIERS_NO_TIER_CACHE=1`` env var disables the entire mechanism.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# Validators whose result is non-deterministic given file inputs.
# These are NEVER cached.
TIER_CACHE_INELIGIBLE: set[str] = {
    "V06",  # go-quality includes go test (system state)
    "V09",  # go-test-runner
    "V10",  # ts-test-runner
    "V11",  # py-test-runner
    "V12",  # commit-discipline (git log state)
    "V21",  # py-pytest
    "V37",  # go-test-race (workflow check, git aware)
}


@dataclass
class CacheEntry:
    ts: float
    input_hash: str

    def is_fresh(self, max_age_seconds: int) -> bool:
        return (time.time() - self.ts) <= max_age_seconds


def _cache_disabled() -> bool:
    """Honor VERIFIERS_NO_TIER_CACHE=1 escape hatch."""
    return os.environ.get("VERIFIERS_NO_TIER_CACHE", "0") == "1"


def _vid_prefix(validator_id: str) -> str:
    """Extract V-NN prefix from a validator id."""
    return validator_id.split("-", 1)[0]


def is_cacheable(validator_id: str) -> bool:
    """Return True if this validator's result is safe to cache."""
    return _vid_prefix(validator_id) not in TIER_CACHE_INELIGIBLE


def _cache_dir(project_root: Path | str) -> Path:
    return Path(project_root) / ".verifiers" / "state" / "tier-cache"


def _cache_file(project_root: Path | str, validator_id: str) -> Path:
    return _cache_dir(project_root) / f"{_vid_prefix(validator_id)}.json"


def compute_input_hash(
    file_patterns: list[str],
    project_root: Path | str,
    exclude_paths: list[str] | tuple[str, ...] = (),
) -> str:
    """Hash (path, size, mtime) of all files matching ``file_patterns``.

    Stat-based (no content read) for speed. Captures:
      - additions (new file → new path in hash)
      - deletions (missing file → missing entry in hash)
      - modifications (mtime/size change)

    Phase64.1: ``exclude_paths`` (gitignore-style globs, typically
    ``ctx.config.exclude.paths``) are filtered out of the hash. This
    matters in two ways:

      1. **Speed**: monorepos with vendored deps (``vendor/**``,
         ``node_modules/**``) can have thousands of files matching
         ``**/*.go`` / ``**/*.ts``. Stat-ing every one of them on every
         Stop hook costs 100-200ms even though those files are excluded
         from validation anyway.
      2. **Correctness**: a file that's excluded from validation should
         not invalidate the validator's cache when it changes. Without
         this filter, ``git pull`` on a vendored dep would invalidate
         every validator's cache even though the vendored code is
         never actually checked.

    Empty ``file_patterns`` → empty hash (rare; validators with no
    file_patterns run on every invocation).
    """
    if not file_patterns:
        return ""

    root = Path(project_root)
    h = hashlib.sha256()
    seen: set[Path] = set()
    # Pre-resolve project root once for relative-path computation in the
    # exclusion check. ``Path.resolve()`` per-file would dominate the
    # walk on large trees.
    root_resolved = root.resolve()

    for pattern in file_patterns:
        try:
            for f in root.glob(pattern):
                if not f.is_file():
                    continue
                resolved = f.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                # Phase64.1: skip files matching exclude_paths. Compute
                # the relative path once; if it lives outside the root
                # (rare for hook inputs), skip exclusion check and let
                # the file in.
                if exclude_paths:
                    try:
                        rel = str(resolved.relative_to(root_resolved))
                    except ValueError:
                        rel = str(f)
                    if any(fnmatch(rel, pat) for pat in exclude_paths):
                        continue
                try:
                    stat = f.stat()
                    h.update(f"{f}:{stat.st_size}:{stat.st_mtime_ns}\n".encode("utf-8"))
                except OSError:
                    continue
        except (OSError, ValueError):
            # Pattern invalid or path traversal — skip.
            continue

    return h.hexdigest()


def lookup_recent_pass(
    project_root: Path | str,
    validator_id: str,
    input_hash: str,
    max_age_seconds: int = 300,
) -> bool:
    """Return True if cache says this validator passed recently with same input.

    Returns False on:
      - cache disabled (VERIFIERS_NO_TIER_CACHE=1)
      - validator in TIER_CACHE_INELIGIBLE
      - cache file missing / corrupt
      - input_hash mismatch
      - entry older than max_age_seconds
    """
    if _cache_disabled():
        return False
    if not is_cacheable(validator_id):
        return False

    cache_file = _cache_file(project_root, validator_id)
    if not cache_file.is_file():
        return False

    try:
        data = json.loads(cache_file.read_text(errors="replace"))
        entry = CacheEntry(ts=float(data["ts"]), input_hash=str(data["input_hash"]))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # Corrupt — wipe and treat as miss.
        try:
            cache_file.unlink()
        except OSError:
            pass
        return False

    if not entry.is_fresh(max_age_seconds):
        return False
    return entry.input_hash == input_hash


def record_pass(project_root: Path | str, validator_id: str, input_hash: str) -> None:
    """Persist a PASS entry. No-op if validator is not cacheable."""
    if _cache_disabled():
        return
    if not is_cacheable(validator_id):
        return

    cache_dir = _cache_dir(project_root)
    cache_dir.mkdir(parents=True, exist_ok=True)

    entry = {"ts": time.time(), "input_hash": input_hash}
    cache_file = _cache_file(project_root, validator_id)
    tmp = cache_file.with_suffix(".json.tmp")

    try:
        tmp.write_text(json.dumps(entry, separators=(",", ":")))
        os.replace(str(tmp), str(cache_file))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def clear_cache(project_root: Path | str) -> None:
    """Wipe the entire tier-cache directory.

    Used by tests and as a manual recovery handle when the cache is
    suspected of being stale.
    """
    cache_dir = _cache_dir(project_root)
    if not cache_dir.is_dir():
        return
    for f in cache_dir.iterdir():
        try:
            if f.is_file():
                f.unlink()
        except OSError:
            continue
