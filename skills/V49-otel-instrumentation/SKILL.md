# V49 — otel-instrumentation

> **Owner**: `hooks/validators/otel_instrumentation.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `**/go.mod`, `server/cmd/**/*.go`, `server/internal/**/*.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V49-NO-OTEL` | warning | `server/go.mod` lacks `go.opentelemetry.io/otel` in the `require` block, **OR** no `.go` file under `server/cmd/` imports `otelhttp`. |

## Why this verifier exists

Distributed tracing and metrics provide visibility into production behavior: which database queries are slow, which Connect-RPC handlers take longest, where requests spend time. Without OpenTelemetry instrumentation, performance problems are invisible until they cause outages.

Evidence: `server/go.mod` has no `go.opentelemetry.io/otel*` import. `grep -rn "otel\|otelhttp\|otelpgx" server/internal/` returns only one placeholder comment: `interceptors/interceptors.go:171` — `// metrics interceptor not yet implemented`. The intent existed; implementation never landed. Production has no distributed tracing: DB query hotspots and Connect-RPC handler latency are invisible.

V49 flags the absence and prompts the team to add basic OTEL instrumentation before the service scales.

## Design rationale

- **Warning, not error.** Some projects may defer observability to a later phase. The flag alerts; enforcement is optional.
- **Two-part check: module + import.** Just having `go.opentelemetry.io/otel` in `go.mod` isn't enough — many projects add the dependency but forget to import and wrap handlers. V49 checks both: (a) the dependency is declared, **and** (b) at least one handler is actually instrumented via `otelhttp` import.
- **`otelhttp` is the HTTP-layer instrumentation.** For a Connect-RPC / mux-based server, `connectrpc.com/connect/otelconnect` (if available) or wrapping the mux with `otelhttp.NewHandler` is standard. V49 looks for `otelhttp` as a proxy for "HTTP instrumentation exists".
- **Database instrumentation is separate.** `github.com/exaring/otelpgx` (for pgx/v5) is a second dependency for query tracing. V49 doesn't enforce it; a future `V50-OTEL-DB` could. This check focuses on the client-facing layer (handlers).
- **No version pinning.** V49 doesn't care which version of OTEL is used, only that the module exists and is imported.

## How it checks (implementation plan)

Lives in `hooks/validators/otel_instrumentation.py`.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    findings.extend(self._check_otel_module(ctx))
    findings.extend(self._check_otel_import(ctx))
    return findings
```

### `_check_otel_module(ctx)` — First part of V49-NO-OTEL

```python
def _check_otel_module(self, ctx):
    """Check if server/go.mod has go.opentelemetry.io/otel."""
    go_mod = ctx.server_dir / "go.mod"
    if not go_mod.exists():
        return []  # Not a Go project, skip
    
    text = go_mod.read_text()
    
    # Check for any otel import in the require block
    if re.search(r"go\.opentelemetry\.io/otel", text):
        return []  # Found
    
    return [Finding(
        rule="V49-NO-OTEL",
        file=str(go_mod),
        line=1,
        message="go.opentelemetry.io/otel dependency not found in go.mod"
    )]
```

### `_check_otel_import(ctx)` — Second part of V49-NO-OTEL

```python
def _check_otel_import(self, ctx):
    """Check if any cmd/*.go file imports otelhttp."""
    cmd_dir = ctx.server_dir / "cmd"
    if not cmd_dir.exists():
        return []
    
    found_otelhttp = False
    for go_file in cmd_dir.rglob("*.go"):
        if "otelhttp" in go_file.read_text():
            found_otelhttp = True
            break
    
    if found_otelhttp:
        return []
    
    # Also check for otelconnect (alternative for Connect-RPC)
    for go_file in cmd_dir.rglob("*.go"):
        if "otelconnect" in go_file.read_text():
            found_otelhttp = True
            break
    
    if found_otelhttp:
        return []
    
    return [Finding(
        rule="V49-NO-OTEL",
        file=str(cmd_dir),
        line=1,
        message="No otelhttp or otelconnect import found in server/cmd/*.go"
    )]
```

### Could be more effective

- **Verify OTEL export configuration.** Having the import is not enough; the OTEL SDK must be initialized with an exporter (e.g., `go.opentelemetry.io/exporters/otlp/otlptrace/otlptracehttp`). V49 could check for exporter package imports and `NewOTLPTraceExporter()` calls.
- **Sampler configuration validation.** A tracer without a sampler that sends 100% of traces will OOM. V49 could verify that a sampler is configured (e.g., `AlwaysSample` for dev, `ParentBased(TraceIDRatioBased(0.01))` for prod).
- **Service name and attributes.** OTEL requires a service name and resource attributes for identification in the collector. V49 could check for `semconv.ServiceNameKey` or similar.
- **Instrumentation package coverage.** Besides `otelhttp`, a production service typically needs `otelpgx` (database), possibly `otelsql` (for sql.DB), and gRPC's built-in OTEL support. A matrix check could verify all layers.
- **Environment variable support.** OTEL can be configured via `OTEL_*` env vars. V49 could verify that the app respects `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_EXPORTER`, and `OTEL_SAMPLER_ARG` for flexibility.

## References

- [OpenTelemetry — Getting Started (Go)](https://opentelemetry.io/docs/languages/go/getting-started/) — OpenTelemetry, continuously updated, retrieved 2026-04-30. The quickstart for Go OTEL setup.
- [otelhttp — Instrumenting HTTP servers](https://pkg.go.dev/go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp) — OpenTelemetry, continuously updated, retrieved 2026-04-30. The HTTP middleware package.
- [exaring/otelpgx — pgx instrumentation](https://github.com/exaring/otelpgx) — exaring, continuously developed since 2022-01, retrieved 2026-04-30. PostgreSQL query tracing.
- [OpenTelemetry — Sampling](https://opentelemetry.io/docs/concepts/sampling/) — OpenTelemetry, continuously updated, retrieved 2026-04-30. Why sampling matters for production.

## Examples

### ✓ Pass

```go
// server/cmd/server/main.go
import (
    "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

func main() {
    // ... setup ...
    mux := http.NewServeMux()
    wrappedMux := otelhttp.NewHandler(mux, "server")
    // ... register routes on mux ...
    http.ListenAndServe(":8080", wrappedMux)
}
```

```go
// Alternative with Connect-RPC
import (
    otelconnect "connectrpc.com/otel"
)

func main() {
    mux := http.NewServeMux()
    interceptor := otelconnect.NewInterceptor()
    // ... register handlers with interceptor ...
}
```

```
# server/go.mod
require (
    go.opentelemetry.io/otel v1.14.0
    go.opentelemetry.io/otel/trace v1.14.0
    go.opentelemetry.io/exporters/otlp/otlptrace/otlptracehttp v1.14.0
)
```

### ✗ Fail

```
# server/go.mod (no otel import)
require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
    // go.opentelemetry.io/otel not present
)
# → V49-NO-OTEL
```

```go
// server/cmd/server/main.go (no otelhttp import)
import (
    "net/http"
)

func main() {
    mux := http.NewServeMux()
    // ... setup routes ...
    http.ListenAndServe(":8080", mux)  // No instrumentation
    // → V49-NO-OTEL
}
```
