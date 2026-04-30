"""V38: golangci-lint Strictness Config validator.

Enforces that .golangci.yaml/.golangci.yml files enable key strictness
linters and configure them properly:

  - V38-NO-WRAPCHECK      — `wrapcheck` not in linters.enable
  - V38-WEAK-NOLINTLINT   — `nolintlint` present but require-specific or
                             require-explanation not set to true
  - V38-NO-GOFUMPT        — `gofumpt` not in linters.enable (warning only)

Handles both v1 and v2 golangci-lint config schemas.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402


class GolangciStrictnessValidator(BaseValidator):
    """V38: golangci-lint Strictness Config."""

    id = "V38-golangci-strictness"
    name = "golangci-lint Strictness Config"
    file_patterns: list[str] = [
        "**/.golangci.yaml",
        "**/.golangci.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: the config file was just edited — run checks on it."""
        path = Path(file_path)
        if not path.is_file():
            return []
        return list(self._all_checks(path))

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: walk all .golangci.{yaml,yml} files in the project."""
        root = Path(ctx.project_root)
        findings: list[Finding] = []
        for pattern in ("**/.golangci.yaml", "**/.golangci.yml"):
            for config_file in root.rglob(pattern.lstrip("**/")):
                if config_file.is_file():
                    findings.extend(self._all_checks(config_file))
        return findings

    # ── Internals ──────────────────────────────────────────────────────

    def _all_checks(self, file_path: Path) -> Iterator[Finding]:
        try:
            config = yaml.safe_load(file_path.read_text(errors="replace"))
        except Exception:
            return  # Invalid YAML — skip silently

        if not isinstance(config, dict):
            return

        yield from self._check_wrapcheck(config, file_path)
        yield from self._check_nolintlint(config, file_path)
        yield from self._check_gofumpt(config, file_path)

    def _enabled_linters(self, config: dict) -> list[str]:
        """Extract the list of enabled linters, handling v1 and v2 schemas."""
        linters_section = config.get("linters", {})
        if not isinstance(linters_section, dict):
            return []
        enable = linters_section.get("enable", [])
        return list(enable) if isinstance(enable, list) else []

    def _nolintlint_settings(self, config: dict) -> dict:
        """Extract nolintlint settings, handling v1 and v2 schemas."""
        # v1: linters-settings.nolintlint
        v1 = config.get("linters-settings", {})
        if isinstance(v1, dict) and "nolintlint" in v1:
            result = v1["nolintlint"]
            return result if isinstance(result, dict) else {}

        # v2: settings.nolintlint
        v2 = config.get("settings", {})
        if isinstance(v2, dict) and "nolintlint" in v2:
            result = v2["nolintlint"]
            return result if isinstance(result, dict) else {}

        return {}

    def _check_wrapcheck(self, config: dict, file_path: Path) -> Iterator[Finding]:
        linters = self._enabled_linters(config)
        if "wrapcheck" not in linters:
            yield Finding(
                severity="error",
                file=str(file_path),
                rule="V38-NO-WRAPCHECK",
                message=(
                    "`wrapcheck` linter not enabled. Bare `return err` without %w wrapping "
                    "(V34's concern) won't be caught at lint time."
                ),
                fix=("Add `wrapcheck` to `linters.enable` (v1) or `linters: { enable: [..., wrapcheck] }` (v2)."),
            )

    def _check_nolintlint(self, config: dict, file_path: Path) -> Iterator[Finding]:
        linters = self._enabled_linters(config)
        if "nolintlint" not in linters:
            return  # nolintlint itself is off — skip settings check

        nolintlint_cfg = self._nolintlint_settings(config)
        require_specific = nolintlint_cfg.get("require-specific", False)
        require_explanation = nolintlint_cfg.get("require-explanation", False)

        if not (require_specific and require_explanation):
            missing_parts: list[str] = []
            if not require_specific:
                missing_parts.append("require-specific: true")
            if not require_explanation:
                missing_parts.append("require-explanation: true")
            missing = " and ".join(missing_parts) + " not set"

            yield Finding(
                severity="error",
                file=str(file_path),
                rule="V38-WEAK-NOLINTLINT",
                message=(
                    f"`nolintlint` is weak: {missing}. "
                    "Bare `//nolint` without justification breaks suppression audit trail."
                ),
                fix=("Add to linters-settings.nolintlint:\n  require-specific: true\n  require-explanation: true"),
            )

    def _check_gofumpt(self, config: dict, file_path: Path) -> Iterator[Finding]:
        linters = self._enabled_linters(config)
        if "gofumpt" not in linters:
            yield Finding(
                severity="warning",
                file=str(file_path),
                rule="V38-NO-GOFUMPT",
                message=("`gofumpt` not enabled. Stricter formatting reduces diff noise across PRs."),
                fix="Add `gofumpt` to `linters.enable`.",
            )


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GolangciStrictnessValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
