"""V47: Foreign Key Index Discipline — every FK column must have a covering index.

PostgreSQL does NOT auto-create indexes on FK columns. A DELETE/UPDATE on a
parent table triggers a sequential scan on child tables with no index on the
FK column, causing lock escalation and multi-second outages at scale.

Rules:
  V47-FK-NO-INDEX: FK column has no CREATE INDEX covering it anywhere in the
                   migration history.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Regex patterns ────────────────────────────────────────────────────────────

# Capture current table from CREATE TABLE statement
_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    re.IGNORECASE,
)

# Inline FK: `col_name  type  REFERENCES ref_table(ref_col)`
# This must be used within a CREATE TABLE block context.
_RE_FK_INLINE = re.compile(
    r"(\w+)\s+\w+(?:\([^)]*\))?\s+(?:NOT\s+NULL\s+)?(?:DEFAULT\s+\S+\s+)?REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)",
    re.IGNORECASE,
)

# ALTER TABLE form: ALTER TABLE tbl ADD CONSTRAINT name FOREIGN KEY (col) REFERENCES ref(ref_col)
_RE_FK_ALTER = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+CONSTRAINT\s+\w+\s+FOREIGN\s+KEY\s*\(\s*(\w+)\s*\)\s+REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)",
    re.IGNORECASE,
)

# CREATE [UNIQUE] INDEX [IF NOT EXISTS] name ON table(leftmost_col, ...)
_RE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)\s*\(\s*(\w+)",
    re.IGNORECASE,
)

# PRIMARY KEY (col1, col2, ...) — leftmost col acts as an implicit index
_RE_PK_INLINE = re.compile(
    r"PRIMARY\s+KEY\s*\(\s*(\w+)",
    re.IGNORECASE,
)


class FkIndexDisciplineValidator(BaseValidator):
    """V47: Foreign Key Index Discipline."""

    id = "V47-fk-index-discipline"
    name = "Foreign Key Index Discipline"
    file_patterns: list[str] = ["**/migrations/**/up.sql"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2 (PostToolUse): when an up.sql is edited, run the full project check."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3 (Stop): walk all up.sql files under ctx.project_root."""
        return self._all_checks(ctx)

    # ── Core logic ────────────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        migration_dirs = self._find_migration_dirs(ctx)
        if not migration_dirs:
            return []

        up_sql_files = self._collect_up_sql_files(migration_dirs)
        if not up_sql_files:
            return []

        fks = self._collect_all_fks(up_sql_files)
        indexed_columns = self._collect_all_indexes(up_sql_files)

        return self._check_fk_coverage(fks, indexed_columns)

    def _find_migration_dirs(self, ctx: ProjectContext) -> list[Path]:
        """Find all migration directories under the project root."""
        root = ctx.project_root

        # Prefer server/hasura/migrations/** pattern (Hasura convention)
        candidates: list[Path] = []
        for pattern in (
            "server/hasura/migrations",
            "hasura/migrations",
        ):
            p = root / pattern
            if p.exists() and p.is_dir():
                candidates.append(p)

        # Generic fallback: any migrations/ directory with up.sql files
        if not candidates:
            for mdir in root.rglob("migrations"):
                if mdir.is_dir() and list(mdir.rglob("up.sql")):
                    candidates.append(mdir)

        return candidates

    def _collect_up_sql_files(self, migration_dirs: list[Path]) -> list[Path]:
        """Return up.sql files sorted chronologically (by filename prefix)."""
        files: list[Path] = []
        for mdir in migration_dirs:
            files.extend(mdir.rglob("up.sql"))
        # Sort by the directory name (leading timestamp ensures chronological order)
        return sorted(files, key=lambda p: p.parent.name)

    def _collect_all_fks(self, up_sql_files: list[Path]) -> list[tuple[str, str, str, str, Path]]:
        """Return list of (table, fk_col, ref_table, ref_col, source_path)."""
        fks: list[tuple[str, str, str, str, Path]] = []

        for up_file in up_sql_files:
            try:
                src = up_file.read_text()
            except OSError:
                continue

            # ── ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY form ──────────
            for m in _RE_FK_ALTER.finditer(src):
                table, fk_col, ref_table, ref_col = (
                    m.group(1).lower(),
                    m.group(2).lower(),
                    m.group(3).lower(),
                    m.group(4).lower(),
                )
                fks.append((table, fk_col, ref_table, ref_col, up_file))

            # ── Inline REFERENCES within CREATE TABLE blocks ──────────────
            # Split on CREATE TABLE boundaries so we know which table each
            # column belongs to.
            for ct_match in _RE_CREATE_TABLE.finditer(src):
                table = ct_match.group(1).lower()
                # Extract the block from the CREATE TABLE ( ... ); region
                block_start = ct_match.end()
                # Find the opening paren
                paren_pos = src.find("(", block_start)
                if paren_pos == -1:
                    continue
                block_end = self._find_closing_paren(src, paren_pos)
                block = src[paren_pos:block_end]

                for m in _RE_FK_INLINE.finditer(block):
                    fk_col = m.group(1).lower()
                    ref_table = m.group(2).lower()
                    ref_col = m.group(3).lower()
                    fks.append((table, fk_col, ref_table, ref_col, up_file))

        return fks

    def _collect_all_indexes(self, up_sql_files: list[Path]) -> set[tuple[str, str]]:
        """Return set of (table, leftmost_column) for all indexes in migration history."""
        indexed: set[tuple[str, str]] = set()

        for up_file in up_sql_files:
            try:
                src = up_file.read_text()
            except OSError:
                continue

            # Explicit CREATE INDEX
            for m in _RE_INDEX.finditer(src):
                table = m.group(1).lower()
                col = m.group(2).lower()
                indexed.add((table, col))

            # Implicit index from composite PRIMARY KEY leftmost column
            for ct_match in _RE_CREATE_TABLE.finditer(src):
                table = ct_match.group(1).lower()
                block_start = ct_match.end()
                paren_pos = src.find("(", block_start)
                if paren_pos == -1:
                    continue
                block_end = self._find_closing_paren(src, paren_pos)
                block = src[paren_pos:block_end]

                for pk_match in _RE_PK_INLINE.finditer(block):
                    leftmost_col = pk_match.group(1).lower()
                    indexed.add((table, leftmost_col))

        return indexed

    def _check_fk_coverage(
        self,
        fks: list[tuple[str, str, str, str, Path]],
        indexed_columns: set[tuple[str, str]],
    ) -> list[Finding]:
        """Emit V47-FK-NO-INDEX for every FK column not covered by any index."""
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()  # deduplicate (table, fk_col) pairs

        for table, fk_col, ref_table, ref_col, source_path in fks:
            key = (table, fk_col)
            if key in seen:
                continue
            seen.add(key)

            if key not in indexed_columns:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(source_path),
                        rule="V47-FK-NO-INDEX",
                        message=(
                            f"Foreign key column {table}.{fk_col} "
                            f"(REFERENCES {ref_table}({ref_col})) has no index. "
                            f"DELETE on parent triggers sequential scan."
                        ),
                        fix=(
                            f"Add `CREATE INDEX IF NOT EXISTS idx_{table}_{fk_col} "
                            f"ON {table}({fk_col});` to the same migration that adds "
                            f"the FK constraint."
                        ),
                    )
                )

        return findings

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def _find_closing_paren(src: str, open_pos: int) -> int:
        """Return the position just after the matching closing ')' for the '(' at open_pos."""
        depth = 0
        for i in range(open_pos, len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        return len(src)


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
    validator = FkIndexDisciplineValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
