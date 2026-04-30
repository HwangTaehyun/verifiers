"""Tests for V03 Proto/Connect-RPC validator."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hooks.validators.proto_connect import ProtoConnectValidator
from lib.project_context import ProjectContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> ProtoConnectValidator:
    """Create a fresh ProtoConnectValidator instance."""
    return ProtoConnectValidator()


# ---------------------------------------------------------------------------
# _check_handler_coverage
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase50: TestCheckHandlerCoverage removed.
# V03-UNIMPLEMENTED-RPC consolidated into V27-UNIMPLEMENTED-RPC, which
# enforces the strict Connect handler signature shape (ctx +
# *connect.Request[T] + *connect.Response[T]). See tests/test_connect_handler.py
# class TestUnimplementedRpc for the surviving coverage.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _check_stale_generated
# ---------------------------------------------------------------------------


class TestCheckStaleGenerated:
    """Tests for stale generated-code detection."""

    def test_stale_gen_detected_when_proto_newer(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """If proto files have a newer mtime than gen/ Go files and the hash
        has changed, a V03-STALE-GEN error should be raised."""
        proto_dir = tmp_project / "server" / "proto"
        gen_dir = tmp_project / "server" / "gen"

        # Create generated Go file first (older)
        gen_go = gen_dir / "service.connect.go"
        gen_go.write_text("// generated code\npackage gen\n")

        # Ensure proto file is strictly newer
        time.sleep(0.05)
        proto_file = proto_dir / "service.proto"
        proto_file.write_text('syntax = "proto3";\nservice Foo {}\n')

        # Prime the cache with a different hash so has_changed returns True
        validator.hash_cache.set("proto", project_ctx.project_name or "unknown", "stale-hash")

        findings = validator._check_stale_generated(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V03-STALE-GEN"
        assert findings[0].severity == "error"
        assert "regenerated" in findings[0].message.lower()

    def test_no_stale_when_gen_newer(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """If generated code is newer than proto files, no V03-STALE-GEN
        should be reported (even if hashes differ, mtime acts as a guard)."""
        proto_dir = tmp_project / "server" / "proto"
        gen_dir = tmp_project / "server" / "gen"

        # Create proto file first (older)
        proto_file = proto_dir / "service.proto"
        proto_file.write_text('syntax = "proto3";\nservice Foo {}\n')

        # Generated file is newer
        time.sleep(0.05)
        gen_go = gen_dir / "service.connect.go"
        gen_go.write_text("// generated code\npackage gen\n")

        # Force has_changed by setting a stale cache value
        validator.hash_cache.set("proto", project_ctx.project_name or "unknown", "stale-hash")

        findings = validator._check_stale_generated(project_ctx)

        assert len(findings) == 0


# ---------------------------------------------------------------------------
# No proto_dir → graceful skip
# ---------------------------------------------------------------------------


class TestNoProtoDir:
    """Tests for graceful handling when proto directory does not exist."""

    def test_no_proto_dir_returns_empty_result(self, tmp_path: Path, validator: ProtoConnectValidator) -> None:
        """When no proto directory exists at all, validate() should return
        an empty ValidationResult without raising."""
        # Minimal project with no server/proto
        (tmp_path / ".git").mkdir()
        ctx = ProjectContext(tmp_path)

        assert ctx.proto_dir is None

        result = validator.run(ctx, mode="stop")

        assert result.validator_id == "V03-proto-connect"
        assert len(result.findings) == 0

    def test_empty_proto_dir_no_crash(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """An existing but empty proto directory should not crash and should
        produce no stale-gen or handler findings."""
        # proto dir exists (from fixture) but has no .proto files
        result = validator.run(project_ctx, mode="stop")

        # buf lint / breaking may or may not produce findings depending on
        # buf being installed, but handler + stale checks should be clean
        handler_findings = [f for f in result.findings if f.rule == "V03-UNIMPLEMENTED-RPC"]
        stale_findings = [f for f in result.findings if f.rule == "V03-STALE-GEN"]
        assert handler_findings == []
        assert stale_findings == []
