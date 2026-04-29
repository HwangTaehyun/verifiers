# V05 — docker

> **Owner**: `hooks/validators/docker_compose.py`
> **Tier**: 2 (PostToolUse) and 3 (Stop) — same project-wide compose + Dockerfile sweep on both. The Phase29 split delegates `validate_file` → `validate_project` while waiting for true per-file optimization (a Phase33+ follow-up).
> **File patterns**: `**/docker-compose*.yaml`, `**/docker-compose*.yml`, `**/Dockerfile*`, `**/*.Dockerfile`

## Rules (grouped)

### Compose-file rules

| Rule | Severity | When |
|---|---|---|
| `V05-PORT-CONFLICT` | error | Two services in the same compose file map the same host port. |
| `V05-VHOST-NO-NETWORK` | error | Service has `VIRTUAL_HOST` env but is not on any of `docker.reverse_proxy_networks` (default `["nginx-proxy"]`). |
| `V05-UNDEFINED-NETWORK` | error | Service references a network that's not declared at the top-level `networks:` (and not marked `external: true`). |
| `V05-MISSING-HEALTHCHECK` | warning | A `depends_on: { x: { condition: service_healthy }}` exists, but service `x` has no `healthcheck:`. |
| `V05-MISSING-ENV-VAR` | warning | `${VAR}` is referenced without a `${VAR:-default}` fallback and is absent from `.env*` and `environment:` blocks. |

### Dockerfile rules

| Rule | Severity | When |
|---|---|---|
| `V05-DOCKERFILE-NO-USER` | warning | A production-classified stage runs as root (no `USER` directive). |
| `V05-DOCKERFILE-NO-EXPOSE` | warning | A production stage has no `EXPOSE`. |
| `V05-DOCKERFILE-COPY-ALL` | warning | `COPY . .` (or `ADD . .`) in a context with no `.dockerignore` — risk of leaking `.env`, `.git/`, secrets. |
| `V05-DOCKERFILE-LATEST-TAG` | warning | `FROM image:latest` (non-pinned). |
| `V05-DOCKERIGNORE-MISSING` | warning | `.dockerignore` does not exist next to the Dockerfile. |

### Production-mode rules (filename matches `*production*` / `*prod*`)

| Rule | Severity | When |
|---|---|---|
| `V05-PROD-PORT-EXPOSED` | error | A production compose file exposes ports directly (should go through nginx-proxy / Traefik). |
| `V05-PROD-DEV-MODE` | error | Production compose has `develop:` / `command: dev` / hot-reload volume mounts. |
| `V05-PROD-WILDCARD-CORS` | error | Production env has `CORS_*=*` or `Access-Control-Allow-Origin=*`. |
| `V05-PROD-NO-TRAEFIK-LABELS` | warning | Production compose has neither nginx-proxy `VIRTUAL_HOST` nor Traefik `traefik.http.routers.*` labels. |
| `V05-PROD-NO-RESOURCE-LIMITS` | warning | Production service has no `deploy.resources.limits.{memory,cpus}`. |

### Dev-override rules (filename matches `*override*` / dev compose)

| Rule | Severity | When |
|---|---|---|
| `V05-DEV-NO-VOLUME-MOUNT` | warning | Dev override service has no source volume mount (hot reload broken). |
| `V05-DEV-NO-BUILD-TARGET` | warning | Dev override has no `build.target: dev` (multi-stage Dockerfile dev stage not selected). |

## Why this verifier exists

Docker is the layer where small misconfigurations become production incidents:

- **Port conflict** → only the first service binds; the rest silently exit.
- **Healthcheck missing** + `depends_on: condition: service_healthy` → the dependent service blocks forever (no health = never healthy).
- **VHOST without nginx-proxy network** → the proxy can't see the service; users get 503.
- **`COPY . .` without `.dockerignore`** → `.env` and `.git/` end up in the final image. Single biggest secret-leak vector in container deployments.
- **`:latest` tag** → builds become non-reproducible. Same Dockerfile builds different images week to week.
- **Production with dev mode flags** → `develop:` blocks, hot-reload mounts, `CORS=*` in production. Each one is a separate story-of-rage from the security team.

V05 codifies the Docker / Compose Spec / OWASP container best practices into hook-time checks so the regression dies before deploy.

## Design rationale

- **File-based prod / dev classification.** Filename pattern (`*production*` → prod, `*override*` → dev) is faster + more reliable than parsing a `mode:` key. The default classification is overridable via `docker.production_filename_patterns` / `docker.dev_filename_patterns` in `.verifiers/config.yaml` (see SecurityConfig pattern).
- **`vhost_check_mode` config knob (Phase21 BREAKING).** Default changed from `"all"` to `"production"`. The previous default flagged a local `docker-compose.yaml` that set `VIRTUAL_HOST` for production parity but had no `nginx-proxy` network — common false positive. Restore old behavior with `docker.vhost_check_mode: "all"`.
- **`reverse_proxy_networks` list, not single value.** Some projects route via Traefik instead of nginx-proxy; the override (`docker.reverse_proxy_networks: ["traefik"]`) is project-level config, not validator-level code.
- **Dangerous-DDL-style escape hatch is missing here.** Unlike V04's `-- INTENTIONAL:` for `DROP TABLE`, V05's "I really do want `:latest`" case has no comment-based bypass — the project-config knob is the only escape. This is intentional: pinning a tag costs ~10 seconds of effort, and the cost of a non-reproducible build years later is large.

## How it checks (implementation)

Lives in `hooks/validators/docker_compose.py`. The Phase29+ `validate_project` walks the entire project tree once and dispatches to ~14 small `_check_*` helpers. `validate_file` currently delegates to `validate_project`.

### Core sweep

```python
def validate_project(self, ctx) -> list[Finding]:
    self._docker_cfg = ctx.config.docker

    # 1. Discover compose files + Dockerfiles
    compose_files = list(ctx.project_root.glob("**/docker-compose*.yaml"))
    compose_files += list(ctx.project_root.glob("**/docker-compose*.yml"))
    compose_files = self._filter_excluded_files(ctx, compose_files)

    dockerfiles = list(ctx.project_root.glob("**/Dockerfile*"))
    dockerfiles += list(ctx.project_root.glob("**/*.Dockerfile"))
    dockerfiles = self._filter_excluded_files(ctx, dockerfiles)

    findings: list[Finding] = []

    # 2. Per compose file: parse YAML once, then run all rule helpers
    for compose_file in compose_files:
        try:
            data = yaml.safe_load(compose_file.read_text()) or {}
        except (yaml.YAMLError, OSError):
            continue
        findings.extend(self._check_port_conflicts(data, compose_file))
        findings.extend(self._check_virtual_host_network(data, compose_file))
        findings.extend(self._check_network_references(data, compose_file))
        findings.extend(self._check_depends_on_healthcheck(data, compose_file))
        findings.extend(self._check_env_var_references(ctx, data, compose_file))

        # Production-only rules (filename-classified)
        findings.extend(self._check_prod_port_exposed(data, compose_file))
        findings.extend(self._check_prod_dev_mode(data, compose_file))
        findings.extend(self._check_prod_wildcard_cors(data, compose_file))
        findings.extend(self._check_prod_traefik_labels(data, compose_file))
        findings.extend(self._check_prod_resource_limits(data, compose_file))

        # Dev-override-only rules
        findings.extend(self._check_dev_volume_mount(data, compose_file))
        findings.extend(self._check_dev_build_target(data, compose_file))

    # 3. Per Dockerfile
    for df in dockerfiles:
        findings.extend(self._check_dockerfile_multistage(df))
        findings.extend(self._check_dockerfile_user(df))
        findings.extend(self._check_dockerfile_expose(df))
        findings.extend(self._check_dockerfile_copy_all(ctx, df))
        findings.extend(self._check_base_image_latest(df))
        findings.extend(self._check_dockerignore_exists(df))

    # 4. Cross-file
    findings.extend(self._check_build_target_exists(compose_files))
    return findings
```

### Selected helpers

**Port conflict**:
```python
def _check_port_conflicts(self, data, compose_file):
    seen: dict[int, str] = {}
    for svc_name, svc in (data.get("services") or {}).items():
        for port_spec in svc.get("ports") or []:
            host_port = int(str(port_spec).split(":")[0].lstrip("\""))
            if host_port in seen and seen[host_port] != svc_name:
                yield Finding(rule="V05-PORT-CONFLICT", ...)
            seen[host_port] = svc_name
```

**VHOST + network**:
```python
def _check_virtual_host_network(self, data, compose_file):
    if self._docker_cfg.vhost_check_mode == "off":
        return
    if self._docker_cfg.vhost_check_mode == "production" \
       and not self._classify_production(compose_file):
        return
    proxy_nets = self._docker_cfg.reverse_proxy_networks
    for svc_name, svc in (data.get("services") or {}).items():
        env = svc.get("environment") or {}
        has_vhost = (
            "VIRTUAL_HOST" in env
            or any("VIRTUAL_HOST" in (e or "") for e in (env if isinstance(env, list) else []))
        )
        on_proxy = any(n in (svc.get("networks") or []) for n in proxy_nets)
        if has_vhost and not on_proxy:
            yield Finding(rule="V05-VHOST-NO-NETWORK", ...)
```

**Dockerfile `COPY . .` + `.dockerignore`**:
```python
def _check_dockerfile_copy_all(self, ctx, dockerfile):
    src = dockerfile.read_text()
    has_copy_all = re.search(r'^\s*(COPY|ADD)\s+\.\s+\.\s*$', src, re.MULTILINE)
    has_ignore = (dockerfile.parent / ".dockerignore").exists()
    if has_copy_all and not has_ignore:
        yield Finding(rule="V05-DOCKERFILE-COPY-ALL", ...)
```

### Could be more effective

- **`.dockerignore` *content* check.** Currently V05 only verifies the file exists. A real check would parse it and ensure `.env`, `.git`, `node_modules`, etc. are listed. Easy enhancement.
- **Multi-stage build hardening.** V05 doesn't enforce that a production stage be a minimal base (`alpine`, `distroless`, `scratch`) — `FROM ubuntu:22.04` for a Go binary is a wasted 70 MB of attack surface. A future V26 (Phase 27 audit) covers this directly.
- **`hadolint` integration.** Hadolint is the de-facto Dockerfile linter and catches ~50 patterns V05 doesn't. A future enhancement could shell out to `hadolint --format json` per Dockerfile and merge findings under `V05-HADOLINT-<rule>` like V03 does for `buf lint`.
- **Compose Spec validation.** `docker compose config --quiet` resolves variables and validates the schema; running this is a stronger check than YAML-parsing alone. Cheap to add — exit code becomes `V05-COMPOSE-INVALID`.
- **Per-file optimization.** Currently `validate_file` delegates to `validate_project`, scanning the whole tree on every Edit. A targeted version would skip everything except the just-edited compose / Dockerfile, plus its cross-references. Phase33+ follow-up.

## References

- [Compose Specification](https://compose-spec.io/) — Docker Inc. + community, *continuously updated*, retrieved 2026-04-30. The schema V05 parses and validates against.
- [Docker — Best practices for writing Dockerfiles](https://docs.docker.com/build/building/best-practices/) — Docker Inc., *continuously updated*, retrieved 2026-04-30. Source of the multi-stage / non-root / pinned-tag / `.dockerignore` rules.
- [OWASP Docker Top 10](https://owasp.org/www-project-docker-top-10/) — OWASP, *continuously updated*, retrieved 2026-04-30. Especially `D04 Add no setuid binaries`, `D02 Patch Your Images`, `D08 Avoid leaking secrets via `COPY``.
- [Snyk — 10 Docker image security best practices](https://snyk.io/blog/10-docker-image-security-best-practices/) — Snyk, *published 2024-08*, retrieved 2026-04-30. Source of the `.dockerignore` + non-root + minimal-base recommendations.
- [NIST SP 800-190 — Application Container Security Guide](https://csrc.nist.gov/publications/detail/sp/800-190/final) — NIST, *published 2017-09*, retrieved 2026-04-30. Authoritative source for the production hardening rules.
- [nginx-proxy / nginx-proxy](https://github.com/nginx-proxy/nginx-proxy) — Jason Wilder, *continuously maintained*, retrieved 2026-04-30. The `VIRTUAL_HOST` / network convention V05-VHOST-NO-NETWORK enforces.

## Examples

### ✓ Pass

```yaml
# server/docker-compose.yaml
services:
  api:
    image: myapp/api:1.4.2          # pinned tag
    networks: [internal, nginx-proxy]
    environment:
      VIRTUAL_HOST: api.example.com
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
    deploy:
      resources:
        limits: { memory: 512M, cpus: "0.5" }
networks:
  internal:
  nginx-proxy:
    external: true
```

```dockerfile
# multi-stage, non-root, pinned base, EXPOSE
FROM golang:1.25-alpine AS builder
WORKDIR /src
COPY . .
RUN go build -o /app ./cmd/server

FROM gcr.io/distroless/static:nonroot AS final
USER nonroot:nonroot
COPY --from=builder /app /app
EXPOSE 8080
ENTRYPOINT ["/app"]
```

### ✗ Fail

```yaml
services:
  api:
    ports: ["8080:8080"]
  worker:
    ports: ["8080:8080"]    # → V05-PORT-CONFLICT (error)
```

```yaml
# docker-compose.production.yaml
services:
  api:
    ports: ["8080:8080"]    # → V05-PROD-PORT-EXPOSED (error, prod must go through proxy)
    environment:
      CORS_ALLOWED_ORIGINS: "*"   # → V05-PROD-WILDCARD-CORS (error)
```

```dockerfile
FROM node:latest          # → V05-DOCKERFILE-LATEST-TAG (warning)
COPY . .                  # → V05-DOCKERFILE-COPY-ALL if no .dockerignore (warning)
# no USER directive       → V05-DOCKERFILE-NO-USER (warning)
```
