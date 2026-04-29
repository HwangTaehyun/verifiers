"""V03: Proto/Connect-RPC validator — buf lint, stale detection, handler mapping.

Checks:
  V03-BUF-LINT: buf lint violations on .proto files
  V03-STALE-GEN: Proto files changed but gen/ code not regenerated
  V03-UNIMPLEMENTED-RPC: rpc method defined in proto but no handler implementation
  V03-BREAKING: Breaking change detected vs main branch
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.hash_cache import HashCache, hash_files
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
        """Phase29+ API: project-wide proto sweep + breaking-change scan (Tier 3)."""
        if not ctx.proto_dir or not ctx.proto_dir.exists():
            return []
        findings: list[Finding] = []
        findings.extend(self._check_buf_lint(ctx))
        findings.extend(self._check_stale_generated(ctx))
        findings.extend(self._check_handler_coverage(ctx))
        findings.extend(self._check_breaking(ctx))
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
        """Proto files hash vs gen/ mtime comparison."""
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        # Collect proto input files
        proto_files = list(ctx.proto_dir.rglob("*.proto"))
        buf_gen = ctx.server_dir / "buf.gen.yaml"
        input_files = [*proto_files]
        if buf_gen.exists():
            input_files.append(buf_gen)

        if not input_files:
            return findings

        # Check gen/ directory exists
        gen_dir = ctx.server_dir / "gen"
        if not gen_dir.exists():
            return findings

        gen_go_files = list(gen_dir.rglob("*.go"))
        if not gen_go_files:
            return findings

        # Hash comparison
        current_hash = hash_files(input_files)
        has_changed = self.hash_cache.has_changed("proto", ctx.project_name or "unknown", current_hash)

        if has_changed:
            # mtime comparison as double check
            existing_protos = [f for f in proto_files if f.exists()]
            if existing_protos:
                latest_proto = max(f.stat().st_mtime for f in existing_protos)
                latest_gen = max(f.stat().st_mtime for f in gen_go_files)

                if latest_proto > latest_gen:
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

    # ── Check 3: Handler coverage ────────────────────────────────────────

    def _check_handler_coverage(self, ctx: ProjectContext) -> list[Finding]:
        """All rpc methods in proto should have handler implementations."""
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        # Extract rpc methods from proto files
        rpc_methods: dict[str, list[str]] = {}
        for proto_file in ctx.proto_dir.rglob("*.proto"):
            try:
                content = proto_file.read_text()
            except OSError:
                continue

            current_service: str | None = None
            for line in content.split("\n"):
                service_match = re.search(r"service\s+(\w+)\s*\{", line)
                if service_match:
                    current_service = service_match.group(1)
                    rpc_methods.setdefault(current_service, [])

                rpc_match = re.search(r"rpc\s+(\w+)\s*\(", line)
                if rpc_match and current_service:
                    rpc_methods[current_service].append(rpc_match.group(1))

        if not rpc_methods:
            return findings

        # Extract implemented methods from handler files.
        #
        # NOTE: We scan every .go file under internal/* (except *_test.go)
        # because Go projects use many naming conventions for handlers:
        #   - internal/auth/handler.go                    (template convention)
        #   - internal/finance/billing_schedule.go        (domain-split)
        #   - internal/user/user_handler.go               (suffix convention)
        # The previous glob `internal/*/handler*.go` only matched the first
        # style and produced false positives (V03-UNIMPLEMENTED-RPC) whenever
        # handlers lived in domain-named files.
        implemented: set[str] = set()
        for go_file in ctx.server_dir.glob("internal/*/*.go"):
            if go_file.name.endswith("_test.go"):
                continue
            try:
                content = go_file.read_text()
            except OSError:
                continue
            for match in re.finditer(r"func \([^)]+\) (\w+)\(", content):
                implemented.add(match.group(1))

        # Report missing implementations
        for service, methods in rpc_methods.items():
            for method in methods:
                if method not in implemented:
                    svc_lower = service.lower().replace("service", "")
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(ctx.proto_dir),
                            rule="V03-UNIMPLEMENTED-RPC",
                            message=f"rpc {service}.{method} has no handler implementation",
                            fix=(f"Create handler method {method}() in internal/{svc_lower}/handler.go"),
                        )
                    )

        return findings

    # ── Check 4: Breaking changes ────────────────────────────────────────

    def _check_breaking(self, ctx: ProjectContext) -> list[Finding]:
        """Detect breaking changes vs main branch."""
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        # Resolve the git *common* directory so this works inside git worktrees.
        #
        # In a worktree, `.git` is either missing (subdirectory) or a text
        # pointer file instead of a real git dir, so `buf breaking --against
        # .git#branch=main` fails with "does not appear to be a git
        # repository". We ask git for the absolute path to the shared git
        # directory and pass it to buf verbatim, which works in both normal
        # clones and worktrees.
        try:
            git_dir_result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if git_dir_result.returncode != 0:
            return findings

        git_common_dir = Path(git_dir_result.stdout.strip())
        if not git_common_dir.is_absolute():
            # `git rev-parse --git-common-dir` can return a relative path
            # when run inside the main worktree — resolve it against cwd.
            git_common_dir = (Path(ctx.server_dir) / git_common_dir).resolve()

        if not git_common_dir.exists():
            return findings

        against = f"{git_common_dir}#branch=main"

        try:
            result = subprocess.run(
                ["buf", "breaking", "--against", against],
                cwd=str(ctx.server_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stderr.strip():
            stderr = result.stderr.strip()
            # Filter out git-clone plumbing errors that are not actual proto
            # breakages — they indicate infrastructure trouble, not an API
            # contract change, and spamming them as 5 separate warnings is
            # worse than useless.
            noise = (
                "does not appear to be a git repository",
                "Could not read from remote repository",
                "Please make sure you have the correct access rights",
                "and the repository exists",
                "could not clone",
            )
            if any(pattern in stderr for pattern in noise):
                return findings

            for line in stderr.split("\n"):
                if line.strip():
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(ctx.proto_dir),
                            rule="V03-BREAKING",
                            message=f"Breaking change: {line.strip()}",
                            fix="Review if this breaking change is intentional. If so, update clients.",
                        )
                    )

        return findings


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
