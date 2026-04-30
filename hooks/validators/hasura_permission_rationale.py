"""V48: Hasura permission rationale validator.

Checks:
  V48-HASURA-SELECT-ONLY-UNDOCUMENTED: A table YAML has select_permissions
    for one or more roles but no insert/update/delete permissions AND no
    documented intent marker (repo-level or per-table comment).
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml  # noqa: E402

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# ── Intent markers ────────────────────────────────────────────────────────────

# Repo-level markers (searched in docs/ tree, AGENTS.md, CLAUDE.md)
_REPO_INTENT_MARKERS: tuple[str, ...] = ("hasura-read-only", "mutations-via-grpc")

# Per-table YAML comment marker (case-insensitive substring match in raw text)
_TABLE_COMMENT_MARKER = "mutations: intentionally absent"

# Permission keys that indicate mutation access
_MUTATION_PERMISSION_KEYS: tuple[str, ...] = (
    "insert_permissions",
    "update_permissions",
    "delete_permissions",
)


class HasuraPermissionRationaleValidator(BaseValidator):
    """V48: Hasura Permission Rationale Validator."""

    id = "V48-hasura-permission-rationale"
    name = "Hasura Permission Rationale"
    file_patterns: list[str] = [
        "**/hasura/metadata/databases/*/tables/*.yaml",
        "**/hasura/metadata/databases/*/tables/*.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: scan the edited table YAML."""
        path = Path(file_path)
        if not path.exists():
            return []
        repo_intent = self._check_repo_level_intent(ctx)
        return self._check_table(path, repo_intent)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: walk all table YAMLs under hasura metadata."""
        if not ctx.hasura_dir or not ctx.hasura_dir.exists():
            return []

        tables_root = ctx.hasura_dir / "metadata" / "databases"
        if not tables_root.exists():
            return []

        # Cache repo-level intent once for the whole project scan
        repo_intent = self._check_repo_level_intent(ctx)

        findings: list[Finding] = []
        for table_yaml in sorted(tables_root.rglob("*.yaml")):
            findings.extend(self._check_table(table_yaml, repo_intent))
        for table_yaml in sorted(tables_root.rglob("*.yml")):
            findings.extend(self._check_table(table_yaml, repo_intent))
        return findings

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_repo_level_intent(self, ctx: ProjectContext) -> bool:
        """Return True if any repo-level doc contains an intent marker."""
        root = ctx.project_root

        # Check AGENTS.md and CLAUDE.md at project root
        for doc_name in ("AGENTS.md", "CLAUDE.md"):
            doc_file = root / doc_name
            if doc_file.exists():
                text = doc_file.read_text(errors="replace").lower()
                if any(marker in text for marker in _REPO_INTENT_MARKERS):
                    return True

        # Check docs/ directory (any .md file, one level deep glob)
        docs_dir = root / "docs"
        if docs_dir.is_dir():
            for doc in docs_dir.glob("*.md"):
                try:
                    text = doc.read_text(errors="replace").lower()
                except OSError:
                    continue
                if any(marker in text for marker in _REPO_INTENT_MARKERS):
                    return True

        return False

    def _check_table(self, table_yaml: Path, repo_intent: bool) -> list[Finding]:
        """Check a single table YAML for undocumented select-only pattern."""
        try:
            raw_text = table_yaml.read_text(errors="replace")
            data = yaml.safe_load(raw_text)
        except (OSError, yaml.YAMLError):
            return []

        # YAML may parse to None (empty file) or non-dict
        if not isinstance(data, dict):
            return []

        # Table with no select_permissions at all — not "select-only", exempt
        select_perms = data.get("select_permissions")
        if not select_perms:
            return []

        # select_permissions exists but is an empty list — treat as absent
        if isinstance(select_perms, list) and len(select_perms) == 0:
            return []

        # Table has mutation permissions — not select-only, no finding
        if any(k in data for k in _MUTATION_PERMISSION_KEYS):
            return []

        # Select-only table detected. Check for intent documentation.
        if repo_intent:
            return []

        # Per-table YAML comment marker (case-insensitive)
        if _TABLE_COMMENT_MARKER in raw_text.lower():
            return []

        return [
            Finding(
                severity="info",
                file=str(table_yaml),
                rule="V48-HASURA-SELECT-ONLY-UNDOCUMENTED",
                message=(
                    "This Hasura table grants only `select_permissions`. If this is intentional (e.g. "
                    "writes go through gRPC/Connect-RPC, not Hasura), the architectural invariant should "
                    "be documented so future contributors don't accidentally add a mutation permission."
                ),
                fix=(
                    "Either:\n"
                    "  (a) Add comment to the YAML: `# mutations: intentionally absent — writes via gRPC`\n"
                    "  (b) Or document at repo level: AGENTS.md / docs/ should mention "
                    "`hasura-read-only` or `mutations-via-grpc` as the architectural decision"
                ),
            )
        ]


# ── Standalone execution ──────────────────────────────────────────────────────


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
    validator = HasuraPermissionRationaleValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
