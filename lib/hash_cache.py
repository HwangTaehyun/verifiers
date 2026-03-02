"""SHA256 hash-based cache for detecting stale generated code.

Stores input file hashes in logs/.gen-hash-cache.json and compares
them to detect when source files have changed but generated output
hasn't been regenerated.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CACHE_FILE = Path(__file__).parent.parent / "logs" / ".gen-hash-cache.json"


def hash_files(files: list[Path]) -> str:
    """Compute a combined SHA256 hash of multiple files."""
    hasher = hashlib.sha256()
    for f in sorted(files):  # Sort for deterministic ordering
        if f.exists() and f.is_file():
            try:
                hasher.update(f.read_bytes())
            except OSError:
                continue
    return hasher.hexdigest()


class HashCache:
    """Persistent hash cache for stale detection."""

    def __init__(self, cache_file: Path | None = None):
        self.cache_file = cache_file or CACHE_FILE
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        """Persist cache to disk."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.cache_file.write_text(json.dumps(self._data, indent=2))
        except OSError:
            pass

    def get(self, category: str, project: str) -> str | None:
        """Get cached hash for a category+project pair."""
        key = f"{category}:{project}"
        return self._data.get(key)

    def set(self, category: str, project: str, hash_value: str) -> None:
        """Store a hash value and persist."""
        key = f"{category}:{project}"
        self._data[key] = hash_value
        self._save()

    def has_changed(self, category: str, project: str, current_hash: str) -> bool:
        """Check if the hash has changed since last cache."""
        cached = self.get(category, project)
        if cached is None:
            # First time — store and report no change
            self.set(category, project, current_hash)
            return False
        return cached != current_hash
