# V36 — go-http-server-hardening

> **Owner**: `hooks/validators/http_server_hardening.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse) — runs on `cmd/**/main.go` files
> **File patterns**: `server/cmd/**/main.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V36-NO-HTTP-TIMEOUTS` | error | An `&http.Server{...}` struct literal in `cmd/**/main.go` lacks both `ReadHeaderTimeout` and `WriteTimeout` fields. |
| `V36-NO-GRACEFUL-SHUTDOWN` | warning | An `http.Server` is created but the file lacks `signal.NotifyContext(...)` or manual `shutdown()` logic. |

## Why this verifier exists

`net/http.Server` defaults to zero timeouts (unlimited wait). This opens three attack vectors:

1. **Slowloris.** A client sends HTTP headers one byte per second. Server accepts all connections with no timeout; goroutine per connection × 10K slow clients = memory exhaustion.
2. **Idle connection exhaustion.** A client connects, sends nothing, waits forever. Server never closes idle connections; resources leak.
3. **Graceful shutdown race.** Server exits without waiting for in-flight requests to complete. Clients see TCP resets.

Example from `server/cmd/server/main.go:141-144`:

```go
server := &http.Server{
    Addr:    ":8080",
    Handler: mux,
    // ← No ReadHeaderTimeout, WriteTimeout, ReadTimeout, or IdleTimeout
    // ← Slowloris and idle-connection attacks possible
}
server.ListenAndServe()  // Blocks forever, no signal handling for graceful shutdown
```

Hardened version:

```go
ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
defer cancel()

server := &http.Server{
    Addr:              ":8080",
    Handler:           mux,
    ReadHeaderTimeout: 5 * time.Second,
    ReadTimeout:       30 * time.Second,
    WriteTimeout:      60 * time.Second,
    IdleTimeout:       120 * time.Second,
}

go func() {
    <-ctx.Done()
    server.Shutdown(ctx)
}()

if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
    log.Fatal(err)
}
```

V36 catches unprotected servers at commit-time.

**Primary citations**: 
- [net/http.Server documentation](https://pkg.go.dev/net/http#Server) — continuously updated, retrieved 2026-04-30.
- [Cloudflare: The complete guide to Go net/http timeouts](https://blog.cloudflare.com/the-complete-guide-to-golang-net-http-timeouts/) — published 2016-08-26, retrieved 2026-04-30.

## Design rationale

- **Severity: error for timeouts, warning for shutdown.** Missing timeouts is always a vulnerability. Missing graceful shutdown is best practice but sometimes intentional (e.g., CLI tools that exit cleanly anyway).
- **Minimum timeout values:**
  - `ReadHeaderTimeout: 5s` — reject HTTP/1.1 header spam quickly.
  - `ReadTimeout: 30s` — allow legitimate slow clients (mobile networks).
  - `WriteTimeout: 60s` — allow slow response streaming.
  - `IdleTimeout: 120s` — close idle connections.
- **Both ReadHeaderTimeout AND WriteTimeout required.** Just `ReadTimeout` allows slow header reads; just `WriteTimeout` allows slow idle-connection holds. Both together are defense-in-depth.
- **Shutdown detection is heuristic.** V36 looks for `signal.NotifyContext(...)` in the same file. If graceful shutdown is delegated to a separate utility (e.g., a `setupServer()` helper function), V36 may false-positive. Config knob recommended for project-wide exemptions.
- **No magic values enforced.** V36 only checks *presence* of timeout fields, not their magnitudes. A project may choose `ReadHeaderTimeout: 30s` instead of `5s` for legitimate reasons (e.g., large request bodies). Config is optional.

## How it checks (implementation plan)

Lives in `hooks/validators/http_server_hardening.py`.

### Top-level

```python
def validate_file(self, file_path, ctx):
    if not self._is_eligible(file_path):
        return []
    findings = []
    findings.extend(self._check_http_timeouts(file_path))
    findings.extend(self._check_graceful_shutdown(file_path))
    return findings

def _is_eligible(self, file_path: Path) -> bool:
    """Only cmd/**/main.go files."""
    path_str = str(file_path)
    return (
        file_path.name == "main.go"
        and "/cmd/" in path_str
        and "/gen/" not in path_str
    )
```

### `_check_http_timeouts(file_path)` — V36-NO-HTTP-TIMEOUTS

```python
HTTP_SERVER_LITERAL = re.compile(
    r"&http\.Server\s*\{",
    re.MULTILINE
)

TIMEOUT_FIELDS = (
    "ReadHeaderTimeout",
    "WriteTimeout",
    "ReadTimeout",
    "IdleTimeout",
)

def _extract_struct_literal(src, start_pos):
    """Extract from {, through matching }, return substring."""
    brace_depth = 0
    in_literal = False
    for i in range(start_pos, len(src)):
        if src[i] == "{":
            brace_depth += 1
            in_literal = True
        elif src[i] == "}" and in_literal:
            brace_depth -= 1
            if brace_depth == 0:
                return src[start_pos:i+1]
    return src[start_pos:start_pos+2000]  # Fallback to 2000 chars

src = file_path.read_text()

for match in HTTP_SERVER_LITERAL.finditer(src):
    literal = _extract_struct_literal(src, match.start())
    
    has_read_header = "ReadHeaderTimeout:" in literal
    has_write = "WriteTimeout:" in literal
    
    if not (has_read_header and has_write):
        line_no = src[:match.start()].count("\n") + 1
        yield Finding(
            rule="V36-NO-HTTP-TIMEOUTS",
            file=str(file_path),
            line=line_no,
            message=(
                "http.Server missing ReadHeaderTimeout and WriteTimeout; "
                "vulnerable to Slowloris attacks"
            ),
        )
```

### `_check_graceful_shutdown(file_path)` — V36-NO-GRACEFUL-SHUTDOWN

```python
SIGNAL_NOTIFY = re.compile(r"signal\.NotifyContext\s*\(")

src = file_path.read_text()

if not SIGNAL_NOTIFY.search(src):
    yield Finding(
        rule="V36-NO-GRACEFUL-SHUTDOWN",
        file=str(file_path),
        line=1,
        message=(
            "no signal.NotifyContext(...) for graceful shutdown; "
            "requests may be interrupted on stop"
        ),
    )
```

## Could be more effective

- **Config knobs for timeout values.** Allow projects to override default-acceptable values (e.g., longer timeouts for file-upload endpoints).
- **Graceful shutdown pattern matching.** Detect `server.Shutdown(ctx)` calls and `http.ErrServerClosed` checks, not just `signal.NotifyContext`. More permissive.
- **TLS-specific hardening.** Add checks for `tls.Config.MinVersion`, `CipherSuites` when HTTPS is detected.
- **Multiplexer scan.** Verify that all routes under the mux are registered (some may be dead code).
- **Rate-limiting.** Suggest middleware integration (e.g., `golang.org/x/time/rate`) for further DoS protection.

## References

- [net/http.Server](https://pkg.go.dev/net/http#Server) — continuously updated, retrieved 2026-04-30. Official timeout field documentation.
- [Cloudflare: The complete guide to Go net/http timeouts](https://blog.cloudflare.com/the-complete-guide-to-golang-net-http-timeouts/) — published 2016-08-26, retrieved 2026-04-30. Comprehensive timeout anatomy and best practices.
- [OWASP: Slowloris](https://owasp.org/www-community/attacks/Slowloris) — continuously updated, retrieved 2026-04-30. Attack vector overview.
- [Go issue: proposal - http: default Server timeouts](https://github.com/golang/go/issues/16100) — created 2016-07-01, retrieved 2026-04-30. Community discussion on why defaults are unsafe.

## Examples

### ✓ Pass

```go
// server/cmd/server/main.go
func main() {
    ctx, cancel := signal.NotifyContext(
        context.Background(),
        os.Interrupt, syscall.SIGTERM,
    )
    defer cancel()
    
    mux := http.NewServeMux()
    mux.HandleFunc("/health", healthHandler)
    
    server := &http.Server{
        Addr:              ":8080",
        Handler:           mux,
        ReadHeaderTimeout: 5 * time.Second,   // ✓ set
        ReadTimeout:       30 * time.Second,  // optional but good
        WriteTimeout:      60 * time.Second,  // ✓ set
        IdleTimeout:       120 * time.Second,
    }
    
    go func() {
        <-ctx.Done()
        server.Shutdown(context.Background())  // ✓ graceful shutdown
    }()
    
    if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
        log.Fatal(err)
    }
}
```

### ✗ Fail

```go
// server/cmd/server/main.go
func main() {
    mux := http.NewServeMux()
    
    server := &http.Server{
        Addr:    ":8080",
        Handler: mux,
        // ← No ReadHeaderTimeout, WriteTimeout
    }  // → V36-NO-HTTP-TIMEOUTS
    
    server.ListenAndServe()
    // ← No signal handling → V36-NO-GRACEFUL-SHUTDOWN
}
```

```go
// server/cmd/api/main.go
func main() {
    server := &http.Server{
        Addr:    ":3000",
        Handler: setupMux(),
        // Only partial timeouts set
        ReadTimeout: 30 * time.Second,
        // Missing WriteTimeout → V36-NO-HTTP-TIMEOUTS
    }
    
    server.ListenAndServe()
}
```
