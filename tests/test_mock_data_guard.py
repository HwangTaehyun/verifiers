"""Tests for V18: Mock Data Guard validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.mock_data_guard import MockDataGuardValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> MockDataGuardValidator:
    return MockDataGuardValidator()


# ── V18-MOCK-VARIABLE ────────────────────────────────────────────────────────


class TestMockVariable:
    def test_detects_mock_const(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLeaderboardData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { useState } from 'react';\n"
            "const MOCK_ENTRIES = [{ rank: 1 }];\n"
            "export function useLeaderboardData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(errors) == 1
        assert "MOCK_ENTRIES" in errors[0].message

    def test_detects_lowercase_mock(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useTrendsData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "const mockStats = { value: 6.12 };\n"
            "export function useTrendsData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(errors) == 1

    def test_detects_fake_and_dummy(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "const FAKE_USERS = [];\n"
            "let dummyResponse = {};\n"
            "export function useData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(errors) == 2

    def test_ignores_comments(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "// const MOCK_DATA = [];\n"
            "/* const mockEntries = []; */\n"
            "import { client } from '../api/client';\n"
            "export function useData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(errors) == 0

    def test_clean_hook_no_findings(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLeaderboardData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { leaderboardClient } from '../api/client';\n"
            "export function useLeaderboardData() {\n"
            "  const resp = await leaderboardClient.getGlobalLeaderboard({});\n"
            "  setEntries(resp.entries);\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(errors) == 0


# ── V18-MOCK-DATA (hardcoded state) ──────────────────────────────────────────


class TestHardcodedState:
    def test_detects_hardcoded_setstate(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLandingData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { useState } from 'react';\n"
            "export function useLandingData() {\n"
            "  setStats({ rank: 1, score: 8.5, username: 'test' });\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-DATA"]
        assert len(errors) >= 1

    def test_allows_error_state_reset(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        """setPagination({ totalCount: 0 }) is an error reset, not mock data."""
        hook = tmp_project / "web" / "src" / "hooks" / "useLeaderboardData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { leaderboardClient } from '../api/client';\n"
            "export function useLeaderboardData() {\n"
            "  setPagination({ page: 1, pageSize: 20, totalCount: 0, totalPages: 0 });\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-DATA"]
        assert len(errors) == 0

    def test_allows_api_response_mapping(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLandingData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { dashboardClient } from '../api/client';\n"
            "export function useLandingData() {\n"
            "  const resp = await dashboardClient.getLandingStats({});\n"
            "  setStats(resp);\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-MOCK-DATA"]
        assert len(errors) == 0


# ── V18-FAKE-DELAY ───────────────────────────────────────────────────────────


class TestFakeDelay:
    def test_detects_setTimeout_promise(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { client } from '../api/client';\n"
            "export function useData() {\n"
            "  await new Promise(resolve => setTimeout(resolve, 300));\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        warnings = [f for f in result.findings if f.rule == "V18-FAKE-DELAY"]
        assert len(warnings) == 1

    def test_detects_simulate_comment(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { client } from '../api/client';\n"
            "// Simulate network delay\n"
            "export function useData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        warnings = [f for f in result.findings if f.rule == "V18-FAKE-DELAY"]
        assert len(warnings) == 1


# ── V18-TODO-API ─────────────────────────────────────────────────────────────


class TestTodoApi:
    def test_detects_todo_replace_api(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { useState } from 'react';\n"
            "// TODO: Replace with actual API call when connected\n"
            "export function useData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-TODO-API"]
        assert len(errors) == 1

    def test_no_finding_for_unrelated_todo(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { client } from '../api/client';\n"
            "// TODO: Add error handling\n"
            "export function useData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-TODO-API"]
        assert len(errors) == 0


# ── V18-NO-API-IMPORT ────────────────────────────────────────────────────────


class TestNoApiImport:
    def test_detects_missing_api_import(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLeaderboardData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { useState, useCallback } from 'react';\n"
            "export function useLeaderboardData() {\n"
            "  return { entries: [], loading: false };\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-NO-API-IMPORT"]
        assert len(errors) == 1

    def test_allows_api_client_import(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useLeaderboardData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { leaderboardClient } from '../api/client';\n"
            "export function useLeaderboardData() {\n"
            "  return { entries: [], loading: false };\n"
            "}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-NO-API-IMPORT"]
        assert len(errors) == 0

    def test_allows_connectrpc_import(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hook = tmp_project / "web" / "src" / "hooks" / "useProfileData.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { createPromiseClient } from '@connectrpc/connect';\n"
            "export function useProfileData() {}\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-NO-API-IMPORT"]
        assert len(errors) == 0

    def test_skips_non_data_hooks(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        """useAuth.ts (not use*Data.ts) should not require API import."""
        hook = tmp_project / "web" / "src" / "hooks" / "useAuth.ts"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "import { useState } from 'react';\n"
            "export function useAuth() { return { user: null }; }\n"
        )
        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, str(hook))
        errors = [f for f in result.findings if f.rule == "V18-NO-API-IMPORT"]
        assert len(errors) == 0


# ── Stop mode (full scan) ───────────────────────────────────────────────────


class TestStopMode:
    def test_scans_all_hook_files(self, tmp_project: Path, validator: MockDataGuardValidator) -> None:
        hooks_dir = tmp_project / "web" / "src" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)

        # File 1: has mock data
        (hooks_dir / "useLeaderboardData.ts").write_text(
            "const MOCK_ENTRIES = [{ rank: 1 }];\n"
            "export function useLeaderboardData() {}\n"
        )
        # File 2: clean
        (hooks_dir / "useCountryData.ts").write_text(
            "import { leaderboardClient } from '../api/client';\n"
            "export function useCountryData() {\n"
            "  const resp = await leaderboardClient.getCountryRankings({});\n"
            "}\n"
        )
        # File 3: has mock data
        (hooks_dir / "useTrendsData.ts").write_text(
            "const mockChart = [{ date: 'Jan', value: 5 }];\n"
            "export function useTrendsData() {}\n"
        )

        ctx = ProjectContext(tmp_project)
        result = validator.validate(ctx, file_path=None, mode="stop")
        mock_errors = [f for f in result.findings if f.rule == "V18-MOCK-VARIABLE"]
        assert len(mock_errors) == 2  # Two files with mock variables


# ── should_run ───────────────────────────────────────────────────────────────


class TestShouldRun:
    def test_matches_hook_files(self, validator: MockDataGuardValidator) -> None:
        assert validator.should_run("web/src/hooks/useLeaderboardData.ts")
        assert validator.should_run("web/src/hooks/useTrendsData.tsx")
        assert validator.should_run("web/src/hooks/useAuth.ts")

    def test_skips_non_hook_files(self, validator: MockDataGuardValidator) -> None:
        assert not validator.should_run("web/src/api/client.ts")
        assert not validator.should_run("web/src/routes/pages/Leaderboard/index.tsx")
        assert not validator.should_run("server/internal/leaderboard/service.go")
