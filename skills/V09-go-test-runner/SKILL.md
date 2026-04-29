# V09 — go-test-runner

> **Owner**: `hooks/validators/go_test_runner.py`
> **Tier**: 2 (PostToolUse) only. Stop is intentionally a no-op — V06's `validate_project` already runs `go test -race -count=1 ./...` against the entire module.
> **File patterns**: `**/*.go`, `**/go.mod`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V09-TEST-FAIL` | error | The package containing the just-edited Go file has a failing test (`go test -json` reported `Action: "fail"`). |
| `V09-NO-TEST` | warning | A non-test source file was edited and the corresponding `_test.go` either doesn't exist or contains no `func Test*` for an exported symbol the file defines. |
| `V09-REPEATED-FAIL` | warning | The shared failure tracker (`lib/feedback_tracker.py`) sees the same `<package>::<test>` fail ≥ N times in a row (configurable via `thresholds.test_runner.repeated_failure_count`, default 3). Suggests the test or the spec, not the implementation, is wrong. |

## Why this verifier exists

Go's testing model is **package-scoped**: a change inside `internal/users/repository.go` only affects `internal/users/...`, so running the whole `./...` test surface on every Edit is wasteful (often minutes vs the Tier 2 budget of a few seconds). V09's design is "find the package the edited file belongs to, run only those tests, run them fast".

Three failure modes V09 catches that are otherwise easy to miss:

1. **Repository-method change with no test update.** The compiler accepts the new signature; the existing test still calls the old one. `go build` passes; `go test` fails. V09 surfaces it on the *Edit*, not on commit.
2. **New exported symbol with no test.** `func (r *Repo) NewMethod` defined, no corresponding `TestNewMethod` exists. `V09-NO-TEST` warns — not as an error, because some things genuinely don't need tests (config-only types) — but at least the gap is visible.
3. **Same test failing 3+ times across a short window.** Almost certainly the test is wrong (or the spec). Continuing to "fix the implementation" wastes turns. The repeated-fail tracker pumps the brakes and suggests the user re-look at the test itself.

## Design rationale

- **Per-package, not per-file.** Go's `_test.go` discipline scopes tests to the package. Running just one file's tests is impossible at the `go` CLI level — V09 maps the edited file → `go list -f '{{.Dir}}'` → run that dir.
- **`-json` output is mandatory.** Plain text output is too brittle to parse across Go versions. JSON gives one event per `Action: "run" | "pass" | "fail"`.
- **Stop is a no-op (V06 covers it).** Running the per-package check on Stop would duplicate V06's race-test, doubling Tier 3 cost. V06 owns the project-wide test surface; V09 owns the per-edit fast feedback.
- **Repeated-fail counter is shared with V10/V11.** All three test-runner validators write into the same `lib/feedback_tracker.FeedbackTracker` so a Go test failing 2× and a TS test failing 1× still count distinctly. Per-test memory means the counter resets when the user changes the test (because the failure signature changes).
- **`V09-NO-TEST` is warning, not error.** A type-only file or a `wire.go` (DI registration) doesn't need tests. Hard-failing would generate noise and erode trust in the warning.

## How it checks (implementation)

Lives in `hooks/validators/go_test_runner.py`. `validate_file` runs the per-edit pipeline; `validate_project` is a no-op.

### `validate_file(ctx, file_path)`

```python
def validate_file(self, ctx, file_path):
    if not (ctx.server_dir and ctx.server_dir.exists()):
        return []
    if not file_path.endswith(".go"):
        return []
    threshold = ctx.config.thresholds.test_runner.repeated_failure_count
    findings: list[Finding] = []

    # Test file edited: run that package directly
    if file_path.endswith("_test.go"):
        pkg_dir = self._get_package_dir(ctx, file_path)
        if pkg_dir:
            findings.extend(self._run_package_tests(ctx, pkg_dir, file_path, threshold))
        return findings

    if self._is_excluded(file_path):  # vendor / generated / .gen.
        return findings

    # Source file edited: try to resolve the test package
    pkg_dir = self._resolve_test_package(ctx, file_path)
    if pkg_dir:
        findings.extend(self._run_package_tests(ctx, pkg_dir, file_path, threshold))
    else:
        findings.extend(self._check_test_exists(ctx, file_path))
    return findings
```

### `_resolve_test_package(ctx, file_path)`

```python
# `go list -f '{{.Dir}}' <import-path>` would be ideal, but it requires
# a build. V09 uses the convention "test file lives in the same dir":
parent = Path(file_path).parent
if any(parent.glob("*_test.go")):
    return parent
return None
```

### `_run_package_tests(ctx, pkg_dir, file_path, threshold)` — V09-TEST-FAIL

```python
result = subprocess.run(
    ["go", "test", "-json", "-count=1", "./..."],
    cwd=str(pkg_dir),
    capture_output=True, text=True, timeout=60,
)
failures: list[tuple[str, str]] = []  # (package, test_name)
for line in result.stdout.splitlines():
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        continue
    if evt.get("Action") == "fail" and "Test" in evt.get("Test", ""):
        failures.append((evt["Package"], evt["Test"]))

# Record into the shared tracker; emit V09-REPEATED-FAIL when streak ≥ threshold
tracker = FeedbackTracker(ctx)
for pkg, test in failures:
    sig = f"V09::{pkg}::{test}"
    streak = tracker.record_failure(sig)
    yield Finding(
        severity="error",
        rule="V09-TEST-FAIL" if streak < threshold else "V09-REPEATED-FAIL",
        ...
    )
```

### `_check_test_exists(ctx, file_path)` — V09-NO-TEST

```python
# Heuristic: count exported symbols (Test should exist for at least one)
EXPORT = re.compile(r'^func\s+(?:\(\w+\s+\*?\w+\)\s+)?([A-Z]\w+)\s*\(', re.MULTILINE)
src = Path(file_path).read_text()
exports = [m.group(1) for m in EXPORT.finditer(src) if not m.group(1).startswith("Test")]
if not exports:
    return []  # nothing to test

test_file = Path(file_path).with_name(Path(file_path).stem + "_test.go")
if not test_file.exists():
    yield Finding(severity="warning", rule="V09-NO-TEST", ...)
    return
test_src = test_file.read_text()
if not any(f"Test{e}" in test_src for e in exports):
    yield Finding(severity="warning", rule="V09-NO-TEST", ...)
```

### Could be more effective

- **`go test -run` per changed function.** Currently V09 runs the whole package's tests. With `git diff` against HEAD V09 could narrow to "tests that mention the edited symbol", further cutting Tier 2 latency on large packages.
- **Coverage delta on edited file.** `go test -coverprofile=...` + parsing → "coverage for `repository.go` dropped 80% → 65%". Project-wide coverage delta is hard; per-file is tractable. Future enhancement.
- **Race detector on Tier 2.** Currently `-race` is Stop-only (V06). Adding `-race` to V09 would surface concurrency bugs at edit time, but doubles single-package test runtime. Per-project knob worth exploring.
- **Build cache awareness.** `go test` already caches; V09's `-count=1` defeats the cache to force a re-run. Removing `-count=1` would drop typical Tier 2 from seconds to milliseconds, but at the cost of stale-cache false-passes after toolchain or env changes. Trade-off documented; default (`count=1`) errs toward correctness.
- **Spec-vs-test distinction.** `V09-REPEATED-FAIL` says "the test or the spec is wrong". Could be sharper: if the test file *changed* in the same turn, the test is the suspect; if not, the spec is. Usable signal sitting in `git diff`.

## References

- [Go — `go test`](https://pkg.go.dev/cmd/go#hdr-Test_packages) — Go team, *continuously updated*, retrieved 2026-04-30. The package-scoped testing model V09 leverages.
- [Go — `-json` test output](https://pkg.go.dev/cmd/test2json) — Go team, *continuously updated*, retrieved 2026-04-30. The structured event format V09 parses.
- [Kent Beck — Test-Driven Development by Example, ch. 1](https://www.amazon.com/Test-Driven-Development-Kent-Beck/dp/0321146530) — Kent Beck, *published 2002*, retrieved 2026-04-30. The "fast feedback" principle V09 operationalizes.
- [Mike Bland — Testable code](https://mike-bland.com/2025/10/01/the-three-pillars.html) — Mike Bland, *published 2025-10-01*, retrieved 2026-04-30. Source for the "the same test failing repeatedly suggests the test is wrong" heuristic V09-REPEATED-FAIL embodies.

## Examples

### ✓ Pass

```go
// internal/users/repository.go
func (r *Repo) Find(ctx context.Context, id string) (*User, error) { ... }
```

```go
// internal/users/repository_test.go
func TestFind(t *testing.T) {
    r := NewRepo(...)
    got, err := r.Find(ctx, "u1")
    require.NoError(t, err)
    require.Equal(t, "Alice", got.Name)
}
```

### ✗ Fail

```go
// repository.go added: func (r *Repo) Update(...)
// repository_test.go: no TestUpdate          → V09-NO-TEST (warning)
```

```
go test -json output:
{"Action": "fail", "Package": "internal/users", "Test": "TestFind", ...}
→ V09-TEST-FAIL (error). After 3 consecutive turns, also V09-REPEATED-FAIL.
```
