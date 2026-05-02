"""Per-file findings cache — Phase 64.4.

Where Phase 63 (`lib/tier_cache.py`) decides "skip this validator
entirely if no input file changed since last clean run", this cache
operates one level finer: "even if some files changed, only re-analyze
those files; reuse the previously-computed findings for everything
else."

## Why this exists

Phase 63 invalidation is project-wide for a given validator: as soon
as ANY file matching ``validator.file_patterns`` changes, the cache
misses and the validator runs a full project scan. For V14
(complexity-guard) and V15 (dependency-guard) — both of which walk
``**/*.go`` + ``**/*.py`` + ``**/*.ts(x)`` — the typical workflow
pattern is:

  1. Edit a single .ts file
  2. Stop hook → V14 cache miss → re-analyze ALL 1000+ source files
  3. Edit another single file
  4. Stop hook → V14 cache miss again → re-analyze ALL 1000+ source files

99% of those file analyses produce the exact same findings as before
because the files themselves didn't change. Phase 64.4 caches the
per-file finding list keyed by ``(validator_id, file_path,
mtime_ns)`` so the unchanged files are answered from cache while the
changed ones get real analysis.

## Algorithm

For each file the validator wants to analyze:

  1. ``stat`` the file → ``(size, mtime_ns)``.
  2. Look up ``cache[file_path]``. Hit iff ``mtime_ns`` matches.
  3. Hit → reuse cached findings.
  4. Miss → call the real analyzer; record the new findings.

The cache is bounded by ``MAX_ENTRIES`` (default 10,000) — a project
much larger than that doesn't fit in this cache anyway, and we don't
want unbounded growth. On overflow, the oldest entries (by recorded
``mtime_ns``) are evicted.

## Cache key

```
(validator_id, file_path, mtime_ns, config_fingerprint)
```

The ``config_fingerprint`` lets a validator invalidate its entire
cache when its config changes. For V14 that's the
``thresholds.complexity`` block; for V15 it's
``.verifiers/layers.yaml`` mtime + go module name. Without this,
changing a threshold from `cyclomatic_warn: 10` to `15` would leave
stale findings in the cache.

## Storage

  <root>/.verifiers/state/per-file-cache/<V##>.json
  {
    "version": 1,
    "config_fingerprint": "<sha256 hex>",
    "files": {
      "/abs/path/to/file.go": {
        "mtime_ns": 1234567890,
        "findings": [<serialized Finding dataclass>]
      },
      ...
    }
  }

Atomic write (tmp → os.replace). Corrupt JSON → wipe + treat as miss.

## Concurrency

Two Stop hooks racing → last-write-wins. Each hook reads the cache
once at the start of a validator run and writes once at the end, so
the worst case is one hook's findings overwrite another's. Both
end states are valid (per-file cache entries are independent) so
no corruption is possible.

## Escape hatch

``VERIFIERS_NO_PER_FILE_CACHE=1`` env var bypasses both lookup and
record. Useful for measuring the speedup or debugging stale-cache
suspicions.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lib.validators_core import Finding  # Phase 71 T3


CACHE_VERSION = 1
MAX_ENTRIES = 10_000

_CACHE_SUBDIR = Path(".verifiers") / "state" / "per-file-cache"


def _cache_disabled() -> bool:
    """Honor the VERIFIERS_NO_PER_FILE_CACHE=1 escape hatch."""
    return os.environ.get("VERIFIERS_NO_PER_FILE_CACHE", "0") == "1"


def _vid_prefix(validator_id: str) -> str:
    return validator_id.split("-", 1)[0]


def _cache_dir(project_root: Path | str) -> Path:
    return Path(project_root) / _CACHE_SUBDIR


def _cache_file(project_root: Path | str, validator_id: str) -> Path:
    return _cache_dir(project_root) / f"{_vid_prefix(validator_id)}.json"


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    """Serialize a Finding for JSON storage."""
    return asdict(f)


def _finding_from_dict(d: dict[str, Any]) -> Finding | None:
    """Deserialize, defensively. Returns None if the dict shape is broken."""
    try:
        return Finding(
            severity=str(d.get("severity", "warning")),
            file=str(d.get("file", "")),
            rule=str(d.get("rule", "")),
            message=str(d.get("message", "")),
            fix=str(d.get("fix", "")),
            line=int(d["line"]) if d.get("line") is not None else None,
            kind=str(d.get("kind", "")) if d.get("kind") else "",
        )
    except (TypeError, ValueError):
        return None


class PerFileCache:
    """In-memory + on-disk cache for per-file validator findings.

    Lifecycle is per-validator-run:

      cache = PerFileCache.load(project_root, "V14-complexity-guard",
                                config_fingerprint=fingerprint)
      for file_path in files_to_analyze:
          mtime = stat(file_path).st_mtime_ns
          cached = cache.get(file_path, mtime)
          if cached is not None:
              findings.extend(cached)
              continue
          # Real analysis
          new_findings = analyze(file_path)
          cache.put(file_path, mtime, new_findings)
          findings.extend(new_findings)
      cache.save()

    The two-step ``load`` + ``save`` pattern lets the validator
    accumulate updates from many files into a single atomic write
    instead of re-writing the JSON on every file.
    """

    def __init__(
        self,
        project_root: Path | str,
        validator_id: str,
        config_fingerprint: str,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        self.project_root = Path(project_root)
        self.validator_id = validator_id
        self.config_fingerprint = config_fingerprint
        self._entries = entries
        self._dirty = False

    @classmethod
    def load(
        cls,
        project_root: Path | str,
        validator_id: str,
        config_fingerprint: str = "",
    ) -> PerFileCache:
        """Read the cache file, validating version + config_fingerprint.

        Empty cache returned for any of:
          - cache disabled (env var)
          - file missing
          - corrupt JSON
          - schema version mismatch
          - config_fingerprint changed (config invalidation)
        """
        if _cache_disabled():
            return cls(project_root, validator_id, config_fingerprint, {})

        cache_file = _cache_file(project_root, validator_id)
        if not cache_file.is_file():
            return cls(project_root, validator_id, config_fingerprint, {})

        try:
            data = json.loads(cache_file.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            try:
                cache_file.unlink()
            except OSError:
                pass
            return cls(project_root, validator_id, config_fingerprint, {})

        if not isinstance(data, dict):
            return cls(project_root, validator_id, config_fingerprint, {})
        if data.get("version") != CACHE_VERSION:
            return cls(project_root, validator_id, config_fingerprint, {})
        if data.get("config_fingerprint", "") != config_fingerprint:
            # Config changed → wipe.
            return cls(project_root, validator_id, config_fingerprint, {})

        files_raw = data.get("files", {})
        if not isinstance(files_raw, dict):
            files_raw = {}
        return cls(project_root, validator_id, config_fingerprint, files_raw)

    def get(self, file_path: str, mtime_ns: int) -> list[Finding] | None:
        """Return cached findings if (file_path, mtime_ns) matches; else None."""
        if _cache_disabled():
            return None
        entry = self._entries.get(file_path)
        if not entry or not isinstance(entry, dict):
            return None
        if entry.get("mtime_ns") != mtime_ns:
            return None
        raw_findings = entry.get("findings", [])
        if not isinstance(raw_findings, list):
            return None
        out: list[Finding] = []
        for fd in raw_findings:
            if isinstance(fd, dict):
                f = _finding_from_dict(fd)
                if f is not None:
                    out.append(f)
        return out

    def put(self, file_path: str, mtime_ns: int, findings: list[Finding]) -> None:
        """Record findings for this file. Caller serializes the writes."""
        if _cache_disabled():
            return
        self._entries[file_path] = {
            "mtime_ns": mtime_ns,
            "findings": [_finding_to_dict(f) for f in findings],
            # ``recorded_at`` powers FIFO eviction when ``MAX_ENTRIES``
            # overflows. Use ns precision so eviction order is stable
            # even when many entries land in the same second.
            "recorded_at": time.time_ns(),
        }
        self._dirty = True

    def save(self) -> None:
        """Atomic write to disk. No-op when nothing changed."""
        if _cache_disabled() or not self._dirty:
            return

        # Eviction: when we exceed MAX_ENTRIES, drop the oldest
        # ``recorded_at`` entries until under the cap. This keeps the
        # cache file size bounded for huge monorepos and prevents
        # unbounded JSON growth across long-running projects.
        if len(self._entries) > MAX_ENTRIES:
            sorted_items = sorted(
                self._entries.items(),
                key=lambda kv: kv[1].get("recorded_at", 0) if isinstance(kv[1], dict) else 0,
            )
            self._entries = dict(sorted_items[-MAX_ENTRIES:])

        cache_dir = _cache_dir(self.project_root)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        payload = {
            "version": CACHE_VERSION,
            "config_fingerprint": self.config_fingerprint,
            "files": self._entries,
        }
        cache_file = _cache_file(self.project_root, self.validator_id)
        tmp = cache_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, separators=(",", ":")))
            os.replace(str(tmp), str(cache_file))
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    @property
    def size(self) -> int:
        """Number of cached file entries — for tests + diagnostics."""
        return len(self._entries)


def clear_cache(project_root: Path | str, validator_id: str | None = None) -> None:
    """Wipe the per-file cache directory (or one validator's file).

    No-op when the directory doesn't exist. Used by tests + as a manual
    recovery handle when stale-cache is suspected.
    """
    cache_dir = _cache_dir(project_root)
    if not cache_dir.is_dir():
        return
    if validator_id is None:
        for f in cache_dir.iterdir():
            try:
                if f.is_file():
                    f.unlink()
            except OSError:
                continue
    else:
        try:
            _cache_file(project_root, validator_id).unlink(missing_ok=True)
        except OSError:
            pass
