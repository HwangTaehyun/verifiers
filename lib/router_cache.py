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
    """Hash the file's path + bytes via SHA-256 (Phase37 / S3).

    The hash binds the absolute path into the digest so a cache entry
    keyed on ``src/auth.py`` can never match the digest of any other
    file. Without this binding, a malicious or prompt-injected
    ``router-cache.json`` could record ``src/auth.py → <hash of a
    future evil version>`` ahead of time; when Claude later wrote
    those exact bytes the router would skip V08 secret scanning. Path
    binding makes that pre-record impossible — a poisoned entry hashed
    for path A won't match the same bytes at path B.

    Returns ``None`` if the file can't be read (just deleted, missing
    permissions). Callers treat ``None`` as "no cache decision
    possible, run validators normally". Existing pre-Phase37 entries
    will simply mismatch on first re-read and be replaced.
    """
    try:
        content = Path(file_path).read_bytes()
    except OSError:
        return None
    h = hashlib.sha256()
    h.update(file_path.encode("utf-8"))
    h.update(b"\0")
    h.update(content)
    return h.hexdigest()


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
        # Phase37 (A6 audit): 0o700 so a shared CI / dev host doesn't
        # leak the project's metric / cache state to other users on the
        # same machine. mkdir's mode= is honored only when the dir is
        # newly created; we follow up with chmod for the case where it
        # already existed under a more permissive umask.
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
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
