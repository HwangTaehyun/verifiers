"""Shared secret-detection regex set for Tier 1 (security_hook) + V08.

Phase38 (A3 audit). Pre-Phase38, both tiers carried near-identical
regex tables and exclusion sets, and they had already started to
drift: Tier 1's password regex was patched in P2-2 to escape ``${`` /
``${}`` template placeholders so Helm / Go-template strings like
``password = "{{ env.PASSWORD }}"`` stop tripping it, but V08 missed
that fix. Centralizing here closes the drift surface — every patch
now lands once.

This module is intentionally **zero-dep** (stdlib only, no yaml).
Tier 1's contract is "no yaml import, runs every PostToolUse on every
file regardless of file_pattern matching" — pulling in
``lib.config_loader`` would break that.

Layout:
- ``SECRET_REGEXES``: ordered list of ``(pattern, description)``.
  The hardcoded-password rule lives at the end so high-confidence
  prefix patterns (AKIA / ghp_ / sk-) are checked first.
- ``EXCLUDE_DIRS`` / ``EXCLUDE_FILENAME_PREFIXES`` /
  ``EXCLUDE_FILENAME_SUFFIXES`` / ``EXCLUDE_EXACT_NAMES``: path
  classification primitives. Used by ``is_excluded_path`` so a single
  call decides whether a path is secret-scanning-exempt. Each rule is
  anchored on a path *component*, not a substring (the substring
  approach mis-excluded ``mockingbird/Real.go`` because ``mock`` was
  in the path).
"""

from __future__ import annotations

from pathlib import Path

# Phase38: source of truth for both Tier 1 (``hooks/security_hook.py``)
# and V08 (``hooks/validators/security.py``). The ``[^"'${}]`` inside
# the password rule blocks Helm / Go-template placeholders from
# tripping it (P2-2 fix that V08 had previously missed).
SECRET_REGEXES: list[tuple[str, str]] = [
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API Key"),
    (r"sk_live_[a-zA-Z0-9]{20,}", "Stripe Live Key"),
    (r"xoxb-[a-zA-Z0-9\-]+", "Slack Bot Token"),
    (r"""password\s*[:=]\s*["'][^"'${}]{8,}["']""", "Hardcoded password"),
]


# Path classification primitives (P2-3 anchoring fix preserved).
EXCLUDE_DIRS: frozenset[str] = frozenset(
    {"fixtures", "testdata", "mock", "mocks", "__tests__", "vendor", "node_modules", "generated", "gen"}
)
EXCLUDE_FILENAME_PREFIXES: tuple[str, ...] = ("test_",)
EXCLUDE_FILENAME_SUFFIXES: tuple[str, ...] = ("_test.go",)
# Exact .env names: developer-managed env files may contain real
# secrets, so they are exempt. ``.env.example`` must NOT be exempt
# (it's the template that should never carry real values).
EXCLUDE_EXACT_NAMES: frozenset[str] = frozenset({".env", ".env.development", ".env.production"})


def is_excluded_path(file_path: str) -> bool:
    """Return True iff this path falls into a security-exempt category.

    Anchored matching: directory exclusions match on a full path
    component (``Path.parts``), filename rules use prefix/suffix.
    """
    p = Path(file_path)
    name = p.name
    if name in EXCLUDE_EXACT_NAMES:
        return True
    if name.startswith(EXCLUDE_FILENAME_PREFIXES) or name.endswith(EXCLUDE_FILENAME_SUFFIXES):
        return True
    return any(part in EXCLUDE_DIRS for part in p.parts)
