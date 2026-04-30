"""V03: Proto language validator — buf lint + gen-staleness only.

Phase50 consolidation: V03 narrowed to proto-language concerns. Two
former rules moved out:
  - V03-UNIMPLEMENTED-RPC → V27-UNIMPLEMENTED-RPC (handler-runtime
    layer; V27 enforces the strict Connect handler signature shape).
  - V03-BREAKING → V23-BREAKING-<RULE> (governance layer; V23 preserves
    Buf's per-rule code as the finding suffix for selective disabling).

Checks owned by V03 now:
  V03-BUF-LINT: buf lint violations on .proto files
  V03-STALE-GEN: Proto files changed but gen/ code not regenerated
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.codegen_staleness import is_codegen_stale
from lib.hash_cache import HashCache
from lib.project_context import ProjectContext


class ProtoConnectValidator(BaseValidator):
    """V03: Proto/Connect-RPC Validator."""

    id = "V03-proto-connect"
    name = "Proto/Connect-RPC Validator"
    file_patterns: list[str] = [
        "**/proto/**/*.proto",
        "**/buf.yaml",
        "**/buf.gen.yaml",
        "**/gen/**/*.go",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.hash_cache = HashCache()

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-file buf lint + stale-gen check (Tier 2)."""
        if not ctx.proto_dir or not ctx.proto_dir.exists():
            return []
        findings: list[Finding] = []
        findings.extend(self._check_buf_lint(ctx))
        findings.extend(self._check_stale_generated(ctx))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide proto sweep (Tier 3).

        Phase50: handler coverage + breaking checks moved out (V27 / V23).
        """
        if not ctx.proto_dir or not ctx.proto_dir.exists():
            return []
        findings: list[Finding] = []
        findings.extend(self._check_buf_lint(ctx))
        findings.extend(self._check_stale_generated(ctx))
        return findings

    # ── Check 1: buf lint ────────────────────────────────────────────────

    def _check_buf_lint(self, ctx: ProjectContext) -> list[Finding]:
        """Run buf lint on proto files."""
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        try:
            result = subprocess.run(
                ["buf", "lint"],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings  # buf not installed or timed out

        if result.returncode != 0:
            for line in result.stderr.strip().split("\n"):
                if not line.strip():
                    continue
                # buf lint output: "proto/foo.proto:10:3:FIELD_LOWER_SNAKE_CASE"
                parts = line.split(":")
                if len(parts) >= 4:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(ctx.server_dir / parts[0]),
                            rule="V03-BUF-LINT",
                            message=line.strip(),
                            fix=f"Fix the lint violation in {parts[0]} at line {parts[1]}",
                            line=int(parts[1]) if parts[1].isdigit() else None,
                        )
                    )
                elif parts:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(ctx.proto_dir),
                            rule="V03-BUF-LINT",
                            message=line.strip(),
                            fix="Fix the buf lint violation reported above",
                        )
                    )

        return findings

    # ── Check 2: Stale generated code ────────────────────────────────────

    def _check_stale_generated(self, ctx: ProjectContext) -> list[Finding]:
        """Proto files hash vs gen/ mtime comparison.

        Phase51: hash + mtime two-step algorithm extracted to
        ``lib.codegen_staleness.is_codegen_stale`` and shared with V02.
        """
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        # Collect proto input files
        proto_files = list(ctx.proto_dir.rglob("*.proto"))
        buf_gen = ctx.server_dir / "buf.gen.yaml"
        input_files = [*proto_files]
        if buf_gen.exists():
            input_files.append(buf_gen)

        # Check gen/ directory exists
        gen_dir = ctx.server_dir / "gen"
        if not gen_dir.exists():
            return findings

        gen_go_files = list(gen_dir.rglob("*.go"))

        if is_codegen_stale(
            cache=self.hash_cache,
            category="proto",
            project=ctx.project_name or "unknown",
            input_files=input_files,
            generated_files=gen_go_files,
        ):
            build_cmd = "just generate" if ctx.build_tool == "just" else "make generate_buf"
            findings.append(
                Finding(
                    severity="error",
                    file=str(ctx.proto_dir),
                    rule="V03-STALE-GEN",
                    message="Proto files changed but generated code not regenerated",
                    fix=f"Run '{build_cmd}' in {ctx.server_dir} directory",
                )
            )

        return findings

    # ── Phase50 consolidation ────────────────────────────────────────────
    #
    # _check_handler_coverage and _check_breaking removed in Phase50:
    #   - V03-UNIMPLEMENTED-RPC → V27-UNIMPLEMENTED-RPC. V27 enforces the
    #     strict Connect handler signature shape (ctx + *connect.Request[T]
    #     + *connect.Response[T]) which is more accurate than V03's loose
    #     `func (recv) MethodName(` regex. Non-Connect projects no longer
    #     get this check from V03; they should disable V27 explicitly and
    #     rely on `buf lint` / IDE tooling for handler-level checks.
    #
    #   - V03-BREAKING → V23-BREAKING-<RULE>. V23 preserves the original
    #     Buf rule code as the finding suffix (e.g. V23-BREAKING-FIELD_NO_DELETE)
    #     enabling per-rule selective disabling via .verifiers/config.yaml
    #     `validators.disabled: ["V23-BREAKING-FIELD_SAME_NAME"]`. V03's
    #     coarse single-rule emit was duplicated noise.
    #
    # Both deletions: see Phase50 commit message + CHANGELOG entry.


# ── Standalone execution ─────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone PostToolUse hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = ProtoConnectValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
