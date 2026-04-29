# Contributing to verifiers

This guide is for people adding or modifying validators. The audience is
assumed comfortable with Python 3.11+ and the project structure described
in [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md).

## Repo setup

```bash
git clone https://github.com/HwangTaehyun/verifiers.git
cd verifiers
just setup           # uv sync — installs runtime + dev deps into .venv
just test            # 919 tests at the time of writing
just lint            # ruff check
just format          # ruff format (auto-fix)
```

Both `pytest` and `ruff` (check + format) run in CI on every PR
(`.github/workflows/ci.yml`). Match those locally before pushing.

## How to add a new validator

### 1. Pick a V-ID

V-IDs (`V01`, `V02`, …) are the primary identifier. The mapping
**V-ID prefix ↔ module is 1:1** — enforced at runtime by
`_assert_registry_invariants` in `hooks/validators/__init__.py`. The
catalog (`docs/VERIFIERS-CATALOG.md`) and `hooks/run_single.py` rely on
this guarantee.

| Range used | What it means |
|------------|---------------|
| V01–V19 | Existing validators (see catalog) |
| V17 | Reserved (UI verifier, not implemented) |
| V21+ | Available — pick the next free integer |

If your validator clearly fits an existing concern (e.g. another flavor
of test-runner), prefer adding rules under the existing V-ID's prefix
(`V11-*`) over claiming a new V-ID.

### 2. Skeleton

Create `hooks/validators/<your_module>.py`:

```python
"""V21: <one-line summary>.

Checks:
  V21-RULE-A: ...
  V21-RULE-B: ...
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]   # only if you actually need yaml
# ///

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext


class YourValidator(BaseValidator):
    id = "V21-your-rule"
    name = "Your Rule"
    file_patterns: list[str] = ["**/*.py"]   # fnmatch globs

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []
        if file_path:
            findings.extend(self._check_file(file_path))
        elif mode == "stop":
            findings.extend(self._scan_project(ctx))
        return ValidationResult(validator_id=self.id, findings=findings)

    def _check_file(self, file_path: str) -> list[Finding]:
        # Per-file analysis; runs on every Edit/Write match.
        ...

    def _scan_project(self, ctx: ProjectContext) -> list[Finding]:
        # Stop-mode project scan; runs once at turn end.
        ...
```

### 3. Mode dispatch — the contract

`validate()` is called with two distinct shapes. **Honor both.**

| `mode` | `file_path` | When |
|--------|-------------|------|
| `"post_tool_use"` | concrete path | Tier 2 router after Edit/Write/MultiEdit |
| `"stop"` | `None` | Tier 3 stop_validator at turn end |

PostToolUse should be **fast** (single file, ideally <1s). Stop mode
can be slower (≤120s budget for ALL validators combined — be considerate).
Many validators do per-file work in PostToolUse and project-wide scans
in stop mode, but it's fine to skip stop mode entirely (e.g. V09 lets
V06's full `go test ./...` cover its territory).

### 4. Register in the registry

Edit `hooks/validators/__init__.py`:

```python
from .your_module import YourValidator

validators: list[BaseValidator] = [
    ...,
    YourValidator(),  # V21 — short rationale
]
```

Adding `_assert_registry_invariants` will yell loudly at import time if
your V-ID collides with an existing one.

### 5. (Optional) Add to `run_single.py`

If you want users to invoke your validator with `just verify-one your-rule`,
add an entry to `NAME_MAP` in `hooks/run_single.py`. Keep `NAME_MAP` and
the registry in lockstep — the comment block at the top spells out the
convention.

### 6. Tests are not optional

Every validator ships with a `tests/test_<your_module>.py` containing at
minimum:

- `should_run` matching for the file patterns you declared
- Each rule's positive case (input that triggers a finding)
- Each rule's negative case (similar input that should NOT trigger)
- External commands mocked via `unittest.mock.patch("subprocess.run", …)`
  — never invoke the real `pytest` / `tsc` / `golangci-lint` from a test

V09 / V10 / V11 / V19's test files (`tests/test_*_test_runner.py`,
`tests/test_py_quality.py`) are good references for subprocess-mocking
patterns.

#### Style: Classical (Chicago) school is mandatory

This project follows the [`test-classical`](skills/test-classical/SKILL.md)
skill (sourced from [Atipico1/ai-testing-rules](https://github.com/Atipico1/ai-testing-rules)).
Read the skill once before writing your first test. The short version:

- Mock at the **system boundary** only — `subprocess.run`, real HTTP, real
  filesystem APIs at the OS level. **Never** mock `BaseValidator.run`,
  internal helpers, or your own dataclasses.
- Use real `tmp_path` directories with real files — see
  `tests/test_router_cache.py`, `tests/test_config_loader.py`,
  `tests/test_exclusion.py` for the canonical pattern.
- For collaborator stand-ins, prefer module-level dataclass test doubles
  (`_PassValidator`, `_CrashValidator` in `tests/test_parallel_runner.py`)
  over `mock.patch` of internal classes.
- Assert on **return values and observable state**, not on
  `assert_called_with(...)` / `mock.call_count`.
- Test names describe behavior:
  `test_returns_cached_result_when_fetched_within_ttl`, NOT
  `test_findUnique_called_once`.

### 7. Document in the catalog

Add a section to `docs/VERIFIERS-CATALOG.md` §3 covering:

- file_patterns
- Each rule (per-file regex / external command / parsed format)
- post_tool_use vs stop mode behavior
- Why this check exists

### 8. Honor project config

If your validator has tunable thresholds, follow V14's pattern: thread
a typed dataclass (e.g. add a new field to `ComplexityThresholds` or
introduce your own in `lib/config_loader.py`) through the analysis
chain. Do **not** read globals at runtime — keep functions pure and
testable.

`ctx.config.exclude.paths` is already applied at the router level
(`lib/exclusion.py`), so your `_scan_project` doesn't need to filter
those paths separately. If your validator has its own internal skip list
(e.g. `vendor/`, `node_modules/`), keep it focused on language-specific
concerns rather than user-overridable directories.

## PR checklist

Before opening a PR, run:

- [ ] `just test` — full suite green
- [ ] `just lint` — ruff check passes
- [ ] `uv run ruff format --check .` — format clean
- [ ] `uv run python scripts/sync_inline_deps.py --check` — PEP 723
      inline deps in sync with pyproject.toml
- [ ] CHANGELOG.md updated under `## [Unreleased]`
- [ ] If the change adds/removes a V-ID or rule string: catalog and
      README updated to match
- [ ] Conventional Commits message — V12 commit-discipline expects it
      (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`)

## Project conventions

### Naming

- Validator class: `<Subject>Validator` (e.g. `ComplexityGuardValidator`)
- Validator id: `V<NN>-<kebab-case>` (e.g. `V14-complexity-guard`)
- Rule id: `V<NN>-<UPPER-KEBAB-CASE>` (e.g. `V14-HIGH-COMPLEXITY`)
- Test file: `tests/test_<your_module>.py` (mirrors source filename)

### Severity

- `error`: blocks the user's turn (Tier 3) or the Edit/Write (Tier 2)
- `warning`: surfaces in additionalContext but doesn't block
- `info`: informational only — non-blocking note

Reserve `error` for things that genuinely break the build / leak
secrets / drop data. Style preferences belong in `warning`.

### Failure isolation

A crashing validator must never block the user's turn. The router and
stop_validator wrap each validator in `try/except` and route the error
to `lib.json_logger.log_exception`. Don't catch and silently `pass`
inside your validator either — let exceptions propagate; the host hook
will log them.

### Don't pollute the project root

Runtime state goes under `<cwd>/.verifiers/state/` (already in
`.gitignore`). Don't drop dotfiles in the project root.

## Where to get help

- Hook protocol details: see [`memory/hook-protocol.md`](.claude/memory/hook-protocol.md)
- Existing validators in detail: [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md)
- Architecture overview: [`README.md`](README.md)
