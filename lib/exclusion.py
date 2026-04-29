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


def filter_enabled_validators(validators, enabled: list[str]):
    """Keep only validators whose V-ID prefix or full id is in the allowlist.

    Empty ``enabled`` means **no allowlist** — every validator passes
    through (the README's default semantics: "비워두면 모든 validator
    활성"). When non-empty, this is a strict allowlist: only validators
    explicitly named survive. Combine with ``filter_disabled_validators``
    to subtract from the allowlist (router applies enabled THEN disabled
    so ``disabled`` wins on conflict).

    Names follow the same form as ``filter_disabled_validators``:
    full id (``"V01-env-config"``) or just the V-ID prefix (``"V01"``).

    Hard-fail on user typos
    -----------------------
    A non-empty ``enabled`` list that matches **zero** registered
    validators is always a user error (typo or stale id). Silently
    returning an empty list would let the Stop hook approve every turn
    without running any validators — a security-critical false-approve
    (e.g. V08 secret scanning silently disabled). We raise a
    ``ValueError`` with a suggestion list instead, so the user finds out
    immediately. Caller (router / stop_validator) catches and surfaces
    via the standard exception path.
    """
    if not enabled:
        return list(validators)
    enabled_set = set(enabled)
    out = []
    for v in validators:
        prefix = v.id.split("-", 1)[0]
        if v.id in enabled_set or prefix in enabled_set:
            out.append(v)

    if not out:
        # Build a hint list so the user can spot their typo.
        known = sorted({v.id for v in validators} | {v.id.split("-", 1)[0] for v in validators})
        raise ValueError(
            f"validators.enabled = {sorted(enabled)} but matched 0 registered validators. "
            "This silently disables every validator and will silent-approve every Stop hook. "
            f"Did you mean one of: {', '.join(known)}? "
            "Fix the typo in .verifiers/config.yaml or remove the 'enabled' key to run all validators."
        )
    return out


def is_excluded_for_validator(
    file_path: str,
    project_root: Path,
    per_validator: dict[str, list[str]],
    validator_id: str,
) -> bool:
    """Per-validator exclusion: should ``validator_id`` skip ``file_path``?

    ``per_validator`` is the user's
    ``ctx.config.exclude.per_validator`` map. Keys may be the full
    validator id (``"V14-complexity-guard"``) or just the V-ID prefix
    (``"V14"``); both forms are checked so configs can use whichever the
    user prefers in ``.verifiers/config.yaml``.

    The ``validator_id`` argument may itself be either form. When it's a
    bare prefix (``"V14"``) — as happens in
    ``hooks/stop_validator.py:_apply_exclude_filters`` where only a
    finding's rule is available — we also match any config key that
    starts with ``"<prefix>-"`` so a config written with full ids still
    applies.

    Returns False fast when the map is empty (the common case).
    """
    if not per_validator:
        return False

    prefix = validator_id.split("-", 1)[0]  # "V14-complexity-guard" → "V14"

    # Patterns can be registered under either form; merge all matching buckets.
    patterns: list[str] = []
    if validator_id in per_validator:
        patterns.extend(per_validator[validator_id])
    if prefix != validator_id and prefix in per_validator:
        patterns.extend(per_validator[prefix])
    # Bare-prefix caller (e.g. _apply_exclude_filters): also pick up any
    # full-id keys in the same V-NN family.
    if validator_id == prefix:
        for key, vals in per_validator.items():
            if key != prefix and key.startswith(f"{prefix}-"):
                patterns.extend(vals)

    if not patterns:
        return False

    rel = _relativize(file_path, project_root)
    return any(fnmatch(rel, pattern) for pattern in patterns)
