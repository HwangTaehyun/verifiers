# V19 — py-quality

> **Owner**: `hooks/validators/py_quality.py`
> **Tier**: 2 (PostToolUse) per-file `ruff check` + `ruff format --check`. 3 (Stop) project-wide `ruff check .`.
> **File patterns**: `**/*.py`, `**/pyproject.toml`, `**/ruff.toml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V19-RUFF-{CODE}` | error | `ruff check --output-format text --no-fix <file>` returned a finding. The Ruff rule code is preserved in the rule id (e.g., `V19-RUFF-E501`, `V19-RUFF-F401`, `V19-RUFF-S105`). |
| `V19-RUFF-FORMAT` | warning | `ruff format --check <file>` exited non-zero (file is not Ruff-formatted). |
| `V19-RUFF-ALL` | warning | Project-wide `ruff check .` returned findings (Stop only); same per-rule code preservation, capped at 20 findings + a `V19-RUFF-SUMMARY` if more. |

## Why this verifier exists

V19 is a thin but essential wrapper around Ruff:

- **Per-file Tier 2 enforcement** so style/lint regressions are caught in the same Edit that introduced them, not on commit.
- **Per-rule code preservation** so `validators.disabled: ["V19-RUFF-D107"]` lets users mute a single noisy rule (`D107` = missing docstring) without disabling all of V19.
- **Stop-mode summary cap** so a project with 200 lint warnings doesn't drown the additionalContext field.

Phase 28 split the pytest path out of V19 into V21, so V19 is now ruff-only (lint + format + project-wide). The split lets the Tier 3 parallel runner schedule lint and tests as independent units, which was the perf win documented in the Phase 28 commit.

## Design rationale

- **Ruff over flake8 / pylint / black / isort.** Ruff is one tool that subsumes all four with 10-100x speed. The single-binary single-config story means V19's surface is small.
- **`--no-fix`.** Ruff has `--fix` which auto-applies safe fixes. V19 never invokes that — the rule is "tell the user about the violation; let them or the agent fix it". Auto-fix on a hook would conflate detection and remediation in a way that hides the change.
- **`--output-format text`.** Ruff supports JSON output but text format gives one violation per line (`file:line:col: code message`) which is trivial to parse. JSON would be more robust to format changes; trade-off is the small parse cost.
- **20-finding cap on Stop.** Surfacing all 200 findings would overwhelm Claude. The cap shows the first 20 plus a `V19-RUFF-SUMMARY` saying "X more — run `ruff check .` to see all". Same UX as `eslint`'s default.
- **`--venv` ruff preferred.** V19 prefers `<py_root>/.venv/bin/ruff` over the system `ruff` so the project's pinned Ruff version is used. Avoids the "different teammate, different ruff version, different lint result" problem.

## How it checks (implementation)

Lives in `hooks/validators/py_quality.py`. Phase 28 removed the pytest helpers; only ruff lives here now.

### `validate_file(ctx, file_path)` — Tier 2

```python
def validate_file(self, ctx, file_path):
    py_root = self._find_python_root(ctx)
    if not py_root or not file_path.endswith(".py"):
        return []
    findings: list[Finding] = []
    findings.extend(self._check_ruff_lint(py_root, file_path))
    findings.extend(self._check_ruff_format(py_root, file_path))
    return findings
```

### `_find_python_root(ctx)`

Walks up from `ctx.cwd` looking for `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements.txt` / `Pipfile`. Falls through to `ctx.server_dir` if it's a Python project under `server/`. Returns `None` if no Python project — V19 short-circuits.

### `_find_ruff_bin(py_root)`

```python
venv_ruff = py_root / ".venv" / "bin" / "ruff"
if venv_ruff.exists():
    return str(venv_ruff)
return "ruff"
```

### `_check_ruff_lint(py_root, file_path)` — V19-RUFF-{CODE}

```python
result = subprocess.run(
    [self._find_ruff_bin(py_root), "check", file_path,
     "--output-format", "text", "--no-fix"],
    cwd=str(py_root),
    capture_output=True, text=True, timeout=15,
)
if result.returncode != 0 and result.stdout.strip():
    LINE = re.compile(r'(.+?):(\d+):(\d+): (\S+) (.+)')
    for line in result.stdout.strip().split("\n"):
        if (m := LINE.match(line)):
            yield Finding(
                severity="error",
                file=m.group(1),
                line=int(m.group(2)),
                rule=f"V19-RUFF-{m.group(4)}",
                message=m.group(5),
                fix=f"Fix ruff error {m.group(4)}: {m.group(5)}",
            )
```

### `_check_ruff_format(py_root, file_path)` — V19-RUFF-FORMAT

```python
result = subprocess.run(
    [self._find_ruff_bin(py_root), "format", "--check", file_path],
    cwd=str(py_root),
    capture_output=True, text=True, timeout=10,
)
if result.returncode != 0:
    yield Finding(
        severity="warning",
        file=file_path,
        rule="V19-RUFF-FORMAT",
        message="File is not properly formatted by ruff",
        fix=f"Run 'ruff format {file_path}' to auto-format",
    )
```

### `validate_project(ctx)` — Tier 3 (V19-RUFF-ALL)

```python
def validate_project(self, ctx):
    py_root = self._find_python_root(ctx)
    if not py_root:
        return []
    return self._check_ruff_all(py_root)

def _check_ruff_all(self, py_root):
    result = subprocess.run(
        [self._find_ruff_bin(py_root), "check", ".",
         "--output-format", "text", "--no-fix"],
        cwd=str(py_root),
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0 or not result.stdout.strip():
        return []
    LINE = re.compile(r'(.+?):(\d+):(\d+): (\S+) (.+)')
    findings: list[Finding] = []
    count = 0
    for line in result.stdout.strip().split("\n"):
        if (m := LINE.match(line)):
            count += 1
            if count <= 20:
                findings.append(Finding(severity="warning", rule=f"V19-RUFF-{m.group(4)}", ...))
    if count > 20:
        findings.append(Finding(
            severity="warning",
            rule="V19-RUFF-SUMMARY",
            message=f"{count} total ruff issues found ({count - 20} not shown)",
            fix="Run 'ruff check .' to see all issues",
            ...
        ))
    return findings
```

### Could be more effective

- **`--output-format json`.** Text parsing is fragile (a colon in a filename can mis-split). Ruff's JSON output is stable and contains the full violation context (`fix.applicability`, etc.). One-line change.
- **Per-finding `fix` integration.** Ruff often knows the exact fix (`--fix-only`); preserving that in the Finding's `fix` field would let Claude apply it verbatim. Currently `fix` is a generic "Fix ruff error X" string.
- **`pyright` / `mypy` integration.** Ruff doesn't do type-checking. A separate Tier 3 type-check pass would catch a class of bugs ruff misses. Could be a sibling validator (V##-py-types) rather than V19 expansion.
- **Coverage delta on edited file.** Same idea as V09/V11 — `coverage.py run --append` plus per-file delta. V21's pytest path already runs the test suite; piggy-backing coverage is cheap.
- **Diff-only mode.** `ruff check --diff <file>` would ensure the lint scope is exactly the file just edited and not its imports' transitive checks.

## References

- [Ruff — Astral docs](https://docs.astral.sh/ruff/) — Astral, *continuously updated*, retrieved 2026-04-30. The single source of truth for rules, output formats, and config.
- [Ruff — Rules index](https://docs.astral.sh/ruff/rules/) — Astral, *continuously updated*, retrieved 2026-04-30. The rule-code prefixes (`E`, `F`, `W`, `B`, `S`, etc.) V19 preserves into `V19-RUFF-<code>`.
- [PEP 8 — Style Guide for Python Code](https://peps.python.org/pep-0008/) — Python, *originally 2001, continuously updated*, retrieved 2026-04-30. The style baseline Ruff's `E*` rules enforce.
- [Black — *The uncompromising Python code formatter*](https://black.readthedocs.io/en/stable/) — Łukasz Langa et al., *continuously updated*, retrieved 2026-04-30. Predecessor whose formatting rules `ruff format` adopted.

## Examples

### ✓ Pass

```python
# src/lib/format.py
"""Currency formatting helpers."""
from decimal import Decimal


def format_money(amount: int, currency: str = "USD") -> str:
    """Render an integer-cents amount as a localized currency string."""
    return f"${Decimal(amount) / 100:,.2f}"
```

### ✗ Fail

```python
# src/handler.py
import os                  # F401: unused import → V19-RUFF-F401 (error)

def     foo (x ):           # poorly formatted → V19-RUFF-FORMAT (warning)
    if x > 100000000000000000000000000000000000:  # E501 line too long → V19-RUFF-E501 (error)
        pass
```
