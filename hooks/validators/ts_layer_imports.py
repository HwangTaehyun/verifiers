"""V64: TS layer imports — dependency-cruiser config presence (detection mode).

TypeScript's import system has no first-class concept of layers. A
``import { x } from '@/data/db'`` from a component bypasses any layered
architecture in a single line. ESLint's ``no-restricted-paths`` is too
weak for serious layer rules; the ecosystem standard is
[dependency-cruiser](https://github.com/sverweij/dependency-cruiser)
(continuously developed since 2016, ★4.4k) — JSON/JS-config based
forbidden-imports rules with proper graph traversal.

V64 (detection mode) verifies that a TypeScript project has either:

  1. A dependency-cruiser config file
     (``.dependency-cruiser.{cjs,js,mjs,json}``) at project root, OR
  2. ``dependency-cruiser`` listed in ``package.json`` devDependencies

If neither is present in a TypeScript-using project, V64 emits a warning
recommending depcruise adoption. Active mode (running depcruise as a
subprocess + parsing forbidden findings) is reserved for Phase 73.

Rules:
  - V64-NO-LAYER-CONFIG  — TS project lacks any layered-import enforcement (warning)
  - V64-DEPCRUISE-NOT-WIRED — depcruise is configured but no `package.json` script invokes it (info)

Reference: [eslint-plugin-boundaries](https://github.com/javierbrea/eslint-plugin-boundaries)
(continuously developed since 2020) — alternative tool we accept as
"layer enforcement present".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

_DEPCRUISE_CONFIG_NAMES = (
    ".dependency-cruiser.cjs",
    ".dependency-cruiser.js",
    ".dependency-cruiser.mjs",
    ".dependency-cruiser.json",
    "dependency-cruiser.config.js",
    "dependency-cruiser.config.cjs",
)

# Alternative tool — eslint-plugin-boundaries entry in eslint config also counts.
_BOUNDARIES_PACKAGE = "eslint-plugin-boundaries"
_DEPCRUISE_PACKAGE = "dependency-cruiser"


class TsLayerImportsValidator(BaseValidator):
    """V64: detect missing TS layer-import enforcement (Phase 72 detection mode)."""

    id = "V64-ts-layer-imports"
    name = "TS Layer Imports"
    # Stop-mode only — runs project-wide. Tier 2 fires on package.json edits
    # since adding/removing depcruise package is what most often changes the
    # outcome.
    file_patterns: list[str] = ["**/package.json"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        return self._scan(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        return self._scan(ctx)

    def _scan(self, ctx: ProjectContext) -> list[Finding]:
        # Only fire on projects that actually use TypeScript.
        if not ctx.file_index.find_by_pattern("*.ts", "*.tsx"):
            return []

        # Check for any depcruise config at project root or web/.
        if self._has_depcruise_config(ctx):
            # Config present — check it's wired into a package.json script.
            if not self._has_depcruise_script(ctx):
                return [
                    Finding(
                        severity="info",
                        file=str(ctx.project_root),
                        rule="V64-DEPCRUISE-NOT-WIRED",
                        message=(
                            "dependency-cruiser config is present but no package.json "
                            "script invokes `depcruise`. CI / pre-commit cannot enforce "
                            "the layer rules without an explicit invocation."
                        ),
                        fix=(
                            'Add a script to package.json, e.g. '
                            '`"deps:check": "depcruise --config .dependency-cruiser.cjs src"`. '
                            "Wire it into CI (`.github/workflows/*.yml`) so layer violations "
                            "block merges."
                        ),
                    )
                ]
            return []

        # No depcruise config. Check devDependencies for the package.
        if self._has_layer_tool_in_deps(ctx):
            # Package installed but no config — most likely partial setup.
            return [
                Finding(
                    severity="info",
                    file=str(ctx.project_root),
                    rule="V64-DEPCRUISE-NOT-WIRED",
                    message=(
                        "dependency-cruiser (or eslint-plugin-boundaries) is installed but "
                        "no config file was found. Add forbidden-import rules so the tool "
                        "actually enforces layers."
                    ),
                    fix=(
                        "Run `bunx depcruise --init` (depcruise) or add a `boundaries` "
                        "section to your eslint config. Then add a `package.json` script "
                        "that runs the check."
                    ),
                )
            ]

        # Nothing — emit the main warning.
        return [
            Finding(
                severity="warning",
                file=str(ctx.project_root),
                rule="V64-NO-LAYER-CONFIG",
                message=(
                    "TypeScript project has no layer-import enforcement. ESLint's "
                    "`no-restricted-paths` is too weak for architectural boundaries; "
                    "without a tool like dependency-cruiser or eslint-plugin-boundaries, "
                    "any PR can silently bypass layered architecture."
                ),
                fix=(
                    "Install dependency-cruiser:\n"
                    "  bun add -D dependency-cruiser\n"
                    "  bunx depcruise --init\n"
                    "Then edit `.dependency-cruiser.cjs` to add `forbidden:` rules — see "
                    "https://github.com/sverweij/dependency-cruiser#example-rules. "
                    "Wire it into a package.json script and CI."
                ),
            )
        ]

    # ── Helpers ──────────────────────────────────────────────────────────

    def _has_depcruise_config(self, ctx: ProjectContext) -> bool:
        # Check project root and web/ subdir (common monorepo layout).
        candidates: list[Path] = []
        for name in _DEPCRUISE_CONFIG_NAMES:
            candidates.append(ctx.project_root / name)
            if ctx.web_dir is not None:
                candidates.append(ctx.web_dir / name)
        return any(c.is_file() for c in candidates)

    def _iter_package_jsons(self, ctx: ProjectContext) -> list[dict]:
        """Return parsed top-level dicts from every package.json in the project."""
        out: list[dict] = []
        for pkg in ctx.file_index.find_by_pattern("package.json"):
            try:
                data = json.loads(pkg.read_text(errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def _has_layer_tool_in_deps(self, ctx: ProjectContext) -> bool:
        for data in self._iter_package_jsons(ctx):
            all_deps: dict[str, str] = {}
            all_deps.update(data.get("dependencies", {}) or {})
            all_deps.update(data.get("devDependencies", {}) or {})
            if _DEPCRUISE_PACKAGE in all_deps or _BOUNDARIES_PACKAGE in all_deps:
                return True
        return False

    def _has_depcruise_script(self, ctx: ProjectContext) -> bool:
        for data in self._iter_package_jsons(ctx):
            scripts = data.get("scripts") or {}
            if not isinstance(scripts, dict):
                continue
            for v in scripts.values():
                if isinstance(v, str) and ("depcruise" in v or "dependency-cruiser" in v):
                    return True
        return False


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = TsLayerImportsValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
