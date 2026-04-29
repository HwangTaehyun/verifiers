# V21 — pytest

> **Owner**: `hooks/validators/py_pytest.py`
> **Tier**: 3 (Stop) only — pytest is too slow for Tier 2. Tier 2's per-file pytest run is V11's job.
> **File patterns**: `**/*.py`, `**/pyproject.toml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V21-TEST-FAIL` | error | `pytest -x -q --tb=no` exited non-zero with at least one `FAILED <file>::<test>` line. The message includes the failure count and up to 5 failed test names. |

V21 has only one finding rule but four execution gates (see config below).

## Config — `stop.run_pytest`

```yaml
stop:
  run_pytest: "smart"   # default
  # | "smart"   — run only when this turn touched .py / pyproject.toml (git diff --name-only HEAD)
  # | "always"  — run on every Stop hook (Phase28-pre V19 behavior)
  # | "never"   — never run; CI is the safety net
```

When `git` is unavailable or returns non-zero, smart mode **fails open** (runs pytest) so a misconfigured repo never silently suppresses the test gate.

## Why this verifier exists

Phase 28's design problem: V19 used to run *both* ruff and pytest in one validator slot. The Tier 3 parallel runner schedules per-validator, so the two checks queued behind one slot — pytest's seconds-of-work blocked ruff's milliseconds-of-work, exactly the opposite of what Amdahl's law wants.

V21 is the pytest half of the split. With V19 (ruff) and V21 (pytest) as separate registry entries, the parallel runner schedules them on different threads. On the user's repo this took Tier 3 wall clock from 7.8s sequential / 6.8s with the previous 4-worker pool down to ~5.6s with thread pool — measurable, repeatable.

The **smart mode** is the second contribution: pytest is expensive even after parallelization, and most turns don't touch Python (markdown / yaml / TS / Go edits). Running pytest only when the working tree has uncommitted Python changes drops Tier 3 to ~1s on those turns. The exact thing the Phase 27 audit asked for.

## Design rationale

- **Stop-only.** pytest's per-file mode is V11; V21 owns the project-wide run. No per-Edit invocation — would blow the Tier 2 budget.
- **`-x -q --tb=no`** specifically:
  - `-x` stops on first failure. We don't need a failure report; we need *one signal* per Stop.
  - `-q` quiet output. Smaller stdout to parse.
  - `--tb=no` no tracebacks. We surface failed test names; the user reruns locally for traces.
- **Smart mode default.** The ROI of "run pytest on every Stop" is low when most Stop hooks don't touch Python. Smart-mode default reflects empirical usage.
- **Fail-open on git error.** Smart mode's heuristic is "did git diff find a Python change?". If git itself is broken (no `.git/` in worktree, hung subprocess), the heuristic returns "yes, run pytest" so a CI runner that doesn't ship git as `git` doesn't silently disable the test gate.
- **Falls back to `python` in `.venv` if available.** `<py_root>/.venv/bin/python` is preferred over the system `python` so the project's pinned interpreter (and its installed pytest) is used. Same convention as V19's `.venv/bin/ruff`.
- **Gate uses `git diff --name-only HEAD`, not `--cached` or `--staged`.** Captures both staged and unstaged changes. Matches the user's mental model of "what did I change in this session".

## How it checks (implementation)

Lives in `hooks/validators/py_pytest.py`.

### `validate_project(ctx)` — only entry point

```python
def validate_project(self, ctx):
    run_mode = ctx.config.stop.run_pytest
    if run_mode == "never":
        return []

    py_root = self._find_python_root(ctx)
    if not py_root:
        return []

    if run_mode == "smart" and not has_uncommitted_python_changes(py_root):
        return []

    return self._check_pytest(py_root)
```

`validate_file` is the BaseValidator default (no-op) — V21 is intentionally Stop-only.

### `has_uncommitted_python_changes(project_root)` — smart-mode oracle

```python
def has_uncommitted_python_changes(project_root):
    """Smart-mode oracle: did this turn touch Python sources?"""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(project_root),
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return True  # fail open

    if result.returncode != 0:
        return True  # not a git repo / detached HEAD with no commits

    SUFFIXES = (".py",)
    NAMES = ("pyproject.toml",)
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        if name.endswith(SUFFIXES):
            return True
        if Path(name).name in NAMES:
            return True
    return False
```

### `_check_pytest(py_root)` — V21-TEST-FAIL

```python
python_bin = self._find_python_bin(py_root)
cmd = [python_bin, "-m", "pytest", "-x", "-q", "--tb=no"]
env = self._load_dotenv(py_root)

result = subprocess.run(
    cmd,
    cwd=str(py_root),
    capture_output=True, text=True,
    timeout=180,
    env=env,
)

if result.returncode == 0:
    return []

output = result.stdout + result.stderr

# Skip false positives: exit-non-zero from plugin warnings, not test failures
if re.search(r'\d+ passed', output) and not re.search(r'\d+ failed', output):
    return []
if "FAILED" not in output and "ERROR" not in output and "failed" not in output:
    return []

# Parse counts
m = re.search(r'(\d+) failed', output)
failed_count = m.group(1) if m else "unknown"
failed_tests = re.findall(r'FAILED\s+(\S+)', output)
test_names = ", ".join(failed_tests[:5]) if failed_tests else "see output"

return [
    Finding(
        severity="error",
        file=str(py_root),
        rule="V21-TEST-FAIL",
        message=f"pytest: {failed_count} test(s) failed: {test_names}",
        fix=f"Fix failing tests. Run 'cd {py_root} && python -m pytest -x -v' for details",
    )
]
```

### `_load_dotenv(py_root)`

```python
def _load_dotenv(self, py_root):
    """Load .env file and merge with current environment."""
    env = os.environ.copy()
    dotenv_path = py_root / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("\"'")
    return env
```

V21 needs `.env` because pytest commonly reads test-only env vars (`DATABASE_URL`, `REDIS_URL`).

### Could be more effective

- **`pytest-testmon` instead of full suite.** testmon caches which tests cover which lines and reruns only the affected subset. On the user's repo (1065 tests), it would drop runtime from ~5.6s to typically <1s. Cost: extra dependency + cache state in `.verifiers/state/`.
- **Exit codes 1 vs 2 distinction.** pytest 1 = test failures, 2 = test collection error, 5 = no tests collected. V21 currently treats every non-zero as `V21-TEST-FAIL`. Splitting (e.g., `V21-COLLECTION-ERROR`) would give better signals.
- **Coverage delta integration.** Same pattern as V09/V11/V19 — `pytest --cov` + delta vs baseline. Would unlock "this commit dropped overall coverage" findings without a separate validator.
- **Per-file scoped run when `smart` triggers.** Currently smart runs the whole suite. Could narrow to "run only tests in the same package as the touched file". Combination of git diff + pytest path discovery.
- **Watch-mode upgrade.** A persistent pytest watcher that responds to file changes would yield sub-100ms feedback. Architecturally heavier than V21's design (long-running process); out of scope for hook-time validation.

## References

- [pytest — Stopping after the first failure (`-x`)](https://docs.pytest.org/en/stable/how-to/output.html) — pytest team, *continuously updated*, retrieved 2026-04-30.
- [pytest — Failure traceback (`--tb`)](https://docs.pytest.org/en/stable/how-to/output.html#modifying-python-traceback-printing) — pytest team, *continuously updated*, retrieved 2026-04-30.
- [pytest-testmon](https://testmon.org/) — Tibor Arpas + community, *continuously updated*, retrieved 2026-04-30. Reference upgrade target for selective test execution.
- [Phase 28 design — V19 split + smart pytest (this repo)](https://github.com/HwangTaehyun/verifiers/commit/f109aa7) — Phase28 commit, *2026-04-29*, retrieved 2026-04-30. The architectural decision V21 implements.
- [Amdahl's law — Wikipedia](https://en.wikipedia.org/wiki/Amdahl%27s_law) — *continuously updated*, retrieved 2026-04-30. Why splitting a long-running validator out of a parallel-runner slot is a real perf win.

## Examples

### ✓ Pass

```
$ uv run pytest tests/ -q
............................. (1065 passed)
$ git diff --name-only HEAD
docs/CHANGELOG.md
README.md
# Smart mode → no .py / pyproject.toml change → V21 returns [] (no findings)
# Tier 3 wall clock: ~1s
```

### ✗ Fail

```
$ pytest -x -q --tb=no
F.....
=================================== short test summary =====================================
FAILED tests/test_format.py::test_format_money_default
1 failed, 5 passed in 0.42s

→ V21-TEST-FAIL (error): "pytest: 1 test(s) failed: tests/test_format.py::test_format_money_default"
```

```yaml
# .verifiers/config.yaml
stop:
  run_pytest: "always"   # always run, even on markdown-only turns
```
