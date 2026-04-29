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


class TestCheckHandlerCoverage:
    """Tests for rpc-to-handler coverage checking."""

    def test_unimplemented_rpc_produces_finding(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """An rpc method declared in a proto file with no matching handler
        should produce a V03-UNIMPLEMENTED-RPC warning."""
        # Write a proto file with one service and one rpc
        proto_dir = tmp_project / "server" / "proto"
        proto_file = proto_dir / "greeter.proto"
        proto_file.write_text(
            'syntax = "proto3";\nservice GreeterService {\n  rpc SayHello (HelloRequest) returns (HelloReply);\n}\n'
        )

        # No handler files exist -> method is unimplemented
        findings = validator._check_handler_coverage(project_ctx)

        assert len(findings) == 1
        assert findings[0].rule == "V03-UNIMPLEMENTED-RPC"
        assert "SayHello" in findings[0].message
        assert "GreeterService" in findings[0].message
        assert findings[0].severity == "warning"

    def test_all_rpcs_implemented_no_finding(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """When every rpc method has a matching handler, no findings should
        be produced."""
        # Proto with one rpc
        proto_dir = tmp_project / "server" / "proto"
        proto_file = proto_dir / "greeter.proto"
        proto_file.write_text(
            'syntax = "proto3";\nservice GreeterService {\n  rpc SayHello (HelloRequest) returns (HelloReply);\n}\n'
        )

        # Create a handler file that implements SayHello
        handler_dir = tmp_project / "server" / "internal" / "greeter"
        handler_dir.mkdir(parents=True, exist_ok=True)
        handler_file = handler_dir / "handler.go"
        handler_file.write_text(
            "package greeter\n\n"
            "func (s *GreeterService) SayHello(ctx context.Context, req *connect.Request) "
            "(*connect.Response, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        findings = validator._check_handler_coverage(project_ctx)

        assert len(findings) == 0

    def test_multiple_rpcs_partial_implementation(
        self, tmp_project: Path, project_ctx: ProjectContext, validator: ProtoConnectValidator
    ) -> None:
        """Only the unimplemented rpc methods should appear in findings."""
        proto_dir = tmp_project / "server" / "proto"
        proto_file = proto_dir / "greeter.proto"
        proto_file.write_text(
            'syntax = "proto3";\n'
            "service GreeterService {\n"
            "  rpc SayHello (HelloRequest) returns (HelloReply);\n"
            "  rpc SayGoodbye (GoodbyeRequest) returns (GoodbyeReply);\n"
            "}\n"
        )

        handler_dir = tmp_project / "server" / "internal" / "greeter"
        handler_dir.mkdir(parents=True, exist_ok=True)
        handler_file = handler_dir / "handler.go"
        handler_file.write_text(
            "package greeter\n\n"
            "func (s *GreeterService) SayHello(ctx context.Context, req *connect.Request) "
            "(*connect.Response, error) {\n"
            "    return nil, nil\n"
            "}\n"
        )

        findings = validator._check_handler_coverage(project_ctx)

        assert len(findings) == 1
        assert "SayGoodbye" in findings[0].message


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
