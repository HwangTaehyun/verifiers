# V37 — go-test-race-coverage

> **Owner**: `hooks/validators/test_race_coverage.py` (planned, not yet implemented)
> **Tier**: 3 (Stop) — comprehensive CI/build file check on all commits
> **File patterns**: `.github/workflows/*.yml`, `Makefile`, `justfile`, `*.mk`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V37-CI-NO-RACE` | error | A `go test` command in CI workflows lacks the `-race` flag. |
| `V37-CI-NO-COVERAGE-GATE` | warning | No `go test -coverprofile=...` or coverage upload step detected in CI. |

## Why this verifier exists

Go's race detector identifies data races in concurrent code. In financial systems (invoice matching, payment processing), concurrent bugs are rare but catastrophic: two goroutines write the same invoice record, one overwrites the other's state, money is lost.

Example from `server/internal/finance/invoice_number_test.go:107` — a test guarded by `INVOICE_RACE_TEST=1` env var:

```go
// Comment: "known race in concurrent invoice-number generation; 
// safe in production via mutex but test must opt-in"
func TestConcurrentInvoiceAlloc(t *testing.T) {
    if os.Getenv("INVOICE_RACE_TEST") == "" {
        t.Skip("race test disabled by default")
    }
    // ...
}
```

And from `.github/workflows/ci.yml:54`:

```yaml
- name: Test
  run: go test ./...  # ← No -race flag
```

The race scenario is documented but never runs in CI. A future change that reintroduces the race will ship undetected.

Best practice: `go test -race ./...` in CI. The race detector runs all tests concurrently with memory instrumentation; ~10% slowdown.

Coverage gate is secondary — it doesn't prevent bugs but catches untested code paths.

**Primary citations**:
- [Go blog: Introducing the Go Race Detector](https://go.dev/blog/race-detector) — published 2013-06-26, retrieved 2026-04-30.
- [Go blog: The cover story](https://go.dev/blog/cover) — published 2013-12-02, retrieved 2026-04-30.

## Design rationale

- **Severity: error for `-race`, warning for coverage.** Race detection is non-negotiable in concurrent financial code. Coverage gates are best practice but optional (some projects cap coverage at 80%).
- **CI-only scope.** Local developer runs (`make test`) may skip `-race` for speed. V37 only checks CI workflows where latency is acceptable.
- **Coverage gate is lenient.** V37 only checks *presence* of coverage reporting. Projects may choose 50% or 95% gate thresholds; V37 doesn't enforce a floor.
- **Multiple-workflow aware.** A project may have separate `ci.yml` (unit tests), `integration.yml` (slower), and `nightly.yml` (expensive). V37 flags `-race` absence in any workflow that mentions `go test`.
- **Makefile and justfile support.** CI often invokes `make test` or `just test`, which then runs `go test`. V37 scans those files too.

## How it checks (implementation plan)

Lives in `hooks/validators/test_race_coverage.py`.

### Top-level

```python
def validate_project(self, ctx):
    findings = []
    findings.extend(self._check_workflows(ctx))
    findings.extend(self._check_makefile(ctx))
    findings.extend(self._check_justfile(ctx))
    return findings

def _check_workflows(self, ctx):
    """Scan .github/workflows/*.yml for go test commands."""
    workflows_dir = ctx.project_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return []
    
    return self._scan_yaml_files(workflows_dir)

def _scan_yaml_files(self, workflows_dir):
    """Parse YAML and find go test steps."""
    findings = []
    for yml_file in workflows_dir.glob("*.yml"):
        try:
            data = yaml.safe_load(yml_file.read_text())
            if not data or "jobs" not in data:
                continue
            
            for job_name, job_spec in data["jobs"].items():
                findings.extend(
                    self._check_job_steps(yml_file, job_spec)
                )
        except yaml.YAMLError:
            # Skip malformed YAML
            pass
    
    return findings

def _check_job_steps(self, yml_file, job_spec):
    """Scan job steps for go test commands."""
    if "steps" not in job_spec:
        return []
    
    findings = []
    for i, step in enumerate(job_spec["steps"]):
        if "run" not in step:
            continue
        
        run_cmd = step["run"]
        if "go test" not in run_cmd:
            continue
        
        # Found a go test step
        has_race = "-race" in run_cmd
        has_coverage = (
            "-coverprofile=" in run_cmd or
            "coverage" in run_cmd.lower()
        )
        
        if not has_race:
            yield Finding(
                rule="V37-CI-NO-RACE",
                file=str(yml_file),
                line=i + 1,  # approximate
                message="go test step missing -race flag",
            )
        
        if not has_coverage:
            yield Finding(
                rule="V37-CI-NO-COVERAGE-GATE",
                file=str(yml_file),
                line=i + 1,
                message="go test step missing -coverprofile or coverage upload",
            )
```

### `_check_makefile(ctx)` — V37-CI-NO-RACE in Makefile

```python
def _check_makefile(self, ctx):
    """Scan Makefile and *.mk for go test targets."""
    makefile = ctx.project_root / "Makefile"
    if not makefile.exists():
        return []
    
    findings = []
    src = makefile.read_text()
    
    # Find lines like:   test:   go test ./...
    for i, line in enumerate(src.splitlines(), 1):
        if "go test" in line and "-race" not in line:
            yield Finding(
                rule="V37-CI-NO-RACE",
                file=str(makefile),
                line=i,
                message="Makefile test target missing -race",
            )
```

### `_check_justfile(ctx)` — V37-CI-NO-RACE in justfile

```python
def _check_justfile(self, ctx):
    """Scan justfile for go test targets."""
    justfile = ctx.project_root / "justfile"
    if not justfile.exists():
        return []
    
    findings = []
    src = justfile.read_text()
    
    for i, line in enumerate(src.splitlines(), 1):
        if "go test" in line and "-race" not in line:
            yield Finding(
                rule="V37-CI-NO-RACE",
                file=str(justfile),
                line=i,
                message="justfile test target missing -race",
            )
```

## Could be more effective

- **Coverage percentage enforcement.** Add config knob to specify minimum coverage gate (e.g., `test_race_coverage.min_coverage: 75`). Currently V37 only checks *presence*.
- **Parallel race runs.** Some CI systems run tests in parallel (faster). Go's race detector is slower with `-parallel`. A secondary check could detect and warn about `GOMAXPROCS` overrides.
- **Coverage report upload.** Check for `codecov`, `coveralls`, or similar third-party coverage integrations and flag if missing.
- **Test isolation.** Detect tests that depend on execution order (not shuffled). Go 1.17+ supports `-shuffle=...` flag.
- **Slow-test tagging.** Check that slow tests (integration tests) are marked with a build tag (e.g., `// +build integration`) so they can be skipped in fast CI runs.

## References

- [Go blog: Introducing the Go Race Detector](https://go.dev/blog/race-detector) — published 2013-06-26, retrieved 2026-04-30. Original announcement and how the race detector works.
- [Go blog: The cover story](https://go.dev/blog/cover) — published 2013-12-02, retrieved 2026-04-30. Coverage analysis and reporting.
- [pkg.go.dev: go test -race](https://pkg.go.dev/cmd/go#hdr-Test_packages) — continuously updated, retrieved 2026-04-30. The `-race` flag documentation.
- [Go issue: proposal - always run tests with -race](https://github.com/golang/go/issues/5744) — created 2013-07-02, retrieved 2026-04-30. Community discussion on race detector as a default.

## Examples

### ✓ Pass

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-go@v4
        with:
          go-version: '1.22'
      
      - name: Test with race detector
        run: go test -race -coverprofile=coverage.out ./...  # ✓ both flags
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.out
```

```makefile
# Makefile
.PHONY: test
test:
	go test -race -coverprofile=coverage.out ./...  # ✓ -race present
```

```justfile
# justfile
@test:
    go test -race -v ./...  # ✓ -race present
```

### ✗ Fail

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-go@v4
      
      - name: Test
        run: go test ./...  # → V37-CI-NO-RACE (no -race flag)
                            # → V37-CI-NO-COVERAGE-GATE (no -coverprofile)
```

```makefile
# Makefile
test:
	go test ./cmd/...  # → V37-CI-NO-RACE
	go test ./internal/...
```

```go
// server/internal/finance/invoice_number_test.go
// Race scenario documented but gated behind env var,
// never runs in V37-checked CI
func TestConcurrentInvoiceAlloc(t *testing.T) {
    if os.Getenv("INVOICE_RACE_TEST") == "" {
        t.Skip("race test disabled by default")
    }
    // ... concurrent goroutines modifying shared invoice state
}
```
