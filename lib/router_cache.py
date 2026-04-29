"""Per-file content-hash cache for the Tier 2 router (P2-1).

When the router is registered as a PostToolUse hook (via
``scripts/merge_settings.py``), Claude can fire Edit/Write/MultiEdit
many times against the same file in quick succession — sometimes
without actually changing the bytes on disk (e.g. an Edit whose
``new_string`` exactly matches the file's existing content). Re-running
the full validator suite for those is wasted cost.

This module keeps a tiny ``{absolute_path: sha256}`` map at
``<project_root>/.verifiers/state/router-cache.json``. The router calls
``should_skip`` before invoking validators; if the file's current hash
matches the recorded one, we short-circuit. After a successful run,
``record_hit`` writes the new hash so subsequent identical edits skip.

Cap: 1000 entries. On overflow we drop the oldest 200 (Python 3.7+
dicts preserve insertion order). Bounded so a long-running session
across many files never balloons the JSON file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from lib.json_logger import log_exception

_CACHE_RELATIVE = Path(".verifiers") / "state" / "router-cache.json"
_MAX_ENTRIES = 1000
_EVICT_TARGET = 800  # When over cap, trim down to this many entries


def cache_path(project_root: Path) -> Path:
    """Return the canonical cache path under ``project_root``."""
    return project_root / _CACHE_RELATIVE


def file_content_hash(file_path: str) -> str | None:
    """Hash the file's bytes via SHA-256.

    Returns ``None`` if the file can't be read — e.g. it was just
    deleted or we lack permissions. Callers should treat ``None`` as
    "no cache decision possible, run validators normally".
    """
    try:
        return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    except OSError:
        return None


def load_cache(project_root: Path) -> dict[str, str]:
    """Load the per-file hash cache, returning ``{}`` on any I/O error."""
    path = cache_path(project_root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log_exception(
            source="router_cache/load_cache",
            error=exc,
            context={"path": str(path)},
        )
        return {}
    if not isinstance(raw, dict):
        return {}
    # Coerce to str → str; ignore anything malformed.
    return {str(k): str(v) for k, v in raw.items() if isinstance(k, str) and isinstance(v, str)}


def save_cache(project_root: Path, cache: dict[str, str]) -> None:
    """Write the cache, applying FIFO eviction when over capacity.

    Saving is best-effort: an OS error is logged and swallowed so a
    full-disk or permissions issue never breaks the user's turn.
    """
    if len(cache) > _MAX_ENTRIES:
        # Drop the oldest entries (insertion order). Python 3.7+ dict
        # preserves order, and we always insert via record_hit at the
        # end of the dict, so iterating from the front gives FIFO.
        keys_to_drop = list(cache.keys())[: len(cache) - _EVICT_TARGET]
        for k in keys_to_drop:
            cache.pop(k, None)

    path = cache_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except OSError as exc:
        log_exception(
            source="router_cache/save_cache",
            error=exc,
            context={"path": str(path)},
        )


def should_skip(cache: dict[str, str], file_path: str, current_hash: str | None) -> bool:
    """Return True iff the cached hash matches the current file content.

    ``current_hash`` may be ``None`` when the file isn't readable —
    in that case we always run validators (no cache decision possible).
    """
    if current_hash is None:
        return False
    return cache.get(file_path) == current_hash


def record_hit(cache: dict[str, str], file_path: str, current_hash: str | None) -> None:
    """Update the cache after a successful validator run.

    Move-to-end behavior: re-inserting an existing key bumps it to the
    back of the dict so it's evicted last. This makes the FIFO eviction
    behave more like LRU for hot files.
    """
    if current_hash is None:
        return
    cache.pop(file_path, None)
    cache[file_path] = current_hash
