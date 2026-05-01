"""Tests for Phase64.2 — extension-bucketed validator dispatch index."""

from __future__ import annotations

import pytest

from hooks.validators import (
    _build_dispatch_index,
    _classify_pattern,
    get_all_validators,
    get_matching_validators,
)


# ── _classify_pattern ────────────────────────────────────────────────────


def test_classify_extension_pattern() -> None:
    assert _classify_pattern("**/*.go") == ".go"
    assert _classify_pattern("*.go") == ".go"
    assert _classify_pattern("*.tsx") == ".tsx"
    assert _classify_pattern("**/*.py") == ".py"


def test_classify_uppercase_extension_normalized() -> None:
    assert _classify_pattern("*.GO") == ".go"


def test_classify_filename_pattern_returns_none() -> None:
    """Exact filename / glob without trailing extension → None."""
    assert _classify_pattern("go.mod") is None
    assert _classify_pattern("Dockerfile*") is None
    assert _classify_pattern("**/__generated__/**") is None
    assert _classify_pattern("buf.yaml") is None


def test_classify_compound_extension_falls_to_residual() -> None:
    """``*.tar.gz`` doesn't end in ``*.<single-token>`` so the regex
    declines it. Compound extensions land in the residual bucket where
    the per-validator ``should_run`` regex still matches them."""
    assert _classify_pattern("*.tar.gz") is None


# ── _build_dispatch_index ────────────────────────────────────────────────


@pytest.fixture
def fresh_index():
    """Clear cached index before/after to prevent inter-test pollution."""
    _build_dispatch_index.cache_clear()
    yield
    _build_dispatch_index.cache_clear()


def test_index_separates_buckets(fresh_index) -> None:
    ext_index, residual, wildcard = _build_dispatch_index()
    # Sanity: at least Go / TS / Python validators land in expected buckets.
    assert any(v.id.startswith("V06") for v in ext_index.get(".go", []))
    assert any(v.id.startswith("V07") for v in ext_index.get(".ts", []))
    assert any(v.id.startswith("V19") for v in ext_index.get(".py", []))
    # V08 security has empty file_patterns → wildcard bucket.
    assert any(v.id.startswith("V08") for v in wildcard)
    # V12 commit-discipline has empty file_patterns → wildcard.
    assert any(v.id.startswith("V12") for v in wildcard)
    # Some validator declares filename-only pattern (e.g. V05 Dockerfile*).
    assert len(residual) > 0


def test_index_validator_appears_in_each_extension_bucket(fresh_index) -> None:
    """A validator with multiple extensions (e.g. V14 .go/.py/.ts) lands
    in each ext bucket."""
    ext_index, _, _ = _build_dispatch_index()
    v14_in_go = any(v.id.startswith("V14") for v in ext_index.get(".go", []))
    v14_in_py = any(v.id.startswith("V14") for v in ext_index.get(".py", []))
    v14_in_ts = any(v.id.startswith("V14") for v in ext_index.get(".ts", []))
    assert v14_in_go and v14_in_py and v14_in_ts


# ── get_matching_validators ──────────────────────────────────────────────


def test_matching_validators_includes_security_for_any_file(fresh_index) -> None:
    """V08 security has empty file_patterns and runs on every file."""
    active = get_all_validators()
    result = get_matching_validators("/some/random/file.go", active)
    assert any(v.id.startswith("V08") for v in result)


def test_matching_validators_go_file_returns_go_validators(fresh_index) -> None:
    active = get_all_validators()
    result = get_matching_validators("/proj/main.go", active)
    ids = {v.id.split("-", 1)[0] for v in result}
    # V06 (go-quality) + V09 (go-test) + V14 (complexity) at minimum.
    assert {"V06", "V09", "V14"}.issubset(ids)


def test_matching_validators_unrelated_extension_skips_irrelevant(fresh_index) -> None:
    """A .md edit shouldn't include go/ts/python-specific validators."""
    active = get_all_validators()
    result = get_matching_validators("/proj/README.md", active)
    ids = {v.id.split("-", 1)[0] for v in result}
    # V06/V09/V07/V10/V19/V11 are language-specific — none should match.
    assert "V06" not in ids
    assert "V07" not in ids
    assert "V19" not in ids


def test_matching_validators_respects_active_filter(fresh_index) -> None:
    """If V06 is excluded from active, it must not appear even if .go matches."""
    active_all = get_all_validators()
    active_no_v06 = [v for v in active_all if not v.id.startswith("V06")]
    result = get_matching_validators("/proj/main.go", active_no_v06)
    assert not any(v.id.startswith("V06") for v in result)


def test_matching_validators_no_duplicates(fresh_index) -> None:
    """A validator listed in both ext_index and residual must appear once."""
    active = get_all_validators()
    result = get_matching_validators("/proj/go.mod", active)
    ids = [v.id for v in result]
    assert len(ids) == len(set(ids))


def test_matching_validators_filename_pattern_matches_residual(fresh_index) -> None:
    """``go.mod`` is a filename-only pattern (residual bucket); validators
    declaring it should still match the file."""
    active = get_all_validators()
    result = get_matching_validators("/proj/go.mod", active)
    ids = {v.id.split("-", 1)[0] for v in result}
    # V06 declares ``go.mod`` and ``go.sum`` as patterns.
    assert "V06" in ids


def test_matching_validators_unknown_extension_falls_open(fresh_index) -> None:
    """An unrecognized extension still gets wildcard validators (V08, V12 etc)."""
    active = get_all_validators()
    result = get_matching_validators("/proj/file.xyz", active)
    # V08 (wildcard, no file_patterns) should always be there.
    assert any(v.id.startswith("V08") for v in result)


# ── Equivalence — Phase64.2 must not change which validators match ───────


def test_dispatch_index_matches_legacy_should_run(fresh_index) -> None:
    """For a representative file set, get_matching_validators(file, active)
    must equal [v for v in active if v.should_run(file)] — same set, no
    silent additions or omissions."""
    active = get_all_validators()
    files = [
        "/proj/main.go",
        "/proj/web/src/app.tsx",
        "/proj/web/src/utils.ts",
        "/proj/server/handler.py",
        "/proj/Dockerfile",
        "/proj/docker-compose.yaml",
        "/proj/go.mod",
        "/proj/buf.yaml",
        "/proj/README.md",
        "/proj/.env.example",
    ]
    for f in files:
        legacy = sorted([v.id for v in active if v.should_run(f)])
        new = sorted([v.id for v in get_matching_validators(f, active)])
        assert legacy == new, f"mismatch for {f}: legacy={legacy} new={new}"
