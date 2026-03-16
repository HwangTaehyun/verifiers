"""V15: Hasura GraphQL Enforcement Validator.

Enforces GraphQL usage over raw SQL when Hasura is present in the project.

PostToolUse checks:
  V15-HASURA-FOUND: Project has Hasura configuration detected
  V15-RAW-SQL-FORBIDDEN: Raw SQL usage forbidden in Hasura projects
  V15-MISSING-GRAPHQL: GraphQL client not used in service
  V15-SQL-IMPORT: database/sql package import detected
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, ValidationResult, read_hook_input, write_hook_output
from lib.project_context import ProjectContext


class HasuraGraphQLEnforcementValidator(BaseValidator):
    """V15: Hasura GraphQL Enforcement Validator."""

    id = "V15-hasura-graphql"
    name = "Hasura GraphQL Enforcement"
    file_patterns: list[str] = [
        "**/*.go",
        "**/docker-compose.yaml",
        "**/docker-compose.yml",
        "**/hasura/**",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # Check if Hasura is present in the project
        hasura_present = self._detect_hasura(ctx)

        if not hasura_present:
            # No Hasura detected, skip validation
            return ValidationResult(
                validator_id=self.id,
                findings=[],
                summary="No Hasura detected in project - GraphQL enforcement skipped"
            )

        findings.append(Finding(
            code="V15-HASURA-FOUND",
            severity="info",
            message="Hasura detected in project - enforcing GraphQL over raw SQL",
            file_path="",
            line_number=1,
            details="Project has Hasura configuration, raw SQL usage is forbidden"
        ))

        # Check for raw SQL usage in Go files
        go_files = [f for f in ctx.changed_files if f.endswith('.go')]
        for go_file in go_files:
            if self._is_exempted_file(go_file):
                continue

            go_findings = self._check_go_file_for_sql(ctx, go_file)
            findings.extend(go_findings)

        return ValidationResult(
            validator_id=self.id,
            findings=findings,
            summary=f"Checked {len(go_files)} Go files for Hasura GraphQL compliance"
        )

    def _detect_hasura(self, ctx: ProjectContext) -> bool:
        """Detect if Hasura is present in the project."""

        # Check for hasura directory
        hasura_dir = ctx.project_root / "hasura"
        if hasura_dir.exists():
            return True

        # Check for hasura directory in server subdirectory
        server_hasura_dir = ctx.project_root / "server" / "hasura"
        if server_hasura_dir.exists():
            return True

        # Check for Hasura in docker-compose files
        docker_compose_files = [
            "docker-compose.yaml",
            "docker-compose.yml",
            "server/docker-compose.yaml",
            "server/docker-compose.yml"
        ]

        for compose_file in docker_compose_files:
            compose_path = ctx.project_root / compose_file
            if compose_path.exists():
                try:
                    content = compose_path.read_text()
                    if "hasura/graphql-engine" in content or "hasura:" in content:
                        return True
                except Exception:
                    continue

        return False

    def _is_exempted_file(self, file_path: str) -> bool:
        """Check if the file is exempted from GraphQL enforcement."""
        exempted_patterns = [
            "**/migrations/**/*.sql",
            "**/*_test.go",
            "**/mocks/**",
            "**/setup/**",
            "**/testdata/**"
        ]

        for pattern in exempted_patterns:
            # Simple pattern matching - could be enhanced with fnmatch
            if "migrations" in file_path and file_path.endswith(".sql"):
                return True
            if file_path.endswith("_test.go"):
                return True
            if "/mocks/" in file_path or "/setup/" in file_path or "/testdata/" in file_path:
                return True

        return False

    def _check_go_file_for_sql(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Check a Go file for raw SQL usage."""
        findings: list[Finding] = []

        try:
            full_path = ctx.project_root / file_path
            content = full_path.read_text()
            lines = content.split('\n')
        except Exception:
            return findings

        # Check for database/sql import
        if re.search(r'^\s*"database/sql"', content, re.MULTILINE):
            findings.append(Finding(
                code="V15-SQL-IMPORT",
                severity="error",
                message="database/sql import forbidden in Hasura projects - use GraphQL instead",
                file_path=file_path,
                line_number=self._find_import_line(lines, "database/sql"),
                details="Replace database/sql with GraphQL client using genqlient and Hasura"
            ))

        # Check for SQL query methods
        sql_patterns = [
            (r'\.Query(?:Row)?Context\s*\(', "raw SQL query"),
            (r'\.ExecContext\s*\(', "raw SQL execution"),
            (r'\.PrepareContext\s*\(', "raw SQL prepared statement"),
            (r'\bSELECT\b.*\bFROM\b', "raw SQL SELECT"),
            (r'\bINSERT\s+INTO\b', "raw SQL INSERT"),
            (r'\bUPDATE\b.*\bSET\b', "raw SQL UPDATE"),
            (r'\bDELETE\s+FROM\b', "raw SQL DELETE"),
        ]

        for i, line in enumerate(lines, 1):
            for pattern, description in sql_patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append(Finding(
                        code="V15-RAW-SQL-FORBIDDEN",
                        severity="error",
                        message=f"Raw SQL usage forbidden: {description}",
                        file_path=file_path,
                        line_number=i,
                        details=f"Line contains: {line.strip()[:100]}... Replace with GraphQL mutation/query"
                    ))

        # Check for missing GraphQL client in service structs
        if "type Service struct" in content and "gqlClient" not in content:
            findings.append(Finding(
                code="V15-MISSING-GRAPHQL",
                severity="warning",
                message="Service struct missing GraphQL client field",
                file_path=file_path,
                line_number=self._find_service_struct_line(lines),
                details="Add 'gqlClient graphql.Client' field to Service struct"
            ))

        return findings

    def _find_import_line(self, lines: list[str], import_name: str) -> int:
        """Find the line number of a specific import."""
        for i, line in enumerate(lines, 1):
            if f'"{import_name}"' in line:
                return i
        return 1

    def _find_service_struct_line(self, lines: list[str]) -> int:
        """Find the line number of Service struct definition."""
        for i, line in enumerate(lines, 1):
            if "type Service struct" in line:
                return i
        return 1


if __name__ == "__main__":
    input_data = read_hook_input()
    validator = HasuraGraphQLEnforcementValidator()
    result = validator.run_validation(input_data)
    write_hook_output(result)
