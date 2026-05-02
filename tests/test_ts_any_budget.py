"""Tests for V65 — ts-any-budget (ratchet, Phase 72).

Covers:
  - No TS files → no findings, no baseline written
  - First run → baseline established silently, no finding
  - Same count subsequent run → no finding
  - Increased count → V65-ANY-BUDGET-EXCEEDED with delta
  - Decreased count → ratchet down (baseline auto-updated), no finding
  - Generated files (.gen., __generated__) excluded from count
  - Each pattern variant counted (`: any`, `as any`, `<any>`, `@ts-expect-error`, `@ts-ignore`)
  - Corrupted baseline → re-established, no finding
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hooks.validators.ts_any_budget import TsAnyBudgetValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> TsAnyBudgetValidator:
    return TsAnyBudgetValidator()


def _baseline_path(tmp_project: Path) -> Path:
    return tmp_project / ".verifiers" / "ts-any-baseline.json"


def _write_ts(tmp_project, name: str, body: str) -> Path:
    f = tmp_project / "web" / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


def _refresh_ctx(tmp_project) -> ProjectContext:
    """Build a fresh ProjectContext (since file_index is cached)."""
    return ProjectContext(tmp_project)


# ── 1. No TS files ───────────────────────────────────────────────────────────


class TestNoTsProject:
    def test_no_ts_files_no_findings(self, validator, tmp_project, project_ctx):
        findings = validator.validate_project(project_ctx)
        assert findings == []
        assert not _baseline_path(tmp_project).exists()


# ── 2. First-run grace ───────────────────────────────────────────────────────


class TestFirstRunGrace:
    def test_first_run_establishes_baseline_no_finding(self, validator, tmp_project):
        _write_ts(tmp_project, "a.ts", "function f(x: any): any { return x; }\n")
        ctx = _refresh_ctx(tmp_project)
        findings = validator.validate_project(ctx)
        assert findings == []
        bp = _baseline_path(tmp_project)
        assert bp.exists()
        data = json.loads(bp.read_text())
        assert data["count"] == 2  # `: any` × 2
        assert data["history"] == [2]


# ── 3. Stable count → silent ─────────────────────────────────────────────────


class TestStableCountSilent:
    def test_same_count_no_finding(self, validator, tmp_project):
        _write_ts(tmp_project, "a.ts", "function f(x: any): any { return x; }\n")
        ctx = _refresh_ctx(tmp_project)
        validator.validate_project(ctx)  # establish baseline
        # Second run, identical content
        ctx2 = _refresh_ctx(tmp_project)
        findings = validator.validate_project(ctx2)
        assert findings == []


# ── 4. Increased → V65-ANY-BUDGET-EXCEEDED ───────────────────────────────────


class TestBudgetExceeded:
    def test_increase_flagged(self, validator, tmp_project):
        f = _write_ts(tmp_project, "a.ts", "function f(x: any): any { return x; }\n")
        ctx = _refresh_ctx(tmp_project)
        validator.validate_project(ctx)  # baseline = 2

        # Add another `any`
        f.write_text("function f(x: any): any { return x as any; }\n")
        ctx2 = _refresh_ctx(tmp_project)
        findings = validator.validate_project(ctx2)
        assert len(findings) == 1
        assert findings[0].rule == "V65-ANY-BUDGET-EXCEEDED"
        assert findings[0].severity == "error"
        assert "2 → 3" in findings[0].message or "+1" in findings[0].message


# ── 5. Decrease → ratchet down ───────────────────────────────────────────────


class TestRatchetDown:
    def test_decrease_updates_baseline(self, validator, tmp_project):
        f = _write_ts(tmp_project, "a.ts", "function f(x: any): any { return x as any; }\n")
        ctx = _refresh_ctx(tmp_project)
        validator.validate_project(ctx)  # baseline = 3
        assert json.loads(_baseline_path(tmp_project).read_text())["count"] == 3

        # Remove one `any`
        f.write_text("function f(x: number): any { return x; }\n")
        ctx2 = _refresh_ctx(tmp_project)
        findings = validator.validate_project(ctx2)
        assert findings == []
        # Baseline ratcheted down to 1
        data = json.loads(_baseline_path(tmp_project).read_text())
        assert data["count"] == 1
        assert data["history"] == [3, 1]


# ── 6. Generated files excluded ──────────────────────────────────────────────


class TestGeneratedFilesExcluded:
    def test_gen_files_not_counted(self, validator, tmp_project):
        # Real source: 1 any
        _write_ts(tmp_project, "a.ts", "let x: any;\n")
        # Generated: many anys, must not count
        gen = tmp_project / "web" / "src" / "schema.gen.ts"
        gen.write_text("export type T = any; export type U = any; export type V = any;\n")
        gen2 = tmp_project / "web" / "src" / "__generated__" / "types.ts"
        gen2.parent.mkdir(parents=True, exist_ok=True)
        gen2.write_text("export type X = any;\n")

        ctx = _refresh_ctx(tmp_project)
        validator.validate_project(ctx)
        data = json.loads(_baseline_path(tmp_project).read_text())
        assert data["count"] == 1  # only a.ts


# ── 7. Each pattern variant counted ──────────────────────────────────────────


class TestPatternVariants:
    def test_all_variants_counted(self, validator, tmp_project):
        _write_ts(
            tmp_project,
            "a.ts",
            "let x: any;\n"                    # `: any` × 1
            "const y = z as any;\n"            # `as any` × 1
            "type T = Map<any, string>;\n"     # `<any` × 1
            "// @ts-expect-error - legacy\n"   # @ts-expect-error × 1
            "// @ts-ignore - other\n",         # @ts-ignore × 1
        )
        ctx = _refresh_ctx(tmp_project)
        validator.validate_project(ctx)
        data = json.loads(_baseline_path(tmp_project).read_text())
        assert data["count"] == 5


# ── 8. Corrupted baseline → re-established ───────────────────────────────────


class TestCorruptedBaseline:
    def test_corrupted_baseline_recovered(self, validator, tmp_project):
        _write_ts(tmp_project, "a.ts", "let x: any;\n")
        bp = _baseline_path(tmp_project)
        bp.parent.mkdir(parents=True, exist_ok=True)
        bp.write_text("not-valid-json{{{")
        ctx = _refresh_ctx(tmp_project)
        findings = validator.validate_project(ctx)
        assert findings == []
        # Baseline re-established with current count (= 1)
        data = json.loads(bp.read_text())
        assert data["count"] == 1


# ── 9. validate_file is no-op (Stop-only) ────────────────────────────────────


class TestValidateFileNoOp:
    def test_validate_file_returns_empty(self, validator, tmp_project, project_ctx):
        f = _write_ts(tmp_project, "a.ts", "let x: any;\n")
        # Default validate_file from BaseValidator returns []
        findings = validator.validate_file(project_ctx, str(f))
        assert findings == []
