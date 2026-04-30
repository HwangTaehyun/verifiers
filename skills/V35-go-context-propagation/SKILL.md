# V35 — go-context-propagation

> **Owner**: `hooks/validators/context_propagation.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse) — runs on `internal/**/*.go` files (non-test)
> **File patterns**: `server/internal/**/*.go` (excludes `*_test.go`)

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V35-MID-FLOW-BACKGROUND-CTX` | error | `context.Background()` or `context.TODO()` is called inside `internal/` code (not at program root, not at goroutine root) without an exemption annotation. |

## Why this verifier exists

`context.Background()` creates a context that never times out and propagates no deadline. It's correct at program root (`main.go`) where a new request arrives, or at the root of a background goroutine. It's a bug mid-call-chain.

Example from `server/internal/finance/minio_pdf_renderer.go:304`:

```go
func (r *PDFRenderer) Render(ctx context.Context, cmf *CMF) ([]byte, error) {
    ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)  // ← WRONG
    defer cancel()
    
    resp, err := r.s3Client.GetObject(ctx, bucket, key)  // ...
}
```

Caller passes a context with a 5-second deadline (e.g., HTTP request). But `PDFRenderer.Render` ignores it and creates a 60-second timeout. When the HTTP request's 5-second deadline hits, the S3 connection is still alive, wasting resources. If the caller is under backpressure (many slow requests), S3 connections accumulate.

Correct pattern: accept and propagate the caller's context:

```go
ctx, cancel := context.WithTimeout(ctx, 60*time.Second)  // Use caller's ctx
defer cancel()
```

Now the connection respects the tighter deadline (5s < 60s).

V35 catches mid-flow `Background()` / `TODO()` calls at commit-time.

**Primary citation**: [Go blog: Contexts and structs](https://go.dev/blog/context-and-structs) — published 2021-01-14, retrieved 2026-04-30.

## Design rationale

- **Severity: error, not warning.** Mid-flow `Background()` always causes resource leaks or deadline ignorance. No legitimate use cases.
- **Scope: `internal/` only.** Library code must respect caller context. `cmd/` (program root) is exempt because `main()` *must* create root contexts.
- **Exemptions for goroutine roots.** If a file contains `signal.NotifyContext(...)` (for signal handling) or `func main(...)` (program root), V35 is disabled. These are legitimate `Background()` sites.
- **Non-test only.** Test code (`*_test.go`) is exempt. Tests often create isolated contexts for unit testing; enforcing propagation there adds noise.
- **Early detection via annotation.** If a developer has a legitimate reason (e.g., a background job scheduler that intentionally isolates context), they can opt-out via a `// v35:exempt` comment above the call.

## How it checks (implementation plan)

Lives in `hooks/validators/context_propagation.py`.

### Top-level

```python
def validate_file(self, file_path, ctx):
    if not self._is_eligible(file_path):
        return []
    return self._check_mid_flow_background(file_path, ctx)

def _is_eligible(self, file_path: Path) -> bool:
    """Skip tests, generated files, and cmd."""
    path_str = str(file_path)
    return (
        file_path.suffix == ".go"
        and "/internal/" in path_str
        and not file_path.name.endswith("_test.go")
        and "/gen/" not in path_str
    )

def _has_exemption(self, file_path: Path) -> bool:
    """Check for goroutine-root indicators."""
    text = file_path.read_text()
    # Program root or signal-based goroutine root
    return (
        "signal.NotifyContext(" in text or
        "func main(" in text
    )
```

### `_check_mid_flow_background(file_path, ctx)` — V35-MID-FLOW-BACKGROUND-CTX

```python
BACKGROUND_CALL = re.compile(
    r"context\.Background\(\)|context\.TODO\(\)",
)

EXEMPT_MARKER = re.compile(r"//\s*v35:exempt")

lines = file_path.read_text().splitlines(keepends=True)

if self._has_exemption(file_path):
    return  # File is exempt (main.go or signal.NotifyContext)

for i, line in enumerate(lines):
    if BACKGROUND_CALL.search(line):
        # Check for exemption marker on preceding line
        if i > 0 and EXEMPT_MARKER.search(lines[i - 1]):
            continue
        
        yield Finding(
            rule="V35-MID-FLOW-BACKGROUND-CTX",
            file=str(file_path),
            line=i + 1,
            message=(
                "mid-flow context.Background() ignores caller's deadline; "
                "use caller's ctx or annotate // v35:exempt"
            ),
        )
```

## Could be more effective

- **AST-based function-entry detection.** Regex can't reliably find function boundaries. Walking the AST would identify the enclosing function name and verify whether it's exported (should take `ctx`) or internal.
- **Signature analysis.** If a function doesn't have a `ctx context.Context` parameter, it can't propagate. The check could suggest adding one.
- **Goroutine analysis.** Detect `go func() { context.Background() ... }()` patterns and allow them (goroutines legitimately start new contexts).
- **Config knob for exemptions.** Instead of per-line comments, allow a project config: `context_propagation.allowed_patterns: ["backgroundJob", "cron"]` to exempt matching function names.

## References

- [Go blog: Contexts and structs](https://go.dev/blog/context-and-structs) — published 2021-01-14, retrieved 2026-04-30. Explains context propagation and when to use `Background()`.
- [pkg.go.dev: context.Background](https://pkg.go.dev/context#Background) — continuously updated, retrieved 2026-04-30. Official documentation.
- [Go blog: Context](https://go.dev/blog/context) — published 2014-10-29, retrieved 2026-04-30. Foundational context design.
- [Cloudflare: Context timeouts in Go](https://blog.cloudflare.com/the-complete-guide-to-golang-net-http-timeouts/) — published 2016-08-26, retrieved 2026-04-30. Real-world timeout propagation patterns.

## Examples

### ✓ Pass

```go
// server/internal/finance/minio_pdf_renderer.go
func (r *PDFRenderer) Render(ctx context.Context, cmf *CMF) ([]byte, error) {
    ctx, cancel := context.WithTimeout(ctx, 60*time.Second)  // ✓ propagate caller's ctx
    defer cancel()
    
    resp, err := r.s3Client.GetObject(ctx, bucket, key)
    // ...
}
```

```go
// server/cmd/scheduler/main.go
func main() {
    ctx := context.Background()  // ✓ program root, OK
    
    job := NewScheduler(ctx)
    job.Start()
}
```

```go
// server/internal/jobs/background_worker.go
// v35:exempt
func (w *Worker) startBackgroundTask() {
    ctx := context.Background()  // ✓ annotated exemption
}
```

### ✗ Fail

```go
// server/internal/finance/minio_pdf_renderer.go
func (r *PDFRenderer) Render(ctx context.Context, cmf *CMF) ([]byte, error) {
    ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
                                  // ↑ → V35-MID-FLOW-BACKGROUND-CTX
    defer cancel()
    // Ignores caller's 5s deadline, S3 conn holds for 60s
}
```

```go
// server/internal/users/repository.go
func (r *UserRepo) GetByID(ctx context.Context, id string) (*User, error) {
    ctx, cancel := context.WithTimeout(context.TODO(), 10*time.Second)
                                      // ↑ → V35-MID-FLOW-BACKGROUND-CTX
    defer cancel()
}
```
