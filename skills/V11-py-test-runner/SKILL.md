# V11 — py-test-runner

> **Owner**: `hooks/validators/py_test_runner.py`
> **Tier**: 2 (PostToolUse) only. Stop is a no-op — V21 (`py_pytest`) handles project-wide pytest with the smart-mode gate (`stop.run_pytest`).
> **File patterns**: `**/*.py`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V11-TEST-FAIL` | error | The test corresponding to the edited Python file failed (single-file pytest run). |
| `V11-NO-TEST` | warning | A non-test source file was edited and no `test_<name>.py` / `<name>_test.py` exists nearby. |
| `V11-REPEATED-FAIL` | warning | Same `<file>::<test>` failed ≥ N times in a row (default 3, shared with V09/V10 via `FeedbackTracker`). |

## Why this verifier exists

Python test execution differs from Go's package-scoped `go test` in one important way: pytest **collects** all tests by default, so naively running pytest on every Edit re-collects the whole tree. V11 narrows the scope to "tests that exercise the file just edited" so Tier 2 latency stays under a few seconds.

Three regression patterns this catches:
1. **Refactor a function signature without updating tests.** Module imports work; the test crashes at call time.
2. **Add a new module without tests.** Common in hot prototype phases. V11-NO-TEST surfaces the gap as warning.
3. **Test breaks repeatedly across iterations.** Same as V09/V10 — points to spec/test bug, not implementation bug.

## Design rationale

- **Per-test-file invocation, not per-package.** pytest doesn't have Go's package abstraction; it has files (and conftest scopes). V11 maps `src/foo.py` → `tests/test_foo.py` → `pytest tests/test_foo.py -x -q`.
- **Stop is no-op (V21 covers it).** V21's smart-mode is the right place for the project-wide pytest run; doing it again here is redundant.
- **`-x` (stop on first failure) is intentional.** Tier 2's job is to give Claude one fixable signal per Edit, not a full failure report. The first failure is what matters; V21 (Stop) gets the whole picture.

## How it checks (implementation)

Lives in `hooks/validators/py_test_runner.py`.

### `validate_file(ctx, file_path)`

```python
def validate_file(self, ctx, file_path):
    py_root = self._find_python_root(ctx)
    if not py_root or not file_path.endswith(".py"):
        return []
    if self._is_excluded(file_path):
        return []

    threshold = ctx.config.thresholds.test_runner.repeated_failure_count
    findings: list[Finding] = []

    if self._is_test_file(file_path):
        findings.extend(self._run_test_file(py_root, file_path, threshold))
    else:
        test_file = self._resolve_test_file(py_root, file_path)
        if test_file:
            findings.extend(self._run_test_file(py_root, test_file, threshold))
        else:
            findings.extend(self._check_test_exists(file_path))
    return findings
```

### `_resolve_test_file(py_root, file_path)`

```python
# pytest convention: tests/test_<name>.py | <name>_test.py | tests/<dir>/test_<name>.py
src = Path(file_path)
name = src.stem
candidates = [
    py_root / "tests" / f"test_{name}.py",
    py_root / "tests" / src.parent.name / f"test_{name}.py",
    src.with_name(f"test_{name}.py"),
    src.with_name(f"{name}_test.py"),
]
return next((c for c in candidates if c.exists()), None)
```

### `_run_test_file(py_root, test_file, threshold)` — V11-TEST-FAIL / V11-REPEATED-FAIL

```python
result = subprocess.run(
    [self._python_bin(py_root), "-m", "pytest", "-x", "-q", "--tb=line", str(test_file)],
    cwd=str(py_root),
    capture_output=True, text=True, timeout=60,
)
if result.returncode == 0:
    return []

# Parse "FAILED <file>::<test>"
FAIL = re.compile(r'^FAILED\s+(\S+)::(\S+)')
tracker = FeedbackTracker(ctx)
for line in (result.stdout + result.stderr).splitlines():
    if (m := FAIL.match(line)):
        sig = f"V11::{m.group(1)}::{m.group(2)}"
        streak = tracker.record_failure(sig)
        yield Finding(
            severity="error",
            rule="V11-REPEATED-FAIL" if streak >= threshold else "V11-TEST-FAIL",
            file=m.group(1),
            message=m.group(2),
            ...
        )
```

### `_check_test_exists(file_path)` — V11-NO-TEST

```python
src = Path(file_path)
# Skip files that don't really need tests (heuristic):
non_blank = [
    line for line in src.read_text().splitlines()
    if line.strip() and not line.strip().startswith(("import ", "from "))
]
if len(non_blank) < 5:
    return []  # tiny / re-export / __init__-style — exempt

if not self._resolve_test_file(self.find_root, file_path):
    yield Finding(severity="warning", rule="V11-NO-TEST", ...)
```

### Could be more effective

- **`pytest --collect-only --quiet` for symbol-level coverage.** Currently V11 only checks "does a test file exist?". Collecting tests and matching `test_<func>` to the source's exported symbols would catch the "test file exists but doesn't test the new function" case.
- **Coverage delta on edited file.** `pytest --cov=<file>` + delta vs baseline → "coverage of `src/foo.py` dropped". `coverage.py` overhead is non-trivial — Tier 2 budget might not allow.
- **`pytest-testmon` integration.** testmon already runs only tests affected by the edit. V11 currently re-runs by file convention; testmon would be more accurate. Trade-off: extra dep + cache state to manage.
- **Fixture-level pollution detection.** A test that fails because a previous test polluted shared state is hard to diagnose. Tracking failure order would surface "test X fails when ordered after Y" patterns. Heavy to implement; high signal when it triggers.

## References

- [pytest — Configuration](https://docs.pytest.org/en/stable/reference/customize.html) — pytest team, *continuously updated*, retrieved 2026-04-30. Conventions for test discovery V11 mirrors.
- [pytest — Failure output (`--tb`)](https://docs.pytest.org/en/stable/how-to/output.html#modifying-python-traceback-printing) — pytest team, *continuously updated*, retrieved 2026-04-30. The `--tb=line` mode V11 uses for compact parsing.
- [pytest-testmon](https://testmon.org/) — Tibor Arpas + community, *continuously updated*, retrieved 2026-04-30. Reference upgrade target for test scoping.
- [Hypothesis — Stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html) — DRMacIver et al., *continuously updated*, retrieved 2026-04-30. Pattern for catching the kind of cross-test pollution V11 doesn't currently detect.

## Examples

### ✓ Pass

```python
# src/lib/format.py
def format_money(amount: int, currency: str = "USD") -> str: ...
```

```python
# tests/test_format.py
def test_format_money_default():
    assert format_money(1234) == "$12.34"
```

### ✗ Fail

```python
# src/lib/auth.py edited; no tests/test_auth.py exists
# → V11-NO-TEST (warning)
```

```
pytest output:
FAILED tests/test_format.py::test_format_money_default - AssertionError: ...
→ V11-TEST-FAIL (error). After 3 turns, V11-REPEATED-FAIL.
```
