"""V65: TS any-budget — ratchet on `any` / `@ts-expect-error` / `@ts-ignore` count.

TypeScript's type safety erodes silently at scale: every PR that adds
``: any`` or ``// @ts-expect-error`` makes the codebase weaker, but
since absolute zero is not reachable in legacy projects, no team
enforces it. The result is monotonic decay.

V65 applies the **ratchet pattern**: count `any`-class usages across the
codebase, store as baseline, fail PRs that *increase* the count, and
auto-update baseline when the count drops. The dynamic is explicitly
asymmetric — improvement is free, regression is blocked.

Patterns counted (per file, summed):

  - ``: any``      — type annotation
  - ``as any``     — type assertion / cast
  - ``<any>``      — generic instantiation (also matches ``<any,``, ``<any |``)
  - ``@ts-expect-error`` / ``@ts-ignore`` — type-check escape hatches

State file: ``<project_root>/.verifiers/ts-any-baseline.json``. This is
intended to be **committed to git** so the budget is a team policy, not
per-developer. Distinct from ``.verifiers/state/`` (developer-local
caches, gitignored).

Rules:
  - V65-ANY-BUDGET-EXCEEDED — current count > baseline.count (error)
  - (Implicit) baseline auto-created on first run; auto-decreases on
    drops. No finding when count is stable or decreasing.

Excluded: generated code (``.gen.``, ``__generated__``, ``dist/``,
``build/``, ``.next/``) — ratchet should not punish the team for
generator output volume.

Reference: [type-coverage](https://github.com/plantain-00/type-coverage)
(continuously developed since 2017) — ratio measurement tool. [Bazel
ratchet pattern](https://bazel.build/concepts/build-files) (continuously
updated). [Betterer](https://betterer.dev/) (continuously updated,
retrieved 2026-05-03) — generic ratchet framework.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Each pattern is independent — counts stack so a line with both
# ``as any`` and ``: any`` contributes 2.
#
# The colon-form deliberately omits a lookbehind so ``let x: any`` matches
# (``x`` is a word char before ``:``); ternaries like ``cond ? a : any``
# would also match here, but in TypeScript that's vanishingly rare.
ANY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r":\s*any\b"),
    re.compile(r"\bas\s+any\b"),
    re.compile(r"<any\b"),
    re.compile(r"@ts-(?:expect-error|ignore)\b"),
)

# Files whose `any` doesn't count toward the team budget. Generators,
# build outputs, dependency-installed code.
_EXCLUDE_HINTS: tuple[str, ...] = (
    ".gen.",
    "__generated__",
    "/dist/",
    "/build/",
    "/.next/",
    "/node_modules/",
)

# Baseline file lives at .verifiers/ (alongside config.yaml), NOT
# .verifiers/state/ — the latter is developer-local cache. The
# baseline is a committed team policy.
_BASELINE_RELATIVE = Path(".verifiers") / "ts-any-baseline.json"

# Cap history length so the file doesn't grow unbounded over years of
# ratchet-downs.
_HISTORY_MAX = 20


class TsAnyBudgetValidator(BaseValidator):
    """V65: ratchet on `any` count (Stop-mode only)."""

    id = "V65-ts-any-budget"
    name = "TS any-budget (ratchet)"
    # Stop-only: counting is project-wide; per-edit Tier 2 doesn't make
    # sense (one Edit can't tell whether the absolute count went up).
    file_patterns: list[str] = []

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        ts_files = [
            f
            for f in ctx.file_index.find_by_pattern("*.ts", "*.tsx")
            if not any(h in str(f) for h in _EXCLUDE_HINTS)
        ]
        if not ts_files:
            return []  # Non-TS project — nothing to ratchet.

        current = self._count(ts_files)
        baseline_path = ctx.project_root / _BASELINE_RELATIVE
        baseline = self._load_baseline(baseline_path)

        if baseline is None:
            # First run — establish baseline silently. Subsequent runs
            # gate on this number.
            self._write_baseline(baseline_path, current, history=[current])
            return []

        prev = baseline.get("count")
        if not isinstance(prev, int):
            # Corrupted baseline — re-establish.
            self._write_baseline(baseline_path, current, history=[current])
            return []

        if current > prev:
            delta = current - prev
            return [
                Finding(
                    severity="error",
                    file=str(ctx.project_root),
                    rule="V65-ANY-BUDGET-EXCEEDED",
                    message=(
                        f"TS any-budget exceeded: count {prev} → {current} (+{delta}). "
                        "Each `: any` / `as any` / `<any>` / `@ts-expect-error` / "
                        "`@ts-ignore` counts as 1."
                    ),
                    fix=(
                        f"Reduce any-class usage back to ≤ {prev}. To find the new "
                        "occurrences:\n"
                        '  rg -n "(:\\s*any\\b|\\bas\\s+any\\b|<any\\b|@ts-(expect-error|ignore))" \\\n'
                        "    --type-add 'ts:*.{ts,tsx}' --type ts | head\n"
                        f"If the increase is intentional (justified architectural "
                        f"decision), reset the baseline:\n"
                        f"  echo '{{\"count\": {current}, \"set_at\": \"$(date -u +%FT%TZ)\", "
                        f'"history": [{prev}, {current}]}}\' > {baseline_path}\n'
                        "and explain in the commit message."
                    ),
                )
            ]

        if current < prev:
            # Ratchet down — auto-update baseline so the new lower
            # number becomes the floor.
            hist_raw = baseline.get("history")
            history: list[int] = hist_raw if isinstance(hist_raw, list) else []
            history = [*history, current][-_HISTORY_MAX:]
            self._write_baseline(baseline_path, current, history=history)

        # current == prev → silent pass.
        return []

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _count(ts_files: list[Path]) -> int:
        total = 0
        for f in ts_files:
            try:
                src = f.read_text(errors="replace")
            except OSError:
                continue
            for pat in ANY_PATTERNS:
                total += len(pat.findall(src))
        return total

    @staticmethod
    def _load_baseline(path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _write_baseline(path: Path, count: int, *, history: list[int]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "count": count,
            "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "history": history,
        }
        try:
            path.write_text(json.dumps(data, indent=2) + "\n")
        except OSError:
            pass  # State write failure shouldn't break the hook.


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = TsAnyBudgetValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
