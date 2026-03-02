"""V08: Security validator — hardcoded secrets, CORS, PHI logging, .gitignore.

Checks:
  V08-HARDCODED-SECRET: Regex-based secret pattern detection in source code
  V08-CORS-WILDCARD: Dangerous CORS wildcard configuration
  V08-PHI-LOGGING: Protected Health Information in log statements (HIPAA)
  V08-NO-GITIGNORE / V08-GITIGNORE-MISSING: Security-sensitive files not in .gitignore
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, ValidationResult, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Secret patterns ──────────────────────────────────────────────────────────

SECRET_REGEXES: list[tuple[str, str]] = [
    (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Anthropic API Key"),
    (r"sk_live_[a-zA-Z0-9]{20,}", "Stripe Live Key"),
    (r"xoxb-[a-zA-Z0-9\-]+", "Slack Bot Token"),
    (r'password\s*[:=]\s*["\'][^"\'$\{]{8,}["\']', "Hardcoded password"),
]

EXCLUDE_PATHS = [
    ".env.example",
    "_test.go",
    "test_",
    "fixtures/",
    "testdata/",
    "mock",
    "__tests__",
    ".gen.",
    "generated",
    "vendor/",
    "node_modules/",
]

# ── CORS patterns ────────────────────────────────────────────────────────────

CORS_PATTERNS: list[tuple[str, str]] = [
    (r"AllowAllOrigins:\s*true", "CORS allows all origins — security risk"),
    (r"Access-Control-Allow-Origin.*\*", "CORS wildcard origin — security risk"),
    (r'cors\.Config\{[^}]*AllowOrigins:\s*\[\s*"\*"\s*\]', "CORS wildcard in config"),
]

# ── PHI fields (HIPAA) ──────────────────────────────────────────────────────

PHI_FIELDS = [
    "patient_name",
    "patient_id",
    "ssn",
    "date_of_birth",
    "medical_record",
    "diagnosis",
    "phone_number",
    "email",
]

# ── .gitignore required entries ──────────────────────────────────────────────

REQUIRED_GITIGNORE = [".env", "*.pem", "*.key", ".env.local", "*.p12"]


class SecurityValidator(BaseValidator):
    """V08: Security — secrets, CORS, PHI, .gitignore."""

    id = "V08-security"
    name = "Security Validator"
    file_patterns: list[str] = []  # Runs on ALL files

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        if file_path:
            # Per-file checks (PostToolUse)
            findings.extend(self._check_secrets(file_path))
            findings.extend(self._check_cors(file_path))
            findings.extend(self._check_phi_logging(file_path))
        else:
            # Project-wide checks (Stop mode)
            findings.extend(self._check_gitignore(ctx))

            # Also scan recently modified files if available
            if ctx.server_dir and ctx.server_dir.exists():
                for go_file in ctx.server_dir.rglob("*.go"):
                    if any(exc in str(go_file) for exc in EXCLUDE_PATHS):
                        continue
                    findings.extend(self._check_secrets(str(go_file)))
                    findings.extend(self._check_cors(str(go_file)))
                    findings.extend(self._check_phi_logging(str(go_file)))

            if ctx.web_dir and ctx.web_dir.exists():
                for ts_file in ctx.web_dir.rglob("*.ts"):
                    if any(exc in str(ts_file) for exc in EXCLUDE_PATHS):
                        continue
                    findings.extend(self._check_secrets(str(ts_file)))
                for tsx_file in ctx.web_dir.rglob("*.tsx"):
                    if any(exc in str(tsx_file) for exc in EXCLUDE_PATHS):
                        continue
                    findings.extend(self._check_secrets(str(tsx_file)))

        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Check: Hardcoded secrets ─────────────────────────────────────────

    def _check_secrets(self, file_path: str) -> list[Finding]:
        if any(exc in file_path for exc in EXCLUDE_PATHS):
            return []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            # Skip comments
            if stripped.startswith(("//", "#", "*", "/*", "<!--")):
                continue
            for pattern, desc in SECRET_REGEXES:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            severity="error",
                            file=file_path,
                            rule="V08-HARDCODED-SECRET",
                            message=f"Possible {desc} detected",
                            fix=(
                                f"Remove the hardcoded secret at {file_path}:{i}. "
                                f"Move to .env and reference via os.Getenv() or ${{VAR}}"
                            ),
                            line=i,
                        )
                    )
                    break  # One finding per line
        return findings

    # ── Check: Dangerous CORS ────────────────────────────────────────────

    def _check_cors(self, file_path: str) -> list[Finding]:
        # Only check Go/YAML/JS files that might have CORS config
        if not file_path.endswith((".go", ".yaml", ".yml", ".ts", ".tsx", ".js")):
            return []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            for pattern, desc in CORS_PATTERNS:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            severity="error",
                            file=file_path,
                            rule="V08-CORS-WILDCARD",
                            message=desc,
                            fix=(
                                f"Replace wildcard CORS at {file_path}:{i} with specific "
                                f"allowed origins from config (APP_CORS_ORIGINS)"
                            ),
                            line=i,
                        )
                    )
        return findings

    # ── Check: PHI logging (HIPAA) ───────────────────────────────────────

    def _check_phi_logging(self, file_path: str) -> list[Finding]:
        if not file_path.endswith((".go", ".ts", ".tsx", ".js")):
            return []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            # Check Go log patterns
            if re.search(r"log\.(Info|Debug|Warn|Print|Printf|Error)\(", line):
                for field in PHI_FIELDS:
                    if field in line.lower():
                        findings.append(
                            Finding(
                                severity="error",
                                file=file_path,
                                rule="V08-PHI-LOGGING",
                                message=f"PHI field '{field}' may be logged — HIPAA violation risk",
                                fix=(
                                    f"Remove '{field}' from log statement at {file_path}:{i}. "
                                    f"Use log.Debug with field masking or remove sensitive data."
                                ),
                                line=i,
                            )
                        )
                        break  # One finding per line
            # Check JS/TS console patterns
            elif re.search(r"console\.(log|debug|info|warn|error)\(", line):
                for field in PHI_FIELDS:
                    if field in line.lower():
                        findings.append(
                            Finding(
                                severity="error",
                                file=file_path,
                                rule="V08-PHI-LOGGING",
                                message=f"PHI field '{field}' may be logged — HIPAA violation risk",
                                fix=(
                                    f"Remove '{field}' from log statement at {file_path}:{i}. "
                                    f"Mask or remove sensitive data before logging."
                                ),
                                line=i,
                            )
                        )
                        break

        return findings

    # ── Check: .gitignore completeness ───────────────────────────────────

    def _check_gitignore(self, ctx: ProjectContext) -> list[Finding]:
        findings: list[Finding] = []
        gitignore = ctx.project_root / ".gitignore"

        if not gitignore.exists():
            findings.append(
                Finding(
                    severity="error",
                    file=str(ctx.project_root),
                    rule="V08-NO-GITIGNORE",
                    message=".gitignore file is missing",
                    fix=(f"Create .gitignore in {ctx.project_root} with at minimum: {', '.join(REQUIRED_GITIGNORE)}"),
                )
            )
            return findings

        content = gitignore.read_text()
        for pattern in REQUIRED_GITIGNORE:
            if pattern not in content:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(gitignore),
                        rule="V08-GITIGNORE-MISSING",
                        message=f"'{pattern}' not in .gitignore — sensitive files may be committed",
                        fix=f"Add '{pattern}' to {gitignore}",
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
    validator = SecurityValidator()
    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
