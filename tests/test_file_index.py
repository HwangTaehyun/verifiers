"""Tests for lib/file_index.py — Phase 65 single-walk project index."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lib.file_index import (
    DEFAULT_PRUNE_NAMES,
    FileEntry,
    ProjectFileIndex,
    _extract_pure_ext,
    _glob_prefixes,
    _matches_any_prefix,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


# ── _extract_pure_ext ────────────────────────────────────────────────────


def test_pure_ext_simple() -> None:
    assert _extract_pure_ext("*.go") == ".go"
    assert _extract_pure_ext("*.tsx") == ".tsx"


def test_pure_ext_recursive_glob() -> None:
    assert _extract_pure_ext("**/*.py") == ".py"


def test_pure_ext_uppercase_lowercased() -> None:
    assert _extract_pure_ext("*.GO") == ".go"


def test_pure_ext_filename_glob_returns_none() -> None:
    assert _extract_pure_ext("Dockerfile*") is None
    assert _extract_pure_ext("buf.yaml") is None
    assert _extract_pure_ext(".golangci.yml") is None


def test_pure_ext_compound_returns_none() -> None:
    """``*.tar.gz`` doesn't end in a single ``*.<token>`` so it's not
    classified as pure-ext (falls through to fnmatch)."""
    assert _extract_pure_ext("*.tar.gz") is None


# ── _glob_prefixes ────────────────────────────────────────────────────────


def test_glob_prefix_strips_double_star() -> None:
    assert _glob_prefixes(("vendor/**",)) == ["vendor"]
    assert _glob_prefixes(("web/build/**",)) == ["web/build"]


def test_glob_prefix_strips_single_star() -> None:
    assert _glob_prefixes(("dist/*",)) == ["dist"]


def test_glob_prefix_preserves_any_depth_form() -> None:
    """``**/__generated__/**`` keeps the ``**/`` so we know it's an
    any-depth basename match, not a path prefix."""
    assert _glob_prefixes(("**/__generated__/**",)) == ["**/__generated__"]


def test_glob_prefix_skips_file_globs() -> None:
    """Patterns like ``*.tmp`` or ``foo*.bak`` are file-level filters,
    not directory prunes — return [] so the walk doesn't try to skip
    them at the dir level."""
    assert _glob_prefixes(("*.tmp",)) == []
    assert _glob_prefixes(("**/*.bak",)) == []


def test_glob_prefix_multiple() -> None:
    out = _glob_prefixes(("vendor/**", "web/build/**", "**/__generated__/**"))
    assert out == ["vendor", "web/build", "**/__generated__"]


# ── _matches_any_prefix ──────────────────────────────────────────────────


def test_matches_exact_prefix() -> None:
    assert _matches_any_prefix("vendor", ["vendor"]) is True
    assert _matches_any_prefix("web/build", ["web/build"]) is True


def test_matches_starts_with_prefix() -> None:
    assert _matches_any_prefix("vendor/a/b", ["vendor"]) is True
    assert _matches_any_prefix("web/build/static/foo", ["web/build"]) is True


def test_matches_any_depth_form() -> None:
    """``**/__generated__`` matches any path component named ``__generated__``."""
    assert _matches_any_prefix("server/foo/__generated__", ["**/__generated__"]) is True
    assert _matches_any_prefix("__generated__", ["**/__generated__"]) is True
    assert _matches_any_prefix("a/b/c", ["**/__generated__"]) is False


def test_matches_no_match() -> None:
    assert _matches_any_prefix("other/dir", ["vendor", "web/build"]) is False


def test_matches_partial_segment_does_not() -> None:
    """``vendorlib`` should NOT match prefix ``vendor``."""
    assert _matches_any_prefix("vendorlib", ["vendor"]) is False


# ── FileEntry ────────────────────────────────────────────────────────────


def test_file_entry_immutable() -> None:
    entry = FileEntry(path=Path("/x"), size=10, mtime_ns=20)
    with pytest.raises((AttributeError, TypeError)):
        entry.size = 999  # type: ignore[misc]


# ── ProjectFileIndex.build ───────────────────────────────────────────────


def test_build_indexes_basic_files(project: Path) -> None:
    (project / "a.go").write_text("package a\n")
    (project / "b.py").write_text("x = 1\n")
    (project / "Dockerfile").write_text("FROM alpine\n")

    idx = ProjectFileIndex.build(project)
    assert idx.total == 3
    assert len(idx.find_by_pattern("*.go")) == 1
    assert len(idx.find_by_pattern("*.py")) == 1
    assert len(idx.find_by_pattern("Dockerfile*")) == 1


def test_build_recursive(project: Path) -> None:
    (project / "src").mkdir()
    (project / "src" / "deep").mkdir()
    (project / "src" / "deep" / "x.go").write_text("package deep\n")
    (project / "main.go").write_text("package main\n")

    idx = ProjectFileIndex.build(project)
    assert idx.total == 2
    paths = idx.find_by_pattern("*.go")
    assert len(paths) == 2


def test_build_prunes_default_dirs(project: Path) -> None:
    """node_modules, .git, vendor, etc. are pruned regardless of config."""
    for noise in ("node_modules", ".git", "vendor", "__pycache__", ".venv"):
        (project / noise).mkdir()
        (project / noise / "junk.go").write_text("noise\n")
    (project / "real.go").write_text("real\n")

    idx = ProjectFileIndex.build(project)
    assert idx.total == 1
    assert idx.find_by_pattern("*.go")[0].name == "real.go"


def test_build_default_prune_names_set() -> None:
    """The hardcoded set covers the universal noise dirs."""
    expected_subset = {".git", "node_modules", "vendor", "__pycache__", ".venv"}
    assert expected_subset.issubset(DEFAULT_PRUNE_NAMES)


def test_build_honors_exclude_globs(project: Path) -> None:
    (project / "src").mkdir()
    (project / "src" / "real.go").write_text("real\n")
    (project / "gen").mkdir()
    (project / "gen" / "auto.go").write_text("auto\n")

    idx = ProjectFileIndex.build(project, exclude_globs=("gen/**",))
    paths = idx.find_by_pattern("*.go")
    assert len(paths) == 1
    assert paths[0].name == "real.go"


def test_build_honors_nested_exclude(project: Path) -> None:
    (project / "web").mkdir()
    (project / "web" / "src").mkdir()
    (project / "web" / "src" / "app.tsx").write_text("x\n")
    (project / "web" / "build").mkdir()
    (project / "web" / "build" / "out.tsx").write_text("y\n")

    idx = ProjectFileIndex.build(project, exclude_globs=("web/build/**",))
    paths = idx.find_by_pattern("*.tsx")
    assert len(paths) == 1
    assert paths[0].name == "app.tsx"


def test_build_honors_any_depth_exclude(project: Path) -> None:
    """``**/__generated__/**`` prunes any directory named __generated__."""
    (project / "real.go").write_text("real\n")
    (project / "deep" / "nested" / "__generated__").mkdir(parents=True)
    (project / "deep" / "nested" / "__generated__" / "auto.go").write_text("auto\n")

    idx = ProjectFileIndex.build(project, exclude_globs=("**/__generated__/**",))
    paths = idx.find_by_pattern("*.go")
    assert len(paths) == 1


def test_build_handles_missing_root() -> None:
    idx = ProjectFileIndex.build("/nonexistent/path")
    assert idx.total == 0


def test_build_does_not_follow_symlinks(project: Path, tmp_path_factory) -> None:
    """Symlink to outside should NOT be followed (no infinite loops, no
    accidental indexing of unrelated trees).

    Note: ``project`` and pytest's default ``tmp_path`` share the same
    base dir, so we use ``tmp_path_factory`` to create the symlink
    target in a TRULY separate tree — otherwise os.walk would discover
    the target naturally (not through the link).
    """
    target_dir = tmp_path_factory.mktemp("symlink_target_outside")
    (target_dir / "stranger.go").write_text("not mine\n")

    (project / "real.go").write_text("real\n")
    try:
        (project / "link").symlink_to(target_dir)
    except OSError:
        pytest.skip("symlinks not supported on this filesystem")

    idx = ProjectFileIndex.build(project)
    # Only real.go indexed; symlinked file is NOT followed.
    paths = [p for p in idx.find_by_pattern("*.go")]
    assert paths == [project / "real.go"]


# ── find_by_pattern ──────────────────────────────────────────────────────


def test_find_pure_ext_path_uses_ext_index(project: Path) -> None:
    (project / "a.go").write_text("a\n")
    (project / "b.GO").write_text("b\n")  # uppercase ext

    idx = ProjectFileIndex.build(project)
    # Path.suffix.lower() makes both files land in ".go" bucket.
    paths = idx.find_by_pattern("*.go")
    assert len(paths) == 2


def test_find_filename_glob_uses_basename_index(project: Path) -> None:
    (project / "Dockerfile").write_text("FROM x\n")
    (project / "Dockerfile.prod").write_text("FROM y\n")
    (project / "DockerNOPE").write_text("not me\n")

    idx = ProjectFileIndex.build(project)
    paths = idx.find_by_pattern("Dockerfile*")
    names = sorted(p.name for p in paths)
    assert names == ["Dockerfile", "Dockerfile.prod"]


def test_find_dedups_across_patterns(project: Path) -> None:
    """A file matched by multiple patterns appears once."""
    (project / "a.go").write_text("a\n")

    idx = ProjectFileIndex.build(project)
    paths = idx.find_by_pattern("*.go", "a*", "*.go")
    assert len(paths) == 1


def test_find_empty_patterns_returns_empty(project: Path) -> None:
    (project / "a.go").write_text("a\n")
    idx = ProjectFileIndex.build(project)
    assert idx.find_by_pattern() == []


def test_find_no_matches(project: Path) -> None:
    (project / "a.go").write_text("a\n")
    idx = ProjectFileIndex.build(project)
    assert idx.find_by_pattern("*.never") == []


def test_find_by_basename_exact(project: Path) -> None:
    (project / "go.mod").write_text("module x\n")
    (project / "go.sum").write_text("h\n")

    idx = ProjectFileIndex.build(project)
    assert len(idx.find_by_basename("go.mod")) == 1
    assert idx.find_by_basename("nonexistent") == []


# ── hash_for_patterns ────────────────────────────────────────────────────


def test_hash_empty_patterns_returns_empty(project: Path) -> None:
    idx = ProjectFileIndex.build(project)
    assert idx.hash_for_patterns(()) == ""


def test_hash_deterministic(project: Path) -> None:
    (project / "a.go").write_text("a\n")
    (project / "b.go").write_text("b\n")

    idx1 = ProjectFileIndex.build(project)
    idx2 = ProjectFileIndex.build(project)
    assert idx1.hash_for_patterns(("**/*.go",)) == idx2.hash_for_patterns(("**/*.go",))


def test_hash_changes_on_file_modify(project: Path) -> None:
    f = project / "a.go"
    f.write_text("a\n")

    idx1 = ProjectFileIndex.build(project)
    h1 = idx1.hash_for_patterns(("**/*.go",))

    time.sleep(0.01)
    f.write_text("a modified\n")
    idx2 = ProjectFileIndex.build(project)
    h2 = idx2.hash_for_patterns(("**/*.go",))
    assert h1 != h2


def test_hash_pruned_dir_changes_dont_invalidate(project: Path) -> None:
    """Phase 65: changes inside DEFAULT_PRUNE_NAMES must NOT change hash."""
    (project / "a.go").write_text("a\n")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "dep.go").write_text("dep\n")

    idx1 = ProjectFileIndex.build(project)
    h1 = idx1.hash_for_patterns(("**/*.go",))

    time.sleep(0.01)
    (project / "node_modules" / "dep.go").write_text("dep changed\n")
    idx2 = ProjectFileIndex.build(project)
    h2 = idx2.hash_for_patterns(("**/*.go",))
    assert h1 == h2


def test_hash_excluded_dir_changes_dont_invalidate(project: Path) -> None:
    """User-excluded dir changes don't invalidate either."""
    (project / "a.go").write_text("a\n")
    (project / "thirdparty").mkdir()
    (project / "thirdparty" / "dep.go").write_text("dep\n")
    excludes = ("thirdparty/**",)

    idx1 = ProjectFileIndex.build(project, excludes)
    h1 = idx1.hash_for_patterns(("**/*.go",))

    time.sleep(0.01)
    (project / "thirdparty" / "dep.go").write_text("dep changed\n")
    idx2 = ProjectFileIndex.build(project, excludes)
    h2 = idx2.hash_for_patterns(("**/*.go",))
    assert h1 == h2


# ── Integration with ProjectContext ──────────────────────────────────────


def test_project_context_caches_file_index(project: Path) -> None:
    """``ctx.file_index`` is computed lazily and cached for the ctx lifetime."""
    from lib.project_context import ProjectContext

    (project / "a.go").write_text("a\n")
    ctx = ProjectContext(str(project))

    idx1 = ctx.file_index
    idx2 = ctx.file_index
    # cached_property → same instance across accesses.
    assert idx1 is idx2
