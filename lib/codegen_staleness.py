"""Codegen staleness detection — shared by V02 (graphql_gen), V03
(proto_connect), and any future codegen-driven validator.

Phase51 extracted this from V02 + V03 where the same two-step
algorithm was duplicated. The two callers used identical
hash + mtime logic with different inputs/outputs and finding
labels; the algorithm is the contribution worth sharing.

## Algorithm

```
                ┌─ inputs ── input_files (source globs) ──┐
                │                                          │
                ▼                                          ▼
       hash via lib.hash_cache.hash_files()      [step 2 below]
                │
                ▼
       cache.has_changed(category, project, current_hash)
       (also persists the new hash for next call)
                │
       ┌────────┴────────┐
       │                 │
       │  False (cache hit; no change)
       │                 │
       │                 ▼
       │              return False (NOT stale)
       │
       ▼
       True (hash differs from cache)
                │
                ▼
       step 2 — mtime double-check:
                max(input mtime) > max(generated mtime)?
                │
       ┌────────┴────────┐
       │                 │
       │  False (generated is newer despite hash change —
       │  cache was wiped but generation actually ran)
       │                 │
       │                 ▼
       │              return False
       │
       ▼
       True → return True (STALE; caller should emit Finding)
```

## Why two steps?

A naive hash check would false-positive after `rm logs/.gen-hash-cache.json`
or first-run on a fresh checkout — the cache is empty so every input
hash looks "changed" even though the generated code is up-to-date.
The mtime fallback says: "even if the cache says changed, only flag
staleness if generated files are actually older than the inputs."

Conversely, a naive mtime-only check would false-positive after
`git checkout` (mtimes get reset to checkout time, breaking ordering).
Hash check rescues that case.

The combined two-step is the reliable answer.

## Why not parameterize the Finding emit?

Each caller wants different file paths, messages, and fix strings
(e.g. V02 points at the .go file, V03 points at the proto dir).
Lifting the emit into this module would force a 6-7 parameter API;
keeping it in the caller keeps `is_codegen_stale` to 5 args and
the caller's intent local. Trade-off: ~3 lines of `if stale: append(Finding(...))`
boilerplate per caller, vs an over-parameterized lib API.
"""

from __future__ import annotations

from pathlib import Path

from lib.hash_cache import HashCache, hash_files


def is_codegen_stale(
    cache: HashCache,
    category: str,
    project: str,
    input_files: list[Path],
    generated_files: list[Path],
) -> bool:
    """Two-step staleness check: hash + mtime.

    Returns True if and only if:
      1. hash(input_files) differs from cached value for (category, project), AND
      2. max(input mtime) > max(generated mtime).

    Returns False on:
      - empty input_files (nothing to check)
      - no existing input files (all globs missed)
      - empty/non-existent generated_files (codegen layer absent — a
        different validator should flag the missing output)
      - hash unchanged (cache hit)
      - hash changed but generated mtime is newer (cache wipe / fresh
        checkout false positive)

    **Side effect:** updates ``cache`` with the current input hash via
    ``HashCache.has_changed``. Treat this as the canonical "rebaseline
    after we've checked" — even if step 2 reports not-stale, step 1
    has already updated the cache so the next call won't re-flag.

    Args:
        cache: Shared HashCache instance for persistent cross-call state.
        category: Cache key category (e.g. ``"graphql"`` for V02,
            ``"proto"`` for V03). Distinguishes hash entries from
            different validators on the same project.
        project: Project name (typically ``ctx.project_name``). Forms the
            second half of the cache key.
        input_files: Source files whose change should trigger
            regeneration (queries, schemas, .proto, build configs).
        generated_files: Output files produced by the codegen step.
            Used for the mtime comparison; must contain at least one
            existing file for the function to consider staleness.

    Returns:
        True if regeneration is needed; False otherwise.
    """
    if not input_files:
        return False

    existing_inputs = [f for f in input_files if f.exists()]
    if not existing_inputs:
        return False

    existing_generated = [f for f in generated_files if f.exists()]
    if not existing_generated:
        return False

    current_hash = hash_files(existing_inputs)
    if not cache.has_changed(category, project, current_hash):
        return False

    latest_input = max(f.stat().st_mtime for f in existing_inputs)
    latest_generated = max(f.stat().st_mtime for f in existing_generated)
    return latest_input > latest_generated
