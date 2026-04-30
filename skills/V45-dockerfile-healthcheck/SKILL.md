# V45 — dockerfile-healthcheck

> **Owner**: `hooks/validators/dockerfile_healthcheck.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `**/Dockerfile*`, `**/*.Dockerfile`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V45-DOCKERFILE-NO-HEALTHCHECK` | warning | The final stage (or only stage) of a Dockerfile declares `EXPOSE` (= HTTP service) but has no `HEALTHCHECK` instruction. |

## Why this verifier exists

HTTP services without health checks silently fail in orchestration:

1. **No automatic restart.** Docker Compose and Kubernetes rely on HEALTHCHECK to know when a container is alive. Without it, a service that hangs or enters a broken state stays "running" indefinitely, silently failing requests.
2. **Cascading upstream failures.** A dead container in a service mesh (Istio, Linkerd) or behind a load balancer (nginx, HAProxy) continues receiving traffic because the orchestrator has no signal to remove it. Downstream services time out waiting for the dead instance.
3. **Compliance gap.** Medical and fintech audit logs require evidence that service liveness is monitored. A Dockerfile without HEALTHCHECK shows no such control.

V45 enforces that every HTTP-service Dockerfile includes a HEALTHCHECK instruction, ensuring the orchestrator can detect and recover from service degradation automatically.

Evidence: `server/docker/server.Dockerfile:83` — `ENTRYPOINT ["./server"]` followed by no `HEALTHCHECK`. The Go server exposes `/health` endpoint (used by `e2e.yml:65` for readiness probes) but the Dockerfile doesn't wire automatic restart via Docker's health check. Same gap in `finance-outbound-worker.Dockerfile`, `hasura.Dockerfile`, `web/Dockerfile` (verified at `/Users/taehyun/github/ai-project-template/server/docker/server.Dockerfile`, etc.).

## Design rationale

- **HEALTHCHECK is only mandatory for services with EXPOSE.** A worker Dockerfile (background job, no HTTP) doesn't need HEALTHCHECK. The rule applies only if the final stage has `EXPOSE` (= exposes a port = HTTP or gRPC service).
- **Heuristic over AST.** V45 uses regex to find EXPOSE and HEALTHCHECK instructions. A more precise AST parse could find language-specific health-check idioms (e.g., Go's `http.HandleFunc("/health", ...)`); not implemented.
- **Standard liveness check is curl.** The typical pattern is `HEALTHCHECK CMD curl -f http://localhost:7778/health || exit 1`. Alternative protocols (gRPC, custom TCP) are accepted if HEALTHCHECK is present.
- **Timings are advisory, not enforced.** `--interval=30s --timeout=5s --retries=3` is a reasonable default. V45 just checks for presence; tuning is a deployment concern.
- **Workers and background jobs are exempt.** A `finance-outbound-worker.Dockerfile` with no EXPOSE is legitimately a non-HTTP service; V45 doesn't flag it.

## How it checks (implementation plan)

Lives in `hooks/validators/dockerfile_healthcheck.py`.

### Top-level

```python
def validate_file(self, ctx, file_path: Path):
    if not self._is_dockerfile(file_path):
        return
    
    findings = []
    findings.extend(self._check_healthcheck(file_path))
    return findings

def _is_dockerfile(self, path):
    """Check if file is a Dockerfile."""
    name = path.name
    return name == "Dockerfile" or name.endswith(".Dockerfile")
```

### `_check_healthcheck(file_path)` — V45-DOCKERFILE-NO-HEALTHCHECK

```python
def _check_healthcheck(self, file_path):
    """Check final stage for EXPOSE + HEALTHCHECK."""
    text = file_path.read_text()
    lines = text.splitlines()
    
    # Find all stages (FROM lines with or without AS)
    stages = self._find_stages(text)
    if not stages:
        return
    
    # The final stage is the one after the last FROM
    final_from_idx = stages[-1]["line_no"] - 1  # 0-indexed
    final_stage = text[text.find("\n", text.find("FROM", 
                                   sum(len(l) + 1 for l in lines[:final_from_idx])))]
    
    # Extract final stage text (from final FROM to EOF)
    final_stage_text = "\n".join(lines[final_from_idx:])
    
    # Check if final stage has EXPOSE
    has_expose = re.search(r"^\s*EXPOSE\s+", final_stage_text, re.MULTILINE)
    if not has_expose:
        # No EXPOSE — not an HTTP service; skip
        return
    
    # Check if final stage has HEALTHCHECK
    has_healthcheck = re.search(r"^\s*HEALTHCHECK\s+", final_stage_text, re.MULTILINE)
    if not has_healthcheck:
        yield Finding(
            rule="V45-DOCKERFILE-NO-HEALTHCHECK",
            file=str(file_path),
            line=final_from_idx + 1,
            message="Final stage has EXPOSE but no HEALTHCHECK"
        )

def _find_stages(self, dockerfile_text):
    """Find all FROM lines (stages) in Dockerfile."""
    stages = []
    for line_no, line in enumerate(dockerfile_text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("FROM "):
            stages.append({"line_no": line_no, "text": stripped})
    return stages
```

### Refinement: extract final stage more robustly

```python
def _extract_final_stage(self, dockerfile_text):
    """Extract text of final stage from last FROM to EOF."""
    lines = dockerfile_text.splitlines()
    
    last_from = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("FROM "):
            last_from = i
    
    if last_from < 0:
        return ""
    
    return "\n".join(lines[last_from:])
```

### Could be more effective

- **Validate HEALTHCHECK syntax.** A malformed HEALTHCHECK (e.g., `HEALTHCHECK CMD /nonexistent/binary`) would still pass. Could validate the CMD exists in the image.
- **Language-specific health endpoints.** Detect if the service source code declares a `/health` endpoint (Go's `http.HandleFunc`, Python's `@app.route("/health")`, etc.) and auto-suggest the HEALTHCHECK command.
- **Gated by orchestration target.** A Dockerfile meant for local `docker compose` may not need HEALTHCHECK; Kubernetes pods do. Could add a config knob `v45.require_healthcheck: kubernetes-only`.
- **Health check start-up delay.** Services with long startup times (database migrations, etc.) need `--start-period=60s` or higher. Could warn if HEALTHCHECK lacks start-period.
- **Multi-port services.** A service exposing both `:8080` (HTTP) and `:50051` (gRPC) needs health checks for both. Currently V45 just checks one HEALTHCHECK instruction exists.

## References

- [Docker — HEALTHCHECK instruction](https://docs.docker.com/reference/dockerfile/#healthcheck) — Docker, *continuously updated*, retrieved 2026-04-30. Official syntax and behavior of HEALTHCHECK in Dockerfiles.
- [CIS Docker Benchmark v1.6 — 4.6: Add HEALTHCHECK to the container image](https://www.cisecurity.org/benchmark/docker) — CIS, *published 2023-09*, retrieved 2026-04-30. Industry security standard requiring health checks in containerized services.
- [Docker Compose — healthcheck](https://docs.docker.com/compose/compose-file/compose-file-v3/#healthcheck) — Docker, *continuously updated*, retrieved 2026-04-30. How Docker Compose interprets HEALTHCHECK and uses it for service orchestration.
- [Kubernetes — Liveness, Readiness, and Startup Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/) — Kubernetes, *continuously updated*, retrieved 2026-04-30. Kubernetes equivalent of Docker HEALTHCHECK; readiness probes reference application-level health endpoints.
- [Docker — Health checks for running containers](https://docs.docker.com/reference/dockerfile/#healthcheck-1) — Docker, *continuously updated*, retrieved 2026-04-30. Implementation details and common patterns for HEALTHCHECK commands.

## Examples

### ✓ Pass

```dockerfile
# server/docker/server.Dockerfile — service with HEALTHCHECK
FROM golang:1.25-bookworm AS builder
RUN go build -o /app/server .

FROM debian:bookworm-slim
COPY --from=builder /app/server /usr/local/bin/server
EXPOSE 7778
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:7778/health || exit 1
ENTRYPOINT ["server"]
```

```dockerfile
# Worker without EXPOSE — exempted from HEALTHCHECK check
FROM golang:1.25-bookworm
COPY . .
RUN go build -o worker .
# No EXPOSE — not an HTTP service
# V45 does not flag this ✓
ENTRYPOINT ["./worker"]
```

```dockerfile
# Custom health check (gRPC)
FROM golang:1.25-bookworm
COPY . .
RUN go install github.com/grpc-ecosystem/grpc-health-probe/cmd/grpc_health_probe@latest
RUN go build -o /app/server .
EXPOSE 50051
HEALTHCHECK --interval=30s --timeout=5s CMD grpc_health_probe -addr localhost:50051
ENTRYPOINT ["/app/server"]
```

### ✗ Fail

```dockerfile
# server/docker/server.Dockerfile — EXPOSE without HEALTHCHECK
FROM golang:1.25-bookworm
RUN go build -o /app/server .
EXPOSE 7778
ENTRYPOINT ["/app/server"]
# → V45-DOCKERFILE-NO-HEALTHCHECK
#   (EXPOSE present, HEALTHCHECK missing)
```

```dockerfile
# Multi-stage: final stage has EXPOSE but no HEALTHCHECK
FROM golang:1.25-bookworm AS builder
RUN go build .

FROM alpine
COPY --from=builder /app/server /server
EXPOSE 8080
# → V45-DOCKERFILE-NO-HEALTHCHECK
#   (final stage EXPOSE, no HEALTHCHECK)
```

```dockerfile
# HEALTHCHECK in intermediate stage (not final) — doesn't count
FROM golang:1.25 AS builder
HEALTHCHECK CMD echo ok
RUN go build .

FROM alpine
COPY --from=builder /app /app
EXPOSE 3000
# → V45-DOCKERFILE-NO-HEALTHCHECK
#   (HEALTHCHECK was in builder stage, not final stage)
```
