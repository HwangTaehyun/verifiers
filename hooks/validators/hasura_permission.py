"""V24: Hasura permission audit.

Hasura's value depends on the metadata-level permission system being
**actually defined** for every table. Three failure patterns this catches:

  1. **A table with no permissions at all.** Anonymous reads / writes
     fall back to the admin secret only. If the GraphQL API is exposed
     publicly, that table is admin-or-nothing — usually wrong.

  2. **`columns: '*'` (wildcard).** Allowlist-by-default is the safer
     pattern; ``'*'`` lets every column out the door including any
     PII / secret column added later. V24 forces the explicit list.

  3. **Empty `filter: {}` on select / update / delete.** The empty
     row-filter is "no row-level restriction at all" — every row is
     visible to that role. For non-admin roles this is the silent
     equivalent of disabling RLS.

V24 fires only when ``hasura/metadata/databases/`` is detected.
Projects without Hasura get zero findings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml  # noqa: E402

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Permission keys recognized in Hasura table metadata
_PERM_KEYS: tuple[str, ...] = (
    "select_permissions",
    "insert_permissions",
    "update_permissions",
    "delete_permissions",
)

# Roles for which the empty filter is accepted as legitimate (admin
# bypasses RLS by design, and a project may legitimately give a
# specific elevated role full table access).
_FILTER_EXEMPT_ROLES: frozenset[str] = frozenset({"admin", "internal-service"})


def _hasura_metadata_dirs(ctx: ProjectContext) -> list[Path]:
    """Return every ``databases/<name>`` dir Hasura uses for table metadata."""
    if ctx.hasura_dir is None:
        return []
    base = ctx.hasura_dir / "metadata" / "databases"
    if not base.is_dir():
        return []
    return [d for d in base.iterdir() if d.is_dir()]


def _table_yaml_files(metadata_dir: Path) -> list[Path]:
    """All ``tables/<schema>_<table>.yaml`` files for one Hasura source."""
    tables_dir = metadata_dir / "tables"
    if not tables_dir.is_dir():
        return []
    return sorted(tables_dir.rglob("*.yaml")) + sorted(tables_dir.rglob("*.yml"))


def _table_label(data: dict) -> str:
    """Render a table label for a finding message: ``schema.name``."""
    table = data.get("table") or {}
    if isinstance(table, dict):
        return f"{table.get('schema', 'public')}.{table.get('name', '?')}"
    return "?"


class HasuraPermissionAuditValidator(BaseValidator):
    """V24: Hasura permission audit."""

    id = "V24-hasura-permission"
    name = "Hasura Permission Audit"
    file_patterns: list[str] = [
        "**/hasura/metadata/**/tables/**/*.yaml",
        "**/hasura/metadata/**/tables/**/*.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: scan only the table-yaml just edited."""
        if not _hasura_metadata_dirs(ctx):
            return []
        path = Path(file_path)
        if "/tables/" not in str(path) and "\\tables\\" not in str(path):
            return []
        return self._scan_table_yaml(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: walk every metadata source's tables/."""
        findings: list[Finding] = []
        for metadata_dir in _hasura_metadata_dirs(ctx):
            for table_file in _table_yaml_files(metadata_dir):
                findings.extend(self._scan_table_yaml(table_file))
        return findings

    # ── Per-file analyzer ────────────────────────────────────────────

    def _scan_table_yaml(self, table_file: Path) -> list[Finding]:
        try:
            data = yaml.safe_load(table_file.read_text(errors="replace")) or {}
        except (yaml.YAMLError, OSError):
            return []
        if not isinstance(data, dict):
            return []

        findings: list[Finding] = []
        label = _table_label(data)

        # (1) No permissions block at all
        has_any_perm = any(
            isinstance(data.get(key), list) and data[key]  # non-empty list
            for key in _PERM_KEYS
        )
        if not has_any_perm:
            findings.append(
                Finding(
                    severity="error",
                    file=str(table_file),
                    rule="V24-NO-PERMISSIONS",
                    message=(
                        f"Table '{label}' has no select/insert/update/delete "
                        "permissions defined. Only admin role can access it."
                    ),
                    fix=(
                        "Add at least a select_permissions block for the roles "
                        "that should read this table (anonymous / user / admin). "
                        "Use explicit columns and a row-filter."
                    ),
                )
            )
            return findings  # No point checking individual perms

        # (2) + (3) per-permission scan
        for perm_key in _PERM_KEYS:
            perms = data.get(perm_key)
            if not isinstance(perms, list):
                continue
            for entry in perms:
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                if not isinstance(role, str):
                    continue
                permission = entry.get("permission")
                if not isinstance(permission, dict):
                    continue

                # Wildcard columns allow-the-world
                cols = permission.get("columns")
                if cols == "*" or cols == ["*"]:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(table_file),
                            rule="V24-WILDCARD-COLUMNS",
                            message=(
                                f"Table '{label}' / role '{role}' / {perm_key.replace('_', ' ')} "
                                "uses wildcard columns ('*'). Any future column "
                                "(including secrets) is auto-exposed."
                            ),
                            fix=("Replace with an explicit allowlist: columns: ['id', 'name', 'created_at']."),
                        )
                    )

                # Empty row filter — applies to select/update/delete only
                # (insert has no filter; check the column set instead).
                if perm_key in ("select_permissions", "update_permissions", "delete_permissions"):
                    flt = permission.get("filter")
                    if isinstance(flt, dict) and not flt and role not in _FILTER_EXEMPT_ROLES:
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(table_file),
                                rule="V24-EMPTY-FILTER",
                                message=(
                                    f"Table '{label}' / role '{role}' / "
                                    f"{perm_key.replace('_', ' ')} has an empty row "
                                    "filter ({}). Every row is visible to '" + role + "'."
                                ),
                                fix=(
                                    "Add a row-level predicate, e.g. "
                                    "filter: { tenant_id: { _eq: X-Hasura-Tenant-Id } }. "
                                    "If unrestricted access is intended, only the "
                                    "'admin' role should have an empty filter."
                                ),
                            )
                        )
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = HasuraPermissionAuditValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
