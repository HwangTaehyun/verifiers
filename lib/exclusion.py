"""Central path exclusion for verifier hooks (P1-4).

Recent commit history showed repeated `fix: skip <X> directory` patches
landing inside individual validators (e.g. ``hooks/validators/complexity_guard.py``
with hardcoded `theme/` and `.claude/` exclusions). That signal pointed at
a missing abstraction: there's no project-level place to say "these paths
are not part of my codebase, don't scan them".

This module provides the abstraction. Patterns come from
``ctx.config.exclude.paths`` (see ``lib.config_loader``) and use
gitignore-style globs interpreted via ``fnmatch``. Hooks call
``is_excluded`` once per file before running validators, so any future
"skip directory X" need is solved by editing ``.verifiers/config.yaml``
rather than touching validator source.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


def _relativize(file_path: str, project_root: Path) -> str:
    """Return ``file_path`` relative to ``project_root`` when possible.

    Falls back to the raw path when the file lives outside the root —
    that case shouldn't happen for hook inputs but we don't crash on it.
    """
    try:
        return str(Path(file_path).resolve().relative_to(project_root.resolve()))
    except (ValueError, OSError):
        return file_path


def is_excluded(file_path: str, project_root: Path, patterns: list[str]) -> bool:
    """Return True iff ``file_path`` matches any exclusion pattern.

    Each pattern is a gitignore-style glob (matched via :mod:`fnmatch`).
    Patterns are applied to the path **relative to ``project_root``** so
    ``"vendor/**"`` works regardless of where the user's cwd is.
    Empty pattern lists return False — no project config means no exclusion.
    """
    if not patterns:
        return False
    rel = _relativize(file_path, project_root)
    return any(fnmatch(rel, pattern) for pattern in patterns)


def filter_disabled_validators(validators, disabled: list[str]):
    """Drop validators whose V-ID prefix is in the disabled list.

    Disabled entries can be either the full id (``"V01-env-config"``) or
    just the V-ID prefix (``"V01"``). The latter is friendlier to write
    in ``.verifiers/config.yaml``.
    """
    if not disabled:
        return list(validators)
    disabled_set = set(disabled)
    out = []
    for v in validators:
        prefix = v.id.split("-", 1)[0]  # "V01-env-config" → "V01"
        if v.id in disabled_set or prefix in disabled_set:
            continue
        out.append(v)
    return out
