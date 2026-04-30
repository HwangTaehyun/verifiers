# V50 — health-endpoint-split

> **Owner**: `hooks/validators/health_endpoint_split.py` (planned, not yet implemented)
> **Tier**: 3 (Stop — critical for production availability)
> **File patterns**: `server/cmd/**/*.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V50-HEALTH-NOT-SPLIT` | error | Server `cmd/` has no separate `/livez` and `/readyz` route handlers, **OR** the `/readyz` handler doesn't reference a database ping operation. |

## Why this verifier exists

Kubernetes uses two distinct probes: **liveness** (is the process alive?) and **readiness** (can the process handle traffic?). Conflating them into a single `/health` endpoint causes cascading failures.

Evidence: `server/cmd/server/main.go:61-64` defines a single `mux.HandleFunc("/health", ...)` that returns HTTP 200 unconditionally with no database connectivity check. K8s deployment maps this endpoint to both `livenessProbe` and `readinessProbe`. When the database goes down:
- The liveness probe (against the honest `/health` endpoint) correctly detects the pod is unhealthy and initiates a restart.
- Users continue sending requests to the pod, which fails because it can't reach the database.
- The restart cycle continues indefinitely without the pod ever draining in-flight requests.

Result: multi-minute outage with pod thrashing and connection errors.

Split endpoints solve this: `/livez` (always 200, no dependencies) and `/readyz` (returns 503 if DB is down). Liveness stays up; readiness drops, load balancers route traffic away. When the DB recovers, readiness returns 200 and traffic resumes.

V50 catches this at commit-time: both endpoints must exist, and `/readyz` must probe a downstream dependency.

## Design rationale

- **Error, not warning.** Health-check behavior directly impacts production stability. A single `/health` endpoint is a known failure mode; it's not a style preference.
- **Both endpoints are required.** Liveness and readiness are orthogonal concepts. A pod can be alive (process running) but not ready (database down). V50 requires both explicit routes.
- **Separate handlers, not one that responds to multiple paths.** A single handler with `if path == "/livez"` is fragile — a refactor could accidentally merge them. V50 looks for distinct handler registrations.
- **Readiness probe **must** check a dependency.** A readiness handler that returns 200 unconditionally is useless. The check is simple: if the `/readyz` handler code imports `pgx` (or similar database driver) and calls `.Ping()`, the intent is clear. This is not airtight (a handler could import pgx and not call Ping), but it catches the obvious cases.
- **No external health checks required.** V50 doesn't validate the actual probe response; it just verifies the endpoints exist and have intent. The K8s configuration is assumed to be correct.

## How it checks (implementation plan)

Lives in `hooks/validators/health_endpoint_split.py`.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    
    has_livez, has_readyz = self._check_endpoints_exist(ctx)
    if not has_livez:
        findings.append(Finding(rule="V50-HEALTH-NOT-SPLIT", ...))
    if not has_readyz:
        findings.append(Finding(rule="V50-HEALTH-NOT-SPLIT", ...))
    
    if has_readyz:
        has_db_ping = self._check_readyz_has_db_check(ctx)
        if not has_db_ping:
            findings.append(Finding(rule="V50-HEALTH-NOT-SPLIT", ...))
    
    return findings
```

### `_check_endpoints_exist(ctx)` — Route discovery

```python
def _check_endpoints_exist(self, ctx):
    """Check if /livez and /readyz routes are registered."""
    has_livez = False
    has_readyz = False
    
    HANDLER_PATTERNS = [
        r"\.HandleFunc\s*\(\s*['\"](/livez|/readyz)['\"]",      # mux.HandleFunc
        r"\.Handle\s*\(\s*['\"](/livez|/readyz)['\"]",           # mux.Handle
        r"router\.POST\s*\(\s*['\"](/livez|/readyz)['\"]",       # chi.Router.POST
        r"\.POST\s*\(\s*['\"](/livez|/readyz)['\"]",              # http.Router variant
        r"\.GET\s*\(\s*['\"](/livez|/readyz)['\"]",               # http.Router variant
    ]
    
    for cmd_file in ctx.server_cmd_dir.rglob("*.go"):
        text = cmd_file.read_text()
        for pattern in HANDLER_PATTERNS:
            for m in re.finditer(pattern, text):
                if "/livez" in m.group(0):
                    has_livez = True
                if "/readyz" in m.group(0):
                    has_readyz = True
    
    return has_livez, has_readyz
```

### `_check_readyz_has_db_check(ctx)` — Dependency verification

```python
def _check_readyz_has_db_check(self, ctx):
    """Check if /readyz handler references database operations."""
    db_operations = ("Ping", "Query", "Exec", "Select", "BeginTx")
    db_packages = ("pgx", "sql/driver", "database/sql")
    
    for cmd_file in ctx.server_cmd_dir.rglob("*.go"):
        text = cmd_file.read_text()
        
        # Find the /readyz handler
        readyz_match = re.search(
            r"(?:HandleFunc|Handle|router\.(?:GET|POST))\s*\(\s*['\"]/ readyz['\"][^)]*,\s*func\s*\([^)]*\)\s*\{",
            text,
            re.VERBOSE
        )
        if not readyz_match:
            continue
        
        # Extract handler body (next ~500 chars or until closing brace)
        start = readyz_match.end()
        body = text[start : start + 500]
        
        # Check for DB package import in file
        has_db_import = any(pkg in text for pkg in db_packages)
        if not has_db_import:
            continue
        
        # Check for DB operation call in handler
        for op in db_operations:
            if op in body:
                return True  # Found a DB check
    
    return False
```

### Could be more effective

- **Validate K8s probe configuration.** V50 currently checks the server code; it could also read `deployment.yaml` / `pod.yaml` and verify that `livenessProbe.httpGet.path == /livez` and `readinessProbe.httpGet.path == /readyz`. This would catch misconfigurations where the server split the endpoints but K8s is still using `/health`.
- **Check graceful shutdown behavior.** When a readiness check fails, in-flight requests should be allowed to complete. V50 could verify that the server has a graceful shutdown handler (e.g., listening on `SIGTERM` and draining requests).
- **Readiness timeout validation.** A readiness probe that takes 30 seconds to respond defeats the purpose of quick load-balancer routing. V50 could verify that the DB ping has a short timeout (< 2 seconds).
- **Liveness must not depend on externals.** A liveness handler that checks the database is a mistake — if the database is down, liveness will return 503, causing pod restarts when the pod is actually fine. V50 could verify that `/livez` has **no** DB checks.
- **Per-dependency readiness signals.** A service with multiple dependencies (DB, Redis, S3) could have fine-grained readiness. V50 could check for `GET /readyz/db`, `GET /readyz/cache`, etc.

## References

- [Kubernetes — Configure Liveness, Readiness, and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) — Kubernetes Documentation, continuously updated, retrieved 2026-04-30. The distinction between liveness and readiness probes.
- [Google SRE Workbook — Managing Load](https://sre.google/workbook/managing-load/) — Google, continuously updated, retrieved 2026-04-30. The operational rationale for splitting health checks.
- [Cloudflare Blog — Graceful shutdown of Go servers](https://blog.cloudflare.com/graceful-shutdown-of-go-servers/) — Cloudflare, published 2014-06, retrieved 2026-04-30. Best practices for health checks and graceful shutdown.
- [Connect-RPC — Health check service](https://connectrpc.com/docs/protocol/#health-check-service) — Connect Authors, continuously updated, retrieved 2026-04-30. If using Connect gRPC, health check standard.

## Examples

### ✓ Pass

```go
// server/cmd/server/main.go
func main() {
    mux := http.NewServeMux()
    
    // Liveness: always 200 (no dependencies)
    mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        w.Write([]byte("alive"))
    })
    
    // Readiness: 200 if DB is healthy, 503 otherwise
    mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
        ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
        defer cancel()
        
        if err := db.Ping(ctx); err != nil {
            w.WriteHeader(http.StatusServiceUnavailable)
            w.Write([]byte("database unreachable"))
            return
        }
        w.WriteHeader(http.StatusOK)
        w.Write([]byte("ready"))
    })
    
    http.ListenAndServe(":8080", mux)
}
```

```yaml
# deployment.yaml
spec:
  containers:
  - name: server
    livenessProbe:
      httpGet:
        path: /livez
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 10
    readinessProbe:
      httpGet:
        path: /readyz
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 5
```

### ✗ Fail

```go
// server/cmd/server/main.go (single endpoint)
func main() {
    mux := http.NewServeMux()
    
    mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        w.Write([]byte("OK"))
    })
    
    // (no /livez or /readyz split)
    // → V50-HEALTH-NOT-SPLIT
    
    http.ListenAndServe(":8080", mux)
}
```

```go
// server/cmd/server/main.go (endpoints exist but /readyz has no DB check)
func main() {
    mux := http.NewServeMux()
    
    mux.HandleFunc("/livez", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
    })
    
    mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)  // Always 200, no DB ping
    })
    
    // → V50-HEALTH-NOT-SPLIT
    // (readyz exists but doesn't check dependencies)
    
    http.ListenAndServe(":8080", mux)
}
```
