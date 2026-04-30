"""V02: GraphQL/genqlient validator — stale detection, omitempty, function refs.

Checks:
  V02-YAML-MISSING-FIELD: Required fields missing in genqlient.yaml
  V02-STALE-GEN: GraphQL files changed but genqlient.go not regenerated
  V02-OMITEMPTY: *uuid.UUID field missing omitempty json tag (null UUID bug)
  V02-MISSING-FUNCTION: Repository calls genqlient function that doesn't exist
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.codegen_staleness import is_codegen_stale
from lib.hash_cache import HashCache
from lib.project_context import ProjectContext

# ── genqlient.yaml required fields ───────────────────────────────────────────

REQUIRED_FIELDS = ["schema", "operations", "generated", "package"]


class GraphqlGenValidator(BaseValidator):
    """V02: GraphQL/genqlient Validator."""

    id = "V02-graphql-gen"
    name = "GraphQL/genqlient Validator"
    file_patterns: list[str] = [
        "**/graph/queries/**/*.graphql",
        "**/graph/schemas/*.graphql",
        "**/genqlient.yaml",
        "**/gqlclient/genqlient.go",
        "**/gqlclient/*.go",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.hash_cache = HashCache()

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-file GraphQL gen check (Tier 2).

        Function-reference check is heavier — only runs when genqlient.go
        is the file just edited.
        """
        if not ctx.graph_dir or not ctx.graph_dir.exists():
            return []
        findings = self._common_checks(ctx)
        if "genqlient" in file_path:
            findings.extend(self._check_function_references(ctx))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide GraphQL gen sweep (Tier 3)."""
        if not ctx.graph_dir or not ctx.graph_dir.exists():
            return []
        findings = self._common_checks(ctx)
        findings.extend(self._check_function_references(ctx))
        return findings

    def _common_checks(self, ctx: ProjectContext) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._check_genqlient_yaml(ctx))
        findings.extend(self._check_omitempty(ctx))
        findings.extend(self._check_stale_generated(ctx))
        return findings

    # ── Check 1: genqlient.yaml required fields ─────────────────────────

    def _check_genqlient_yaml(self, ctx: ProjectContext) -> list[Finding]:
        """genqlient.yaml must have all required fields."""
        findings: list[Finding] = []

        yaml_file = ctx.graph_dir / "genqlient.yaml"
        if not yaml_file.exists():
            findings.append(
                Finding(
                    severity="error",
                    file=str(ctx.graph_dir),
                    rule="V02-YAML-MISSING-FIELD",
                    message="genqlient.yaml not found in graph directory",
                    fix=f"Create {yaml_file} with fields: {', '.join(REQUIRED_FIELDS)}",
                )
            )
            return findings

        try:
            data = yaml.safe_load(yaml_file.read_text()) or {}
        except (yaml.YAMLError, OSError):
            return findings

        for field in REQUIRED_FIELDS:
            if field not in data:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(yaml_file),
                        rule="V02-YAML-MISSING-FIELD",
                        message=f"Required field '{field}' missing in genqlient.yaml",
                        fix=f"Add '{field}:' to {yaml_file}. Check other projects for reference.",
                    )
                )

        return findings

    # ── Check 2: Hash-based stale detection ──────────────────────────────

    def _check_stale_generated(self, ctx: ProjectContext) -> list[Finding]:
        """Detect if GraphQL input files changed but genqlient.go was not regenerated.

        Phase51: hash + mtime two-step algorithm extracted to
        ``lib.codegen_staleness.is_codegen_stale`` and shared with V03.
        """
        findings: list[Finding] = []

        # Collect input files
        input_files: list[Path] = []
        queries_dir = ctx.graph_dir / "queries"
        if queries_dir.exists():
            input_files.extend(queries_dir.rglob("*.graphql"))

        schemas_dir = ctx.graph_dir / "schemas"
        if schemas_dir.exists():
            input_files.extend(schemas_dir.glob("*.graphql"))

        yaml_file = ctx.graph_dir / "genqlient.yaml"
        if yaml_file.exists():
            input_files.append(yaml_file)

        generated = ctx.graph_dir / "gqlclient" / "genqlient.go"

        if is_codegen_stale(
            cache=self.hash_cache,
            category="graphql",
            project=ctx.project_name or "unknown",
            input_files=input_files,
            generated_files=[generated],
        ):
            server_dir = ctx.server_dir or ctx.project_root
            findings.append(
                Finding(
                    severity="error",
                    file=str(generated),
                    rule="V02-STALE-GEN",
                    message="GraphQL files changed but genqlient.go not regenerated",
                    fix=f"Run 'cd {server_dir} && make generate_go' to regenerate",
                )
            )

        return findings

    # ── Check 3: omitempty tag on *uuid.UUID fields ──────────────────────

    def _check_omitempty(self, ctx: ProjectContext) -> list[Finding]:
        """*uuid.UUID fields must have json:",omitempty" to prevent null UUID bug."""
        findings: list[Finding] = []

        generated = ctx.graph_dir / "gqlclient" / "genqlient.go"
        if not generated.exists():
            return findings

        try:
            content = generated.read_text()
        except OSError:
            return findings

        for i, line in enumerate(content.split("\n"), 1):
            if "*uuid.UUID" in line and 'json:"' in line:
                if ",omitempty" not in line:
                    field_match = re.search(r'(\w+)\s+\*uuid\.UUID.*json:"(\w+)"', line)
                    if field_match:
                        field_name = field_match.group(1)
                        json_tag = field_match.group(2)
                        server_dir = ctx.server_dir or ctx.project_root
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(generated),
                                rule="V02-OMITEMPTY",
                                message=(f"Field '{field_name}' (*uuid.UUID) missing omitempty in json tag"),
                                fix=(
                                    f'Change json:"{json_tag}" to json:"{json_tag},omitempty" at line {i}.\n'
                                    f"Or run: cd {server_dir} && make generate_go "
                                    f"(includes omitempty post-processing)"
                                ),
                                line=i,
                            )
                        )

        return findings

    # ── Check 4: Function reference validation ───────────────────────────

    def _check_function_references(self, ctx: ProjectContext) -> list[Finding]:
        """Repository calls to gqlclient.* must exist in generated code."""
        findings: list[Finding] = []

        generated = ctx.graph_dir / "gqlclient" / "genqlient.go"
        if not generated.exists():
            return findings

        try:
            gen_content = generated.read_text()
        except OSError:
            return findings

        # Extract exported functions from generated code
        gen_functions = set(re.findall(r"^func (\w+)\(", gen_content, re.MULTILINE))

        if not ctx.server_dir:
            return findings

        # Check repository files for gqlclient.Function() calls
        for repo_file in ctx.server_dir.rglob("**/repository*.go"):
            try:
                content = repo_file.read_text()
            except OSError:
                continue

            for match in re.finditer(r"gqlclient\.(\w+)\(", content):
                func_name = match.group(1)
                if func_name not in gen_functions:
                    line_num = content[: match.start()].count("\n") + 1
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(repo_file),
                            rule="V02-MISSING-FUNCTION",
                            message=f"gqlclient.{func_name}() called but not in generated code",
                            fix=(
                                f"Either: 1) Create the GraphQL query in graph/queries/ and "
                                f"run 'make generate_go', or "
                                f"2) Remove the call at {repo_file}:{line_num}"
                            ),
                            line=line_num,
                        )
                    )

        return findings


# ── Standalone execution (for skill frontmatter hooks) ───────────────────────


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
    validator = GraphqlGenValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
