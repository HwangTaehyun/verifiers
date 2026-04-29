"""V16: Linter Config Guard — enforce that linter configurations include essential rules.

Rather than re-implementing what linters already do, this validator ensures that
projects have proper linter configurations in place.

Checks:
  V16-NO-LINTER-CONFIG: Project has source files but no linter config (error)
  V16-MISSING-ERROR-RULES: Essential error-handling linter rules disabled (warning)
  V16-MISSING-UNUSED-RULES: Essential unused-code linter rules disabled (warning)
  V16-MISSING-SECURITY-RULES: Essential security linter rules disabled (warning)
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

from hooks.validators.base import (
    BaseValidator,
    Finding,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.json_logger import log_exception
from lib.project_context import ProjectContext

# ── Language detection helpers ────────────────────────────────────────────────


def _has_go_files(root: Path) -> bool:
    """Check if project has Go source files."""
    for p in root.rglob("*.go"):
        # Skip vendor and node_modules
        parts = p.parts
        if "vendor" not in parts and "node_modules" not in parts:
            return True
    return False


def _has_python_files(root: Path) -> bool:
    """Check if project has Python source files."""
    for p in root.rglob("*.py"):
        parts = p.parts
        if "node_modules" not in parts and ".venv" not in parts and "venv" not in parts:
            return True
    return False


def _has_ts_files(root: Path) -> bool:
    """Check if project has TypeScript/JavaScript source files."""
    for ext in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        for p in root.rglob(ext):
            parts = p.parts
            if "node_modules" not in parts and "dist" not in parts and "build" not in parts:
                return True
    return False


# ── Config file detection ─────────────────────────────────────────────────────


def _find_golangci_config(root: Path) -> Path | None:
    """Find golangci-lint config file."""
    candidates = [
        root / ".golangci.yml",
        root / ".golangci.yaml",
        root / ".golangci.toml",
        root / ".golangci.json",
    ]
    # Also check server/ subdirectory
    server_dir = root / "server"
    if server_dir.is_dir():
        candidates.extend(
            [
                server_dir / ".golangci.yml",
                server_dir / ".golangci.yaml",
            ]
        )
    for c in candidates:
        if c.exists():
            return c
    return None


def _find_ruff_config(root: Path) -> tuple[Path | None, str]:
    """Find ruff config — either standalone or in pyproject.toml.

    Returns (config_path, config_type) where config_type is "ruff.toml",
    "pyproject.toml", or "".
    """
    # Standalone ruff.toml
    for name in ("ruff.toml", ".ruff.toml"):
        candidate = root / name
        if candidate.exists():
            return candidate, "ruff.toml"

    # pyproject.toml with [tool.ruff]
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(errors="replace")
        if "[tool.ruff]" in content:
            return pyproject, "pyproject.toml"

    return None, ""


def _find_eslint_config(root: Path) -> Path | None:
    """Find ESLint config file."""
    candidates = [
        root / "eslint.config.js",
        root / "eslint.config.mjs",
        root / "eslint.config.cjs",
        root / "eslint.config.ts",
        root / ".eslintrc.js",
        root / ".eslintrc.cjs",
        root / ".eslintrc.json",
        root / ".eslintrc.yml",
        root / ".eslintrc.yaml",
        root / ".eslintrc",
    ]
    # Also check web/ subdirectory
    web_dir = root / "web"
    if web_dir.is_dir():
        for name in (
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.ts",
            ".eslintrc.js",
            ".eslintrc.json",
        ):
            candidates.append(web_dir / name)
    for c in candidates:
        if c.exists():
            return c
    return None


# ── Config content analysis ───────────────────────────────────────────────────


def _check_golangci_rules(config_path: Path) -> list[Finding]:
    """Check golangci-lint config for essential rules."""
    findings: list[Finding] = []
    try:
        content = config_path.read_text(errors="replace")
        config = yaml.safe_load(content) or {}
    except Exception as exc:
        log_exception(
            source="V16-linter-config-guard/_check_golangci_rules",
            error=exc,
            context={"config_path": str(config_path)},
        )
        return findings

    linters = config.get("linters", {})
    enabled = linters.get("enable", []) or []
    disabled = linters.get("disable", []) or []

    # errcheck: unchecked errors
    if "errcheck" in disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-ERROR-RULES",
                message="errcheck linter is disabled in golangci-lint config",
                fix=(
                    f"Remove 'errcheck' from linters.disable in {config_path}. "
                    "Unchecked errors can cause silent failures in production."
                ),
            )
        )

    # unused: unused code detection
    if "unused" in disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-UNUSED-RULES",
                message="unused linter is disabled in golangci-lint config",
                fix=(
                    f"Remove 'unused' from linters.disable in {config_path}. "
                    "Unused code adds confusion and maintenance burden."
                ),
            )
        )

    # gosec: security checks
    if "gosec" in disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-SECURITY-RULES",
                message="gosec linter is disabled in golangci-lint config",
                fix=(
                    f"Remove 'gosec' from linters.disable in {config_path}. "
                    "Security checks prevent common vulnerabilities."
                ),
            )
        )

    # If enable-all is not set and explicit enable list exists but misses critical linters
    enable_all = linters.get("enable-all", False)
    if not enable_all and enabled:
        if "errcheck" not in enabled and "errcheck" not in disabled:
            # errcheck is default-enabled in golangci-lint, so only warn if explicitly listed
            pass  # Default behavior is fine

    return findings


def _check_ruff_rules(config_path: Path, _config_type: str) -> list[Finding]:
    """Check ruff config for essential rules."""
    findings: list[Finding] = []
    try:
        content = config_path.read_text(errors="replace")
    except Exception as exc:
        log_exception(
            source="V16-linter-config-guard/_check_ruff_rules",
            error=exc,
            context={"config_path": str(config_path)},
        )
        return findings

    # Parse ignore/select sections
    # Look for patterns like: ignore = ["E722", "F401"]
    # or per-file-ignores, or extend-ignore

    ignore_patterns = re.findall(r"ignore\s*=\s*\[([^\]]*)\]", content)
    all_ignored: list[str] = []
    for match in ignore_patterns:
        all_ignored.extend(re.findall(r'"([^"]+)"', match))

    select_patterns = re.findall(r"select\s*=\s*\[([^\]]*)\]", content)
    all_selected: list[str] = []
    for match in select_patterns:
        all_selected.extend(re.findall(r'"([^"]+)"', match))

    # E722: bare except
    if "E722" in all_ignored:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-ERROR-RULES",
                message="E722 (bare except) is ignored in ruff config",
                fix=(
                    f"Remove 'E722' from ignore list in {config_path}. "
                    "Bare except clauses hide bugs. Use specific exception types."
                ),
            )
        )

    # F401: unused imports
    if "F401" in all_ignored:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-UNUSED-RULES",
                message="F401 (unused import) is ignored in ruff config",
                fix=(
                    f"Remove 'F401' from ignore list in {config_path}. "
                    "Unused imports create confusion and potential import cycles."
                ),
            )
        )

    # S (Bandit security rules): check if security rules are selected
    # Only check if select is explicitly set (not default)
    if all_selected:
        has_security = any(rule.startswith("S") for rule in all_selected)
        has_all = "ALL" in all_selected
        if not has_security and not has_all:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(config_path),
                    rule="V16-MISSING-SECURITY-RULES",
                    message="Bandit security rules (S prefix) not selected in ruff config",
                    fix=(
                        f"Add 'S' to select list in {config_path}. "
                        "Security rules help catch common vulnerabilities like SQL injection and hardcoded secrets."
                    ),
                )
            )

    return findings


def _check_eslint_rules(config_path: Path) -> list[Finding]:
    """Check ESLint config for essential rules."""
    findings: list[Finding] = []
    try:
        content = config_path.read_text(errors="replace")
    except Exception as exc:
        log_exception(
            source="V16-linter-config-guard/_check_eslint_rules",
            error=exc,
            context={"config_path": str(config_path)},
        )
        return findings

    # Check for disabled rules
    # Pattern: "no-empty": "off" or "no-empty": 0
    no_empty_disabled = bool(re.search(r'["\']?no-empty["\']?\s*:\s*(["\']off["\']|0)', content))
    if no_empty_disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-ERROR-RULES",
                message="'no-empty' rule is disabled in ESLint config",
                fix=(f"Enable 'no-empty' rule in {config_path}. Empty blocks (especially catch) hide errors."),
            )
        )

    # no-unused-vars disabled
    unused_disabled = bool(
        re.search(
            r'["\']?(?:@typescript-eslint/)?no-unused-vars["\']?\s*:\s*(["\']off["\']|0)',
            content,
        )
    )
    if unused_disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-UNUSED-RULES",
                message="'no-unused-vars' rule is disabled in ESLint config",
                fix=(
                    f"Enable 'no-unused-vars' rule in {config_path}. "
                    "Unused variables indicate dead code or incomplete implementation."
                ),
            )
        )

    # Check for no-eval disabled (security)
    no_eval_disabled = bool(re.search(r'["\']?no-eval["\']?\s*:\s*(["\']off["\']|0)', content))
    if no_eval_disabled:
        findings.append(
            Finding(
                severity="warning",
                file=str(config_path),
                rule="V16-MISSING-SECURITY-RULES",
                message="'no-eval' rule is disabled in ESLint config",
                fix=(
                    f"Enable 'no-eval' rule in {config_path}. "
                    "eval() is a security risk that allows arbitrary code execution."
                ),
            )
        )

    return findings


# ── Validator class ───────────────────────────────────────────────────────────


class LinterConfigGuardValidator(BaseValidator):
    """V16: Linter Config Guard — enforce proper linter configurations."""

    id = "V16-linter-config-guard"
    name = "Linter Config Guard"
    file_patterns: list[str] = []  # Stop mode only — runs on whole project

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: Stop-only project-wide linter config sweep (Tier 3)."""
        findings: list[Finding] = []
        root = ctx.project_root

        # ── Go ──
        if _has_go_files(root):
            config = _find_golangci_config(root)
            if config is None:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(root),
                        rule="V16-NO-LINTER-CONFIG",
                        message="Go project has no golangci-lint config (.golangci.yml)",
                        fix=("Create a .golangci.yml file with at least errcheck, unused, and gosec linters enabled."),
                    )
                )
            else:
                findings.extend(_check_golangci_rules(config))

        # ── Python ──
        if _has_python_files(root):
            config, config_type = _find_ruff_config(root)
            if config is None:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(root),
                        rule="V16-NO-LINTER-CONFIG",
                        message="Python project has no ruff config (ruff.toml or [tool.ruff] in pyproject.toml)",
                        fix=(
                            "Add a [tool.ruff] section to pyproject.toml or create ruff.toml "
                            "with E722, F401 enabled and S (security) rules selected."
                        ),
                    )
                )
            else:
                findings.extend(_check_ruff_rules(config, config_type))

        # ── TypeScript ──
        if _has_ts_files(root):
            config = _find_eslint_config(root)
            if config is None:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(root),
                        rule="V16-NO-LINTER-CONFIG",
                        message="TypeScript project has no ESLint config",
                        fix=(
                            "Create an eslint.config.js (or .eslintrc.json) with "
                            "no-empty, no-unused-vars, and no-eval rules enabled."
                        ),
                    )
                )
            else:
                findings.extend(_check_eslint_rules(config))

        return findings


# ── Standalone execution ──────────────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({"decision": "approve"})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = LinterConfigGuardValidator()

    result = validator.run(ctx, file_path=None, mode="stop")
    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
