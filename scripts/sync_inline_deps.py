#!/usr/bin/env python3
"""Verify PEP 723 inline-script dependencies don't drift from pyproject.toml (P1-6).

Verifier hooks ship as standalone PEP 723 scripts so they run via
``uv run --script`` without depending on the user's ``.venv``. Each
hook embeds its own minimal dependency list at the top::

    # /// script
    # requires-python = ">=3.11"
    # dependencies = ["pyyaml>=6.0"]
    # ///

That list is independent from ``pyproject.toml`` and is intentionally a
**subset** — for example ``hooks/security_hook.py`` is regex-only and
declares ``dependencies = []`` so that it doesn't pay the cost of
resolving pyyaml on every Edit/Write hook fire.

What this script enforces (the *drift* failure mode):
  - For every package an inline block lists, the version specifier MUST
    match the entry in ``pyproject.toml``'s ``project.dependencies``.
  - An inline block may list a strict **subset** of pyproject deps (zero
    is allowed) — that's how lightweight scripts opt out of heavy deps.
  - An inline block listing a package NOT in pyproject is drift (bumping
    the inline block without touching pyproject means a hook would fetch
    something the project never declared).

Usage::

    uv run python scripts/sync_inline_deps.py           # rewrite drift
    uv run python scripts/sync_inline_deps.py --check   # CI mode (exit 1 on drift)
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
HOOKS_DIR = REPO_ROOT / "hooks"

_SCRIPT_BLOCK_RE = re.compile(
    r"^# /// script\n((?:#[^\n]*\n)*?)# ///\n",
    re.MULTILINE,
)
_DEPENDENCIES_LINE_RE = re.compile(r"^# dependencies\s*=\s*(\[[^\]]*\])\s*$", re.MULTILINE)
_DEP_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _runtime_deps_from_pyproject() -> dict[str, str]:
    """Return the project's runtime deps as ``{name: full_specifier}``."""
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project") or {}
    deps = project.get("dependencies") or []
    out: dict[str, str] = {}
    for spec in deps:
        if not isinstance(spec, str):
            continue
        m = _DEP_NAME_RE.match(spec)
        if m:
            out[m.group(1).lower()] = spec
    return out


def _parse_inline_list(raw: str) -> list[str]:
    """Parse a single-line list literal like ``["pyyaml>=6.0", "yaml"]``."""
    inner = raw.strip().lstrip("[").rstrip("]").strip()
    if not inner:
        return []
    items: list[str] = []
    for chunk in inner.split(","):
        stripped = chunk.strip().strip('"').strip("'").strip()
        if stripped:
            items.append(stripped)
    return items


def _format_inline_list(specs: list[str]) -> str:
    return "[" + ", ".join(f'"{s}"' for s in specs) + "]"


def _sync_file(
    path: Path,
    canonical: dict[str, str],
    *,
    check_only: bool,
) -> tuple[bool, str | None]:
    """Validate one file. Returns ``(drifted, reason_or_None)``."""
    text = path.read_text()
    block_match = _SCRIPT_BLOCK_RE.search(text)
    if not block_match:
        return False, None  # No PEP 723 block; skip silently.

    deps_match = _DEPENDENCIES_LINE_RE.search(block_match.group(1))
    if not deps_match:
        return False, None  # Block exists but doesn't declare dependencies.

    current_specs = _parse_inline_list(deps_match.group(1))

    # Per-spec validation
    fixed: list[str] = []
    drift_messages: list[str] = []
    for spec in current_specs:
        m = _DEP_NAME_RE.match(spec)
        if not m:
            drift_messages.append(f"unparseable spec: {spec!r}")
            fixed.append(spec)
            continue
        name = m.group(1).lower()
        canonical_spec = canonical.get(name)
        if canonical_spec is None:
            drift_messages.append(f"package '{name}' not in pyproject.toml")
            fixed.append(spec)
            continue
        if canonical_spec != spec:
            drift_messages.append(f"version mismatch for '{name}': inline={spec!r}, pyproject={canonical_spec!r}")
            fixed.append(canonical_spec)
        else:
            fixed.append(spec)

    if not drift_messages:
        return False, None

    if check_only:
        return True, "; ".join(drift_messages)

    # Rewrite — only when fix is "replace with canonical version".
    if any("not in pyproject.toml" in m or "unparseable" in m for m in drift_messages):
        # Can't auto-fix unknown packages — surface and bail.
        return True, "; ".join(drift_messages)

    new_line = f"# dependencies = {_format_inline_list(fixed)}"
    new_text = text.replace(deps_match.group(0), new_line, 1)
    path.write_text(new_text)
    return True, "updated"


def _hook_files() -> list[Path]:
    return sorted(HOOKS_DIR.rglob("*.py"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift without rewriting files. Exit 1 if any drift exists.",
    )
    args = parser.parse_args()

    canonical = _runtime_deps_from_pyproject()
    drifted = False
    unfixable = False
    for path in _hook_files():
        is_drift, msg = _sync_file(path, canonical, check_only=args.check)
        if is_drift:
            drifted = True
            rel = path.relative_to(REPO_ROOT)
            if args.check:
                sys.stderr.write(f"DRIFT: {rel}: {msg}\n")
            elif msg == "updated":
                sys.stdout.write(f"updated: {rel}\n")
            else:
                sys.stderr.write(f"unfixable drift: {rel}: {msg}\n")
                unfixable = True

    if args.check and drifted:
        sys.stderr.write(
            "\nInline PEP 723 dependencies drifted from pyproject.toml.\n"
            "Run `uv run python scripts/sync_inline_deps.py` (without --check) "
            "to fix version mismatches.\n"
        )
        return 1
    if unfixable:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
