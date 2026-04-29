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

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Secret patterns ──────────────────────────────────────────────────────────

# Phase38 (A3 audit): regex set is now shared with Tier 1 via
# ``lib.secret_regexes``. Pre-Phase38 V08 carried its own copy that
# missed the P2-2 ``${`` / ``${}`` template-placeholder fix; centralizing
# in lib closes the drift surface for good.
from lib.secret_regexes import SECRET_REGEXES  # noqa: E402  (after sys.path mutation)

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

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-file secret / CORS / PHI scan (Tier 2)."""
        sec_cfg = ctx.config.security
        phi_fields = sec_cfg.phi_fields or PHI_FIELDS
        phi_enabled = sec_cfg.phi_check_enabled
        return self._check_single_file(file_path, phi_fields=phi_fields, phi_enabled=phi_enabled)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide gitignore / CORS / secret sweep (Tier 3)."""
        sec_cfg = ctx.config.security
        phi_fields = sec_cfg.phi_fields or PHI_FIELDS
        phi_enabled = sec_cfg.phi_check_enabled
        required_gitignore = sec_cfg.required_gitignore or REQUIRED_GITIGNORE
        return self._check_project_wide(
            ctx,
            phi_fields=phi_fields,
            phi_enabled=phi_enabled,
            required_gitignore=required_gitignore,
        )

    def _check_single_file(
        self,
        file_path: str,
        *,
        phi_fields: list[str] = PHI_FIELDS,
        phi_enabled: bool = True,
    ) -> list[Finding]:
        """Per-file checks (PostToolUse)."""
        findings: list[Finding] = []
        findings.extend(self._check_secrets(file_path))
        findings.extend(self._check_cors(file_path))
        if phi_enabled:
            findings.extend(self._check_phi_logging(file_path, phi_fields=phi_fields))
        return findings

    def _check_project_wide(
        self,
        ctx: ProjectContext,
        *,
        phi_fields: list[str] = PHI_FIELDS,
        phi_enabled: bool = True,
        required_gitignore: list[str] = REQUIRED_GITIGNORE,
    ) -> list[Finding]:
        """Project-wide checks (Stop mode)."""
        findings: list[Finding] = []
        findings.extend(self._check_gitignore(ctx, required_gitignore=required_gitignore))
        findings.extend(self._scan_go_files(ctx, phi_fields=phi_fields, phi_enabled=phi_enabled))
        findings.extend(self._scan_web_files(ctx))
        return findings

    def _scan_go_files(
        self,
        ctx: ProjectContext,
        *,
        phi_fields: list[str] = PHI_FIELDS,
        phi_enabled: bool = True,
    ) -> list[Finding]:
        """Scan Go source files for security issues."""
        findings: list[Finding] = []
        if not (ctx.server_dir and ctx.server_dir.exists()):
            return findings
        for go_file in ctx.server_dir.rglob("*.go"):
            fp = str(go_file)
            if any(exc in fp for exc in EXCLUDE_PATHS):
                continue
            findings.extend(self._check_single_file(fp, phi_fields=phi_fields, phi_enabled=phi_enabled))
        return findings

    def _scan_web_files(self, ctx: ProjectContext) -> list[Finding]:
        """Scan TypeScript/TSX source files for secrets."""
        findings: list[Finding] = []
        if not (ctx.web_dir and ctx.web_dir.exists()):
            return findings
        for ext in ("*.ts", "*.tsx"):
            for ts_file in ctx.web_dir.rglob(ext):
                fp = str(ts_file)
                if any(exc in fp for exc in EXCLUDE_PATHS):
                    continue
                findings.extend(self._check_secrets(fp))
        return findings

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

    def _check_phi_logging(self, file_path: str, *, phi_fields: list[str] = PHI_FIELDS) -> list[Finding]:
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
                for field in phi_fields:
                    # Only flag actual data binding: Str("email", var), not fixed strings like "email and password"
                    # Match: .Str("email", ...) or Sprintf("%s", email) but not fmt.Errorf("email is required")
                    has_binding = re.search(
                        rf'Str\(\s*"{field}"'  # zerolog: .Str("email", val)
                        rf'|"[^"]*%[svd][^"]*"[^)]*{field}'  # Sprintf with variable
                        rf"|{field}\s*[,\)]",  # bare variable reference: email, or email)
                        line,
                        re.IGNORECASE,
                    )
                    if has_binding:
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
                for field in phi_fields:
                    # Only flag variable references, not fixed strings
                    has_binding = re.search(
                        rf"{field}\s*[,\)\}}]"  # variable: console.log(email) or console.log({email})
                        rf"|`[^`]*\$\{{[^}}]*{field}",  # template literal: `${email}`
                        line,
                        re.IGNORECASE,
                    )
                    if has_binding:
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

    def _check_gitignore(
        self, ctx: ProjectContext, *, required_gitignore: list[str] = REQUIRED_GITIGNORE
    ) -> list[Finding]:
        findings: list[Finding] = []
        gitignore = ctx.project_root / ".gitignore"

        if not gitignore.exists():
            findings.append(
                Finding(
                    severity="error",
                    file=str(ctx.project_root),
                    rule="V08-NO-GITIGNORE",
                    message=".gitignore file is missing",
                    fix=(f"Create .gitignore in {ctx.project_root} with at minimum: {', '.join(required_gitignore)}"),
                )
            )
            return findings

        content = gitignore.read_text()
        for pattern in required_gitignore:
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
