# V06 — go-quality

> **Owner**: `hooks/validators/go_quality.py`
> **Tier**: 2 (PostToolUse) — `go vet` + `gofmt -l file` + `go build` (cheap, sub-second on cached). 3 (Stop) — adds `golangci-lint run` + `go test -race -count=1 ./...`.
> **File patterns**: `**/*.go`, `**/go.mod`, `**/go.sum`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V06-GO-VET` | error | `go vet ./...` reported a finding (parsed `(.+\.go):(\d+):\d+: (.+)`). |
| `V06-GOFMT` | warning | `gofmt -l <file>` returned non-empty (file is not gofmt-formatted). |
| `V06-BUILD-FAIL` | error | `go build ./...` exited non-zero. |
| `V06-LINT-<linter>` | warning / error | `golangci-lint run --out-format json` reported an issue; the rule preserves the underlying linter name (`errcheck`, `gosec`, `unused`, ...). |
| `V06-TEST-FAIL` | error | `go test -race -count=1 ./...` produced `--- FAIL: (\S+)`. |

## Why this verifier exists

Go's standard toolchain is famously thorough — but only when invoked. The cost gradient is:

- **`go vet`** (sub-second, idiomatic checks) → run on every edit.
- **`gofmt -l`** (instant per file) → run on every edit.
- **`go build`** (incremental, ~1-3 s on a cached project) → run on every edit.
- **`golangci-lint run`** (10-60 s depending on linter set) → run only on Stop.
- **`go test -race ./...`** (project-dependent, can be minutes) → run only on Stop, with the 30 s per-validator timeout from `parallel_runner` as the upper bound.

V06's two-tier split is essential — running golangci-lint on every Edit would exceed the Tier 2 budget (~5 s soft target). Running only on Stop means the slower checks happen exactly once per turn, which matches the user's expectation: edit fast, verify thoroughly at the end.

## Design rationale

- **`go vet` as `error`, not `warning`.** Vet's checks are conservative — every `vet` finding has been observed to cause real bugs. False positives are rare enough that flagging them as errors is correct.
- **`gofmt` as `warning`, not `error`.** `gofmt` is mechanical — the fix is "run gofmt". Flagging as warning + including `Run 'gofmt -w <file>'` in the `fix` field gives the AI agent direct remediation.
- **`golangci-lint` rule name preserved (`V06-LINT-errcheck`).** This way users can disable specific noisy linters via `validators.disabled: ["V06-LINT-godox"]` without disabling all of V06.
- **`go test -race -count=1`** explicitly. `-race` catches concurrent-access bugs (which Go's runtime hides without it). `-count=1` defeats Go's test cache so results are reproducible. Without these, the test gate is theater.

## How it checks (implementation)

Lives in `hooks/validators/go_quality.py`. Tier 2 entry runs `_has_go_project` short-circuit (no `.go` / `go.mod` → bail) then 3 cheap checks; Tier 3 runs the same plus 2 expensive ones.

### Tier 2 (`validate_file`)

```python
def validate_file(self, ctx, file_path):
    if not self._has_go_project(ctx):
        return []
    findings: list[Finding] = []
    findings.extend(self._check_go_vet(ctx))
    if file_path.endswith(".go"):
        findings.extend(self._check_gofmt(ctx, file_path))
    findings.extend(self._check_go_build(ctx))
    return findings
```

### `_check_go_vet(ctx)` — V06-GO-VET

```python
result = subprocess.run(
    ["go", "vet", "./..."],
    cwd=str(ctx.server_dir),
    capture_output=True, text=True, timeout=20,
)
LINE = re.compile(r'^(.+\.go):(\d+):\d+: (.+)$')
for line in result.stderr.splitlines():
    if (m := LINE.match(line)):
        yield Finding(
            severity="error",
            file=m.group(1),
            line=int(m.group(2)),
            rule="V06-GO-VET",
            message=m.group(3),
            ...
        )
```

### `_check_gofmt(ctx, file_path)` — V06-GOFMT

```python
# Single-file: gofmt -l <file> prints the path if it's unformatted, else nothing
result = subprocess.run(
    ["gofmt", "-l", file_path],
    cwd=str(ctx.server_dir),
    capture_output=True, text=True, timeout=5,
)
if result.stdout.strip():
    yield Finding(
        severity="warning",
        rule="V06-GOFMT",
        fix=f"Run 'gofmt -w {file_path}'",
        ...
    )
```

### `_check_go_build(ctx)` — V06-BUILD-FAIL

```python
result = subprocess.run(
    ["go", "build", "./..."],
    cwd=str(ctx.server_dir),
    capture_output=True, text=True, timeout=60,
)
if result.returncode != 0:
    BUILD_ERR = re.compile(r'^(.+\.go):(\d+):\d+: (.+)$')
    for line in result.stderr.splitlines():
        if (m := BUILD_ERR.match(line)):
            yield Finding(severity="error", rule="V06-BUILD-FAIL", ...)
```

### Tier 3 (`validate_project`) — adds the heavyweights

#### `_check_golangci_lint(ctx)` — V06-LINT-<linter>

```python
result = subprocess.run(
    ["golangci-lint", "run", "--out-format", "json", "./..."],
    cwd=str(ctx.server_dir),
    capture_output=True, text=True, timeout=120,
)
data = json.loads(result.stdout) if result.stdout else {}
for issue in data.get("Issues") or []:
    yield Finding(
        severity="warning",
        file=issue["Pos"]["Filename"],
        line=issue["Pos"]["Line"],
        rule=f"V06-LINT-{issue['FromLinter']}",
        message=issue["Text"],
        ...
    )
```

JSON format is essential — text output is fragile across golangci-lint versions.

#### `_check_go_test(ctx)` — V06-TEST-FAIL

```python
# Prefer Makefile target if present (project-specific test setup),
# else default to `go test -race -count=1 ./...`.
makefile = ctx.server_dir / "Makefile"
cmd = (
    ["make", "test"]
    if makefile.exists() and "^test:" in makefile.read_text()
    else ["go", "test", "-race", "-count=1", "./..."]
)
result = subprocess.run(cmd, cwd=str(ctx.server_dir), timeout=120)
FAIL = re.compile(r'^--- FAIL:\s+(\S+)')
for line in result.stdout.splitlines():
    if (m := FAIL.match(line)):
        yield Finding(rule="V06-TEST-FAIL", message=m.group(1), ...)
```

### Could be more effective

- **`gosec` as a separate linter inside V06.** Currently `gosec` only runs if the project's `golangci.yml` enables it. Forcing `gosec` regardless would catch misconfigured projects — but might double-emit on configured projects. Trade-off deferred.
- **Coverage delta.** `go test -cover` + a stored baseline = "this commit dropped coverage from 78% to 71%". Out of V06's scope (would need persistent state); could be a future V##.
- **`govulncheck`.** `govulncheck ./...` against `pkg.go.dev/vuln/db` flags known CVEs in dependencies. Cheap on Tier 3, high signal. Strong candidate for the Phase 27 V26-style follow-up.
- **`go mod tidy --diff`.** A non-tidy `go.mod` / `go.sum` is a frequent CI break. Currently V06 doesn't check; a `go mod tidy --diff` exit code would be one-line addition.
- **Per-package targeted test.** Currently Stop runs `./...` (everything). On a large repo this is expensive. V09 already does the per-package run on Tier 2; combining with V06's Stop run is redundant. Future enhancement: have V06 test only packages whose source changed since last commit.

## References

- [Go — Command vet](https://pkg.go.dev/cmd/vet) — Go team, *continuously updated*, retrieved 2026-04-30. The static-analysis checks V06-GO-VET surfaces.
- [Go — Command gofmt](https://pkg.go.dev/cmd/gofmt) — Go team, *continuously updated*, retrieved 2026-04-30. The format spec V06-GOFMT enforces.
- [golangci-lint — Configuration](https://golangci-lint.run/usage/configuration/) — golangci-lint contributors, *continuously updated*, retrieved 2026-04-30. The JSON output schema V06 parses.
- [The Go Programming Language Specification — Memory model](https://go.dev/ref/mem) — Go team, *continuously updated*, retrieved 2026-04-30. Why `-race` is non-negotiable: Go's compile-time guarantees do not include race-freedom.
- [Effective Go](https://go.dev/doc/effective_go) — Go team, *originally 2009, continuously updated*, retrieved 2026-04-30. The idiomatic baseline V06 enforces in spirit.
- [Uber Go Style Guide](https://github.com/uber-go/guide/blob/master/style.md) — Uber, *continuously maintained*, retrieved 2026-04-30. Stricter conventions golangci-lint can enforce when configured.

## Examples

### ✓ Pass

```go
// passes vet, gofmt, build
package handler

import "context"

func (s *Server) GetUser(ctx context.Context, id string) (*User, error) {
    return s.repo.Find(ctx, id)
}
```

### ✗ Fail

```go
package handler

func GetUser(id string) *User {
    if id == "" {                 // (style aside, this passes V06)
    return nil                    // → V06-GOFMT (warning, mis-indented)
    }
    return findUser(id)           // assume undefined → V06-BUILD-FAIL (error)
}

func _foo() {
    fmt.Sprintf("%d %s", "wrong order")  // → V06-GO-VET (error, format mismatch)
}
```

```go
// _test.go
func TestUserList(t *testing.T) {
    got := list()
    require.Equal(t, []User{u}, got)   // failing test → V06-TEST-FAIL on Stop
}
```
