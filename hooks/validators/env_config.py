"""V01: Environment & config validator — 3-Layer Separation, .env.example completeness.

Checks:
  V01-SECRET-IN-CONFIG: Secret values hardcoded in config/*.yaml (3-Layer violation)
  V01-ENV-MISSING: Variables referenced in docker-compose/Go but not in .env.example
  V01-CONFIG-KEY-MISSING: Config key exists in some variants but missing in others
  V01-VITE-ENV-MISSING: import.meta.env.VITE_* used in code but not defined in web/env/
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

from hooks.validators.base import BaseValidator, Finding, ValidationResult, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Secret patterns for config files ─────────────────────────────────────────

SECRET_PATTERNS = [
    r'password:\s*["\']?(?![\$\{])\S{8,}',  # password: hardcoded123
    r'secret:\s*["\']?(?![\$\{])\S{8,}',  # secret: mysecret
    r'api_key:\s*["\']?(?![\$\{])\S{8,}',  # api_key: sk-xxx
    r'token:\s*["\']?(?![\$\{])\S{8,}',  # token: ghp_xxx
    r':\s*["\']?sk-[a-zA-Z0-9]{20,}',  # OpenAI key
    r':\s*["\']?ghp_[a-zA-Z0-9]{20,}',  # GitHub token
    r':\s*["\']?AKIA[A-Z0-9]{16}',  # AWS access key
]


class EnvConfigValidator(BaseValidator):
    """V01: Environment & Configuration Validator."""

    id = "V01-env-config"
    name = "Env/Config Validator"
    file_patterns: list[str] = [
        "**/.env*",
        "**/config/*.yaml",
        "**/config/*.yml",
        "**/*.go",
        "**/*.ts",
        "**/*.tsx",
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
    ]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # Always run these checks (they're fast, file-system only)
        findings.extend(self._check_secret_in_config(ctx))
        findings.extend(self._check_env_example_completeness(ctx))
        findings.extend(self._check_config_consistency(ctx))

        # Frontend VITE_* sync only if web dir exists
        if ctx.web_dir and ctx.web_dir.exists():
            findings.extend(self._check_vite_env_sync(ctx))

        return ValidationResult(validator_id=self.id, findings=findings)

    # ── Check 1: 3-Layer boundary violation ──────────────────────────────

    def _check_secret_in_config(self, ctx: ProjectContext) -> list[Finding]:
        """config/*.yaml must not contain hardcoded secrets (3-Layer Separation)."""
        findings: list[Finding] = []

        if not ctx.server_dir:
            return findings

        config_dir = ctx.server_dir / "config"
        if not config_dir.exists():
            return findings

        for config_file in config_dir.glob("*.yaml"):
            try:
                content = config_file.read_text()
            except OSError:
                continue

            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue

                for pattern in SECRET_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        # Skip if value is an env var reference: ${...}
                        value_part = line.split(":", 1)[-1].strip().strip("\"'")
                        if value_part.startswith("${") or value_part.startswith("$"):
                            continue
                        key = line.split(":")[0].strip()
                        env_var = "APP_" + key.upper()
                        findings.append(
                            Finding(
                                severity="error",
                                file=str(config_file),
                                rule="V01-SECRET-IN-CONFIG",
                                message=f"Secret value in config: '{stripped[:60]}...'",
                                fix=(
                                    f"1. Replace value with '${{{env_var}}}' in {config_file}\n"
                                    f"2. Add '{env_var}=<actual-value>' to .env\n"
                                    f"3. Add '{env_var}=<placeholder>' to .env.example"
                                ),
                                line=i,
                            )
                        )
                        break  # One finding per line

        return findings

    # ── Check 2: .env.example completeness ───────────────────────────────

    def _check_env_example_completeness(self, ctx: ProjectContext) -> list[Finding]:
        """Variables referenced in docker-compose/Go must be in .env.example."""
        env_example = ctx.project_root / ".env.example"
        example_vars = self._parse_env_example_vars(env_example)

        findings: list[Finding] = []
        findings.extend(self._check_compose_env_refs(ctx, example_vars, env_example))
        findings.extend(self._check_go_env_refs(ctx, example_vars, env_example))
        return findings

    def _parse_env_example_vars(self, env_example: Path) -> set[str]:
        """Parse variable names from .env.example file."""
        example_vars: set[str] = set()
        if env_example.exists():
            for line in env_example.read_text().splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    example_vars.add(line.split("=", 1)[0].strip())
        return example_vars

    def _check_compose_env_refs(
        self, ctx: ProjectContext, example_vars: set[str], env_example: Path
    ) -> list[Finding]:
        """Check docker-compose references: ${VAR} without default."""
        findings: list[Finding] = []
        for compose_file in ctx.project_root.glob("**/docker-compose*.yaml"):
            try:
                content = compose_file.read_text()
            except OSError:
                continue
            for match in re.finditer(r"\$\{(\w+)\}", content):
                var = match.group(1)
                start = match.start()
                full_ctx = content[start : start + len(match.group(0)) + 20]
                if ":-" in full_ctx[: full_ctx.find("}") + 1] if "}" in full_ctx else "":
                    continue
                if var not in example_vars:
                    line_num = content[:start].count("\n") + 1
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(compose_file),
                            rule="V01-ENV-MISSING",
                            message=f"${{{var}}} referenced in docker-compose but not in .env.example",
                            fix=f"Add '{var}=<placeholder>' to {env_example}",
                            line=line_num,
                        )
                    )
        return findings

    def _check_go_env_refs(
        self, ctx: ProjectContext, example_vars: set[str], env_example: Path
    ) -> list[Finding]:
        """Check Go code: os.Getenv("APP_*")."""
        findings: list[Finding] = []
        if not (ctx.server_dir and ctx.server_dir.exists()):
            return findings
        for go_file in ctx.server_dir.rglob("*.go"):
            if "_test.go" in str(go_file):
                continue
            try:
                content = go_file.read_text()
            except OSError:
                continue
            for match in re.finditer(r'os\.Getenv\("(\w+)"\)', content):
                var = match.group(1)
                if var.startswith("APP_") and var not in example_vars:
                    line_num = content[: match.start()].count("\n") + 1
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(go_file),
                            rule="V01-ENV-MISSING",
                            message=f'os.Getenv("{var}") used but not in .env.example',
                            fix=f"Add '{var}=<placeholder>' to {env_example}",
                            line=line_num,
                        )
                    )
        return findings

    # ── Check 3: Config file key consistency ─────────────────────────────

    def _check_config_consistency(self, ctx: ProjectContext) -> list[Finding]:
        """Config variants (docker/local/production) should have consistent keys."""
        findings: list[Finding] = []

        if not ctx.server_dir or not ctx.project_name:
            return findings

        config_dir = ctx.server_dir / "config"
        if not config_dir.exists():
            return findings

        all_keys: dict[str, set[str]] = {}

        for config_file in config_dir.glob(f"{ctx.project_name}.*.yaml"):
            try:
                data = yaml.safe_load(config_file.read_text()) or {}
            except (yaml.YAMLError, OSError):
                continue
            all_keys[config_file.name] = set(self._flatten_keys(data))

        if len(all_keys) < 2:
            return findings  # Need at least 2 variants to compare

        union_keys = set().union(*all_keys.values())

        for filename, keys in all_keys.items():
            missing = union_keys - keys
            for key in sorted(missing):
                findings.append(
                    Finding(
                        severity="info",
                        file=str(config_dir / filename),
                        rule="V01-CONFIG-KEY-MISSING",
                        message=f"Key '{key}' exists in other config variants but missing here",
                        fix=f"Add '{key}' to {config_dir / filename} (check other config variants for reference)",
                    )
                )

        return findings

    @staticmethod
    def _flatten_keys(data: dict, prefix: str = "") -> list[str]:
        """Flatten nested dict keys into dot-separated paths."""
        keys: list[str] = []
        for k, v in data.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys.extend(EnvConfigValidator._flatten_keys(v, full_key))
            else:
                keys.append(full_key)
        return keys

    # ── Check 4: Frontend VITE_* env sync ────────────────────────────────

    def _check_vite_env_sync(self, ctx: ProjectContext) -> list[Finding]:
        """import.meta.env.VITE_* used in code must be defined in web/env/ files."""
        findings: list[Finding] = []

        if not ctx.web_dir:
            return findings

        src_dir = ctx.web_dir / "src"
        if not src_dir.exists():
            return findings

        # Collect VITE_* references from source code
        vite_vars: set[str] = set()
        for ts_file in src_dir.rglob("*.ts*"):
            try:
                content = ts_file.read_text()
            except OSError:
                continue
            for match in re.finditer(r"import\.meta\.env\.(\w+)", content):
                vite_vars.add(match.group(1))

        # Collect defined variables from web/env/ directory
        defined_vars: set[str] = set()
        env_dir = ctx.web_dir / "env"
        if env_dir.exists():
            for env_file in env_dir.glob(".env*"):
                try:
                    for line in env_file.read_text().splitlines():
                        if "=" in line and not line.strip().startswith("#"):
                            defined_vars.add(line.split("=", 1)[0].strip())
                except OSError:
                    continue

        for var in sorted(vite_vars - defined_vars):
            if var.startswith("VITE_"):
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(src_dir),
                        rule="V01-VITE-ENV-MISSING",
                        message=f"import.meta.env.{var} used in code but not defined in web/env/",
                        fix=f"Add '{var}=<value>' to {ctx.web_dir}/env/.env.local",
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
    validator = EnvConfigValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
