"""V71: React hooks plugin enabled — ESLint must enforce hook rules at error level.

[``eslint-plugin-react-hooks``](https://www.npmjs.com/package/eslint-plugin-react-hooks)
(continuously updated, official React team) is the canonical guard
against the two most common React bug classes:

  - ``rules-of-hooks`` — hooks called inside conditionals / loops
    (causes inconsistent hook-order between renders → React state corruption)
  - ``exhaustive-deps`` — useEffect / useMemo / useCallback dependency
    array missing a referenced value (stale-closure bug — UI doesn't
    update when props change)

Both rules silent-passing as ``'warn'`` or ``'off'`` is functionally
equivalent to disabling them — modern dev workflows ignore warnings.
At 1M LOC scale a single missed deps array is a production bug.

V71 verifies that an ESLint config exists for a TypeScript / JavaScript
project AND both rules are set to ``'error'``.

Rules:
  - V71-NO-ESLINT-CONFIG — TS/JS project but no ESLint config (warning,
    soft because V16 already covers strict enforcement)
  - V71-HOOKS-RULE-NOT-ENFORCED — rule found but level is ``warn`` / ``off``
    (error)
  - V71-HOOKS-RULE-MISSING — rule not present at all in the config
    (error)

Detection scope: ``eslint.config.{js,mjs,cjs,ts}``, ``.eslintrc.{js,cjs,json,yml,yaml}``
at project root or ``web/``.

Reference: [React docs "Rules of Hooks"](https://react.dev/reference/rules/rules-of-hooks)
(continuously updated, retrieved 2026-05-03). [Why exhaustive-deps matters](https://react.dev/learn/synchronizing-with-effects).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

_ESLINT_CONFIG_NAMES = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yml",
    ".eslintrc.yaml",
    ".eslintrc",
)

_REQUIRED_RULES = (
    "react-hooks/rules-of-hooks",
    "react-hooks/exhaustive-deps",
)


def _find_eslint_config(ctx: ProjectContext) -> Path | None:
    candidates: list[Path] = []
    for name in _ESLINT_CONFIG_NAMES:
        candidates.append(ctx.project_root / name)
        if ctx.web_dir is not None:
            candidates.append(ctx.web_dir / name)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _rule_level(content: str, rule_name: str) -> str | None:
    """Extract the configured level for ``rule_name`` from ESLint config text.

    Returns ``"error"``, ``"warn"``, ``"off"``, ``"2"``, ``"1"``, ``"0"``,
    or ``None`` if the rule is not present. Numeric levels are normalized
    to the named form by the caller. Handles both bare ("error") and array
    form (["error", { ... }]).
    """
    # rule level patterns (match within quoted-key context):
    pattern = re.compile(
        r'["\']' + re.escape(rule_name) + r'["\']\s*:\s*'
        r'(?:'
        r'\[?\s*'
        r'["\'](?P<named>error|warn|off)["\']'
        r'|(?P<num>[012])'
        r')'
    )
    m = pattern.search(content)
    if not m:
        return None
    if m.group("named"):
        return m.group("named")
    num = m.group("num")
    return {"0": "off", "1": "warn", "2": "error"}.get(num)


class ReactHooksPluginValidator(BaseValidator):
    """V71: enforce eslint-plugin-react-hooks at 'error' level."""

    id = "V71-react-hooks-plugin"
    name = "React Hooks Plugin Enabled"
    file_patterns: list[str] = []  # Stop-only — project-wide config check.

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        # Only fire on projects that use React (.tsx files or react in deps).
        tsx_files = ctx.file_index.find_by_pattern("*.tsx")
        if not tsx_files:
            return []

        config = _find_eslint_config(ctx)
        if config is None:
            return [
                Finding(
                    severity="warning",
                    file=str(ctx.project_root),
                    rule="V71-NO-ESLINT-CONFIG",
                    message=(
                        "React project (.tsx files) has no ESLint config — react-hooks "
                        "plugin cannot be enforced. Hook rule violations and stale-closure "
                        "bugs will pass through silently."
                    ),
                    fix=(
                        "Create eslint.config.js at project root with "
                        "eslint-plugin-react-hooks installed and both `react-hooks/"
                        "rules-of-hooks` and `react-hooks/exhaustive-deps` set to "
                        "`'error'`."
                    ),
                )
            ]

        try:
            content = config.read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for rule_name in _REQUIRED_RULES:
            level = _rule_level(content, rule_name)
            if level is None:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(config),
                        rule="V71-HOOKS-RULE-MISSING",
                        message=(
                            f"ESLint rule `{rule_name}` is not configured. "
                            "React hook discipline depends on this rule firing as an error."
                        ),
                        fix=(
                            f"Add to your ESLint config rules: "
                            f"`'{rule_name}': 'error'`. Ensure `eslint-plugin-react-hooks` "
                            "is installed and registered as a plugin."
                        ),
                    )
                )
            elif level != "error":
                findings.append(
                    Finding(
                        severity="error",
                        file=str(config),
                        rule="V71-HOOKS-RULE-NOT-ENFORCED",
                        message=(
                            f"ESLint rule `{rule_name}` is set to `{level}`. "
                            "Modern dev workflows ignore warnings — the rule is "
                            "effectively disabled."
                        ),
                        fix=(
                            f"Change `'{rule_name}': '{level}'` to "
                            f"`'{rule_name}': 'error'` in {config.name}. "
                            "Stop-the-line on hook violations is the only way they get fixed."
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
    validator = ReactHooksPluginValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
