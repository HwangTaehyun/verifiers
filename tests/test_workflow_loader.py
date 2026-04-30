"""Tests for lib/workflow_loader.py — Phase60 extraction.

The directory walk + YAML parse pattern was duplicated across 6
validators (V37, V40, V41, V43, V57, V58). Phase 60 extracted the
walker; these tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.workflow_loader import parse_workflow, walk_workflow_paths, walk_workflows


def _write(p: Path, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ── walk_workflow_paths ──────────────────────────────────────────────


class TestWalkWorkflowPaths:
    def test_no_workflows_dir_yields_nothing(self, tmp_path: Path) -> None:
        assert list(walk_workflow_paths(tmp_path)) == []

    def test_yml_and_yaml_both_picked_up(self, tmp_path: Path) -> None:
        _write(tmp_path / ".github/workflows/ci.yml", "name: CI\non: push\n")
        _write(tmp_path / ".github/workflows/release.yaml", "name: R\non: push\n")
        paths = list(walk_workflow_paths(tmp_path))
        names = {p.name for p in paths}
        assert names == {"ci.yml", "release.yaml"}

    def test_sorted_order(self, tmp_path: Path) -> None:
        for name in ("zeta.yml", "alpha.yml", "mu.yml"):
            _write(tmp_path / f".github/workflows/{name}", "name: x\non: push\n")
        names = [p.name for p in walk_workflow_paths(tmp_path)]
        # All .yml come before .yaml (separate glob passes); within each, sorted.
        assert names == ["alpha.yml", "mu.yml", "zeta.yml"]

    def test_dedup_yml_yaml_same_resolved_path(self, tmp_path: Path) -> None:
        # Symlink edge case: same file via both globs (not common but possible).
        # Just verify no path appears twice.
        _write(tmp_path / ".github/workflows/a.yml", "name: a\non: push\n")
        _write(tmp_path / ".github/workflows/b.yaml", "name: b\non: push\n")
        paths = list(walk_workflow_paths(tmp_path))
        resolved = [p.resolve() for p in paths]
        assert len(resolved) == len(set(resolved))

    def test_lazy_iteration_supports_early_break(self, tmp_path: Path) -> None:
        for i in range(10):
            _write(tmp_path / f".github/workflows/wf{i}.yml", f"name: w{i}\non: push\n")
        # Iterate and break after first
        seen = []
        for p in walk_workflow_paths(tmp_path):
            seen.append(p)
            break
        assert len(seen) == 1


# ── walk_workflows (path + parsed YAML) ──────────────────────────────


class TestWalkWorkflows:
    def test_yields_path_and_dict(self, tmp_path: Path) -> None:
        _write(
            tmp_path / ".github/workflows/ci.yml",
            "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        )
        results = list(walk_workflows(tmp_path))
        assert len(results) == 1
        path, data = results[0]
        assert path.name == "ci.yml"
        assert data["name"] == "CI"
        assert "jobs" in data

    def test_malformed_yaml_skipped_silently(self, tmp_path: Path) -> None:
        _write(tmp_path / ".github/workflows/bad.yml", "key: : : invalid\n")
        _write(tmp_path / ".github/workflows/good.yml", "name: OK\non: push\n")
        results = list(walk_workflows(tmp_path))
        names = [p.name for p, _ in results]
        assert names == ["good.yml"]  # bad.yml dropped

    def test_empty_yaml_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path / ".github/workflows/empty.yml", "")
        _write(tmp_path / ".github/workflows/ok.yml", "name: x\n")
        results = list(walk_workflows(tmp_path))
        names = [p.name for p, _ in results]
        assert names == ["ok.yml"]

    def test_list_root_yaml_skipped(self, tmp_path: Path) -> None:
        # YAML where root is a list (not a dict) — skip silently.
        _write(tmp_path / ".github/workflows/list.yml", "- item1\n- item2\n")
        _write(tmp_path / ".github/workflows/dict.yml", "name: x\n")
        results = list(walk_workflows(tmp_path))
        names = [p.name for p, _ in results]
        assert names == ["dict.yml"]


# ── parse_workflow ───────────────────────────────────────────────────


class TestParseWorkflow:
    def test_valid_yaml_returns_dict(self, tmp_path: Path) -> None:
        # PyYAML 1.1 mode coerces unquoted `on:` key to `True` (boolean).
        # That's the default safe_load behavior; assert the structure
        # we care about (name field) rather than the full dict shape.
        p = _write(tmp_path / "wf.yml", "name: CI\n'on': push\n")
        data = parse_workflow(p)
        assert data is not None
        assert data["name"] == "CI"
        assert data["on"] == "push"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert parse_workflow(tmp_path / "ghost.yml") is None

    def test_malformed_returns_none(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "bad.yml", "key: : : invalid\n")
        assert parse_workflow(p) is None

    def test_list_root_returns_none(self, tmp_path: Path) -> None:
        p = _write(tmp_path / "list.yml", "- a\n- b\n")
        assert parse_workflow(p) is None


@pytest.fixture
def _placeholder() -> None:
    """Keep lint quiet; no shared fixtures used here."""
    return None
