"""V61: Go SQL parameterization — block string-concat / fmt.Sprintf into db.* calls.

A SQL string built via concatenation with user-controlled fragments is the
canonical injection vector ([OWASP Top 10 A03:2021](https://owasp.org/Top10/A03_2021-Injection/),
[CWE-89](https://cwe.mitre.org/data/definitions/89.html)). This rule forbids
passing such strings as the first argument of database query/exec methods.
Use placeholder syntax (``?`` / ``$1`` / ``:name``) with separate arguments.

Rules:
  - V61-SQL-CONCAT  — first arg of db.* call starts with ``"..." +`` (string concat)
  - V61-SQL-SPRINTF — first arg of db.* call is ``fmt.Sprintf(...)`` (formatted SQL)

Escape hatch: same-line comment ``// verifier:sql-safe REASON`` (e.g. when
table name is validated against an allowlist before interpolation —
placeholder syntax can't bind identifiers).

Phase 72 (M1 from end-of-session review). gosec G201 covers the same
class but verifier integration ensures Stop-hook-time enforcement +
Tier 3 cache parity.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Common DB-receiver names. Tunable via project conventions; this set covers
# database/sql, sqlx, jmoiron/sqlx, common repository idioms.
_DB_RECEIVERS = r"(?:db|tx|conn|sqlx|database|stdb|store|repo)"

# DB methods whose first argument is a SQL string. Includes context-flavored
# variants and named-parameter variants from sqlx.
_DB_METHODS = (
    r"(?:Query|QueryRow|Exec|MustExec|NamedQuery|NamedExec|Get|Select|"
    r"Prepare|Preparex|QueryContext|ExecContext|QueryRowContext|"
    r"QueryxContext|GetContext|SelectContext|PrepareContext|"
    r"NamedExecContext|NamedQueryContext)"
)

# `<recv>.<Method>(<ws>"<lit>" +`  — string concat as the SQL builder.
RE_DB_CONCAT = re.compile(
    rf"\b{_DB_RECEIVERS}\.{_DB_METHODS}\s*\(\s*"
    r'"[^"]*"\s*\+',
    re.MULTILINE | re.DOTALL,
)

# `<recv>.<Method>(<ws>fmt.Sprintf(`  — Sprintf builds the SQL.
RE_DB_SPRINTF = re.compile(
    rf"\b{_DB_RECEIVERS}\.{_DB_METHODS}\s*\(\s*"
    r"fmt\.Sprintf\s*\(",
    re.MULTILINE | re.DOTALL,
)

# Same-line escape-hatch marker.
RE_VERIFIER_OK = re.compile(r"//\s*verifier:sql-safe\b")

_SKIP_FILE_SUFFIX = "_test.go"


class GoSqlParameterizationValidator(BaseValidator):
    """V61: enforce SQL placeholder usage in Go db.*/tx.*/sqlx.* calls."""

    id = "V61-go-sql-parameterization"
    name = "Go SQL Parameterization"
    file_patterns: list[str] = ["**/*.go"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        if not path.is_file() or file_path.endswith(_SKIP_FILE_SUFFIX):
            return []
        return self._scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        if not (ctx.server_dir and ctx.server_dir.exists()):
            return []
        server_resolved = ctx.server_dir.resolve()
        findings: list[Finding] = []
        for go_file in ctx.file_index.find_by_pattern("*.go"):
            try:
                go_file.resolve().relative_to(server_resolved)
            except (ValueError, OSError):
                continue
            if str(go_file).endswith(_SKIP_FILE_SUFFIX):
                continue
            findings.extend(self._scan_file(go_file))
        return findings

    def _scan_file(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []
        findings: list[Finding] = []
        for regex, rule_id, message in (
            (
                RE_DB_CONCAT,
                "V61-SQL-CONCAT",
                "SQL built via string concatenation in db/tx call — injection vector (OWASP A03 / CWE-89).",
            ),
            (
                RE_DB_SPRINTF,
                "V61-SQL-SPRINTF",
                "SQL built via fmt.Sprintf in db/tx call — injection vector (OWASP A03 / CWE-89).",
            ),
        ):
            for match in regex.finditer(src):
                # Position of the offending `+` or `(` — used to locate the
                # line that should carry the escape-hatch comment.
                suspicious_pos = match.end() - 1
                line_start = src.rfind("\n", 0, suspicious_pos) + 1
                line_end = src.find("\n", suspicious_pos)
                if line_end == -1:
                    line_end = len(src)
                if RE_VERIFIER_OK.search(src[line_start:line_end]):
                    continue
                line_no = src.count("\n", 0, match.start()) + 1
                findings.append(
                    Finding(
                        severity="error",
                        file=str(file_path),
                        line=line_no,
                        rule=rule_id,
                        message=message,
                        fix=(
                            'Use placeholder syntax: `db.Query("SELECT ... WHERE id = ?", id)` '
                            "or sqlx named params (`:name`). For dynamic identifiers (table / column "
                            "names that placeholders cannot bind), validate against an allowlist before "
                            "interpolation and add `// verifier:sql-safe REASON` to the same line."
                        ),
                    )
                )
        return findings


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoSqlParameterizationValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
