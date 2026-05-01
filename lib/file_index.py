"""Single-walk project file index — Phase 65.

Where Phase 64.1 added ``exclude.paths`` awareness to
``lib.tier_cache.compute_input_hash`` (so files matching exclusions
don't bloat the cache hash), Phase 65 pushes the optimization one
level up: the project tree is walked **exactly once** per Stop hook
(cached on ``ProjectContext.file_index``), and every validator + the
Phase 63 cache-hash query the resulting in-memory index instead of
running their own ``Path.glob("**/...")``.

## Why

Each Tier 3 validator that does project-wide work historically called
``Path.glob`` or ``Path.rglob`` independently:

  V05  glob 4 patterns (docker-compose + Dockerfile)
  V14  rglob *.go *.py *.ts *.tsx
  V15  rglob *.go *.py *.ts *.tsx
  V38  rglob .golangci.{yaml,yml}
  V44  glob Dockerfile* + *.Dockerfile
  V45  glob Dockerfile* + *.Dockerfile
  V58  glob Dockerfile* + *.Dockerfile

On a 21k-file monorepo (`web/node_modules` alone holds 91k entries),
each glob takes ~1-2 s in isolation. When 6 of them run concurrently
through `lib.parallel_runner`'s ``ThreadPoolExecutor(8w)`` two pathologies
combine:

1. **GIL contention**: ``Path.glob`` is a pure-Python iterator. The
   syscall (``os.scandir``) releases the GIL but the iteration that
   wraps it does not. Six threads competing for the GIL during walk
   serialize each other.
2. **Filesystem IO contention**: macOS APFS serializes concurrent
   ``stat`` calls under load, so the kernel work itself queues up too.

Measured (ax-finance-project, 102,975 entries):
  Path.glob("**/Dockerfile*") solo:                       ~1.3 s
  Path.glob("**/Dockerfile*") × 6 concurrent (8w pool):   ~16 s each
  ProjectFileIndex.build with default + exclude prune:    ~10 ms
  index.find_by_pattern("Dockerfile*"):                   <1 ms

## Algorithm

1. ``ProjectFileIndex.build(root, exclude_globs)`` walks the tree
   exactly once with ``os.walk``. The crucial bit is ``dirnames[:] = ...``
   in-place mutation, which ``os.walk`` honors as "do not descend into
   these subdirectories" — an interface ``Path.glob`` does not expose.
2. ``DEFAULT_PRUNE_NAMES`` is pruned unconditionally (``.git``,
   ``node_modules``, ``vendor``, ``__pycache__``, ``.venv``, etc).
   These directory names never contain user code.
3. ``exclude_globs`` (typically ``ctx.config.exclude.paths``) get
   matched as **directory prefixes**, so a glob like ``web/build/**``
   prunes the ``web/build`` subtree at walk time rather than filtering
   files individually after they're enumerated.
4. Each surviving file gets stat'd once and bucketed by extension and
   basename for fast pattern lookups.

## Query model

Two query shapes cover every existing validator:

  index.find_by_pattern("*.go", "*.py")
      → list[Path] of matching files. Pure-extension patterns hit
        ``by_ext`` (O(1) bucket fetch). Other patterns scan
        ``by_name.keys()`` (~hundreds of unique basenames) with
        ``fnmatch``.

  index.hash_for_patterns(("**/*.go",))
      → sha256 hex of ``(path:size:mtime_ns)`` for matching files.
        Replaces ``compute_input_hash``'s walk + stat loop. The mtime
        we hash is the one captured at index build time, which is
        consistent across all validators within a single Stop hook
        (even if a file is concurrently modified mid-Stop, we'd see
        either the pre or post stat — never a torn one).

## Cache lifetime

``ProjectContext.file_index`` is a ``functools.cached_property``: the
walk runs lazily on first access within a Stop hook, then memoizes for
the lifetime of the ``ctx``. Each new Stop hook gets a fresh ``ctx``
instance, so there's no stale-cache risk across invocations.

Tier 2 (``hooks/router.py``) does NOT use this index — it operates on
a single edited file and the per-Edit cost of building a 250-file
index is below the cost of even one Path.glob. The index is a Tier 3
optimization specifically.

## What this replaces

``Path.glob`` / ``Path.rglob`` / ``os.walk`` in the seven heavy Tier 3
validators. The Phase 63 ``compute_input_hash`` also delegates here so
the hash computation pays the walk cost once, not once-per-validator.

## Citations

- Python ``os.walk`` directory pruning idiom: see the official docs
  for ``os.walk`` — `<https://docs.python.org/3/library/os.html#os.walk>`__,
  retrieved 2026-05-01.
- macOS APFS concurrent stat behavior: discussed in the Apple
  Developer Forums under "APFS scandir performance" threads
  (developer.apple.com/forums) — empirical observation here, see the
  benchmark numbers above.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

# Hard-coded directory names that never contain user code. These are
# pruned at every walk depth without requiring user config — so a
# project without an ``exclude.paths`` block still gets correct,
# fast behavior.
#
# Conservative on inclusion: only names that are *universally* noise.
# ``dist`` and ``build`` are NOT here because some projects legitimately
# put checkable artifacts there; users configure those via
# ``exclude.paths`` if they want them pruned.
DEFAULT_PRUNE_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        "__pycache__",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".next",  # Next.js build cache
        ".turbo",  # Turborepo cache
    }
)

_PURE_EXT_RE = re.compile(r"\*\.([A-Za-z0-9]+)$")


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One filesystem entry with the stat info needed downstream.

    ``size`` and ``mtime_ns`` are captured at index-build time so the
    Phase 63 cache hash sees a consistent snapshot across all
    validators within a single Stop hook.
    """

    path: Path
    size: int
    mtime_ns: int


class ProjectFileIndex:
    """Pre-walked snapshot of the project's files.

    Built once via :py:meth:`build` at the start of a Stop hook,
    queried by every validator and by the Phase 63 cache hash. The
    walk itself respects directory pruning (the
    ``DEFAULT_PRUNE_NAMES`` builtins plus user-supplied
    ``exclude_globs``) so monorepos with ``node_modules`` or
    ``vendor`` don't pay the 91k-entry walk cost.
    """

    __slots__ = ("_entries", "_by_ext", "_by_name")

    def __init__(
        self,
        entries: list[FileEntry],
        by_ext: dict[str, list[FileEntry]],
        by_name: dict[str, list[FileEntry]],
    ) -> None:
        self._entries = entries
        self._by_ext = by_ext
        self._by_name = by_name

    # ── Construction ────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        root: Path | str,
        exclude_globs: tuple[str, ...] | list[str] = (),
    ) -> ProjectFileIndex:
        """Walk ``root`` once, pruning excluded directories.

        ``exclude_globs`` are gitignore-style globs from
        ``ctx.config.exclude.paths``. A directory whose path
        (relative to ``root``) matches the prefix of any glob — after
        stripping the trailing ``/**`` or ``/*`` — is pruned mid-walk,
        so its subtree is never visited.

        Hard-coded ``DEFAULT_PRUNE_NAMES`` are pruned regardless of
        config. ``OSError`` on individual ``stat`` calls is swallowed
        — the index just omits unreadable entries.

        Symlinks are not followed (``followlinks=False``) to avoid
        infinite loops on circular link structures.
        """
        root = Path(root)
        prune_prefixes = _glob_prefixes(tuple(exclude_globs))

        entries: list[FileEntry] = []
        by_ext: dict[str, list[FileEntry]] = {}
        by_name: dict[str, list[FileEntry]] = {}

        if not root.exists():
            return cls(entries, by_ext, by_name)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Compute relative path of current dir for prefix matching.
            try:
                rel_dir = os.path.relpath(dirpath, root)
            except ValueError:
                rel_dir = ""
            if rel_dir == ".":
                rel_dir = ""

            # Prune subdirectories *before* the walk descends into them.
            # ``dirnames[:] = [...]`` is the canonical os.walk pruning
            # idiom — Path.glob has no equivalent.
            kept: list[str] = []
            for dn in dirnames:
                if dn in DEFAULT_PRUNE_NAMES:
                    continue
                child_rel = f"{rel_dir}/{dn}" if rel_dir else dn
                if _matches_any_prefix(child_rel, prune_prefixes):
                    continue
                kept.append(dn)
            dirnames[:] = kept

            # Index files in current directory.
            for fname in filenames:
                full_path_str = os.path.join(dirpath, fname)
                try:
                    st = os.stat(full_path_str)
                except OSError:
                    continue
                full = Path(full_path_str)
                entry = FileEntry(path=full, size=st.st_size, mtime_ns=st.st_mtime_ns)
                entries.append(entry)
                ext = full.suffix.lower()
                if ext:
                    by_ext.setdefault(ext, []).append(entry)
                by_name.setdefault(fname, []).append(entry)

        return cls(entries, by_ext, by_name)

    # ── Queries ─────────────────────────────────────────────────────

    def find_by_pattern(self, *patterns: str) -> list[Path]:
        """Return Paths whose basename matches any of ``patterns`` (fnmatch).

        Pure-extension patterns (``*.go``, ``**/*.tsx``) hit
        ``by_ext`` directly — O(1) bucket fetch. Other patterns scan
        ``by_name.keys()`` (typically ~hundreds of unique basenames)
        and ``fnmatch`` each. De-dups across patterns by Path identity.
        """
        if not patterns:
            return []
        out: list[Path] = []
        seen: set[Path] = set()
        for pat in patterns:
            for entry in self._lookup(pat):
                if entry.path in seen:
                    continue
                seen.add(entry.path)
                out.append(entry.path)
        return out

    def hash_for_patterns(self, patterns: list[str] | tuple[str, ...]) -> str:
        """Phase 63 input hash — ``sha256(path:size:mtime_ns)`` for matching files.

        Equivalent to ``lib.tier_cache.compute_input_hash`` but uses
        the pre-built index so no second walk happens. The mtime
        captured at build time is hashed (not a fresh stat) for
        consistency across the Stop hook.
        """
        if not patterns:
            return ""
        h = hashlib.sha256()
        seen: set[Path] = set()
        for pat in patterns:
            for entry in self._lookup(pat):
                if entry.path in seen:
                    continue
                seen.add(entry.path)
                h.update(f"{entry.path}:{entry.size}:{entry.mtime_ns}\n".encode("utf-8"))
        return h.hexdigest()

    def find_by_basename(self, basename: str) -> list[Path]:
        """Return Paths whose basename equals ``basename`` exactly."""
        return [e.path for e in self._by_name.get(basename, ())]

    @property
    def total(self) -> int:
        """Number of indexed files (not entries — directories aren't counted)."""
        return len(self._entries)

    # ── Internals ───────────────────────────────────────────────────

    def _lookup(self, pattern: str) -> list[FileEntry]:
        """Resolve one pattern to its FileEntry bucket."""
        ext = _extract_pure_ext(pattern)
        if ext is not None:
            return self._by_ext.get(ext, [])
        # General fnmatch over basenames.
        out: list[FileEntry] = []
        for name, bucket in self._by_name.items():
            if fnmatch(name, pattern):
                out.extend(bucket)
        return out


# ── Pattern + glob helpers ──────────────────────────────────────────


def _extract_pure_ext(pattern: str) -> str | None:
    """Return ``".ext"`` if ``pattern`` ends in ``*.<alnum>``; else None.

    Examples::

        "*.go"          → ".go"
        "**/*.tsx"      → ".tsx"
        "*.GO"          → ".go"   (lowercased)
        "Dockerfile*"   → None    (filename glob, not pure-ext)
        "*.tar.gz"      → None    (compound — falls through to fnmatch)

    Mirrors ``hooks/validators/__init__._classify_pattern`` so the two
    code paths agree on what counts as "pure extension".
    """
    m = _PURE_EXT_RE.search(pattern)
    if not m:
        return None
    return f".{m.group(1).lower()}"


def _glob_prefixes(exclude_globs: tuple[str, ...]) -> list[str]:
    """Convert gitignore-style exclude globs into directory-prefix matchers.

    Returns prefix strings suitable for :py:func:`_matches_any_prefix`.

    Examples::

        "vendor/**"            → "vendor"
        "web/build/**"         → "web/build"
        "**/__generated__/**"  → "**/__generated__"  (any-depth basename)
        "server/gen/**"        → "server/gen"
        "**/*.tmp"             → (skipped — file glob, not a dir prefix)
    """
    prefixes: list[str] = []
    for g in exclude_globs:
        stripped = g.rstrip("/")
        for suffix in ("/**", "/*"):
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)]
                break
        # If the result is empty or still contains a wildcard in its
        # final component (e.g. "*.tmp"), it's a file-level glob — not a
        # directory we can prune. Skip.
        if not stripped:
            continue
        last = stripped.rsplit("/", 1)[-1]
        if "*" in last:
            # Allow the "**/<name>" form: any directory whose basename
            # equals ``<name>``. ``__generated__`` is the canonical case.
            if stripped.startswith("**/") and "*" not in stripped[3:]:
                prefixes.append(stripped)
            continue
        prefixes.append(stripped)
    return prefixes


def _matches_any_prefix(rel_path: str, prefixes: list[str]) -> bool:
    """True if ``rel_path`` matches any directory-prune prefix.

    Prefix forms::

        "vendor"           → exact match
        "web/build"        → exact or starts-with "web/build/"
        "**/__generated__" → any path component equals "__generated__"
    """
    for pre in prefixes:
        if pre.startswith("**/"):
            tail = pre[3:]
            # Match any path component equal to the tail.
            for part in rel_path.split("/"):
                if part == tail:
                    return True
        else:
            if rel_path == pre or rel_path.startswith(pre + "/"):
                return True
    return False
