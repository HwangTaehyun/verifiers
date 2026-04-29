# V26 — docker-prod

> **Owner**: `hooks/validators/docker_prod_hardening.py`
> **Tier**: 2 (PostToolUse) per-file when the edited compose is production-classified. 3 (Stop) sweeps every prod compose in the project.
> **File patterns**: `**/docker-compose*.yaml`, `**/docker-compose*.yml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V26-PROD-NO-RESOURCE-LIMITS` | warning | A service in a production compose has no `deploy.resources.limits.{memory,cpus}`. |
| `V26-PROD-NO-HEALTHCHECK` | error | Service A has `depends_on: { B: { condition: service_healthy }}` but service B has no `healthcheck:`. The dependent will block forever. |
| `V26-PROD-SECRET-BIND-MOUNT` | error | A service bind-mounts `.env`, `secrets/`, `*.pem`, `*.key`, `*.crt`, or `*.p12` from the host. Production must use Docker / k8s secrets, not bind mounts. |
| `V26-PROD-LOCALHOST-VHOST` | error | `VIRTUAL_HOST` env var or `traefik.http.routers.*.rule=Host(...)` label contains a `.localhost` domain in a production compose. |

V26 only fires on compose files matching the project's production filename patterns (default `*.production.yaml`, `*.prod.yaml`, `docker-compose.production.yaml`; configurable via `docker.production_filename_patterns`).

## Why this verifier exists

V05 covers the dev / all-files compose surface. V26 sits on top with **production-only rules** that V05 doesn't enforce. Each rule maps directly to a deploy-time failure mode:

1. **No resource limits.** Without `memory:` / `cpus:`, a runaway container eats the host node. On k8s this means OOMKill of *other* tenants; on a single-host docker-compose deploy it means SSH-impossible-to-recover host. Resource limits are the fence between "one bad release" and "hour-long incident".
2. **Healthcheck missing on a `service_healthy` dependent.** Compose v3+ blocks the dependent service forever if the dependency declares no healthcheck — *forever* meaning until manual intervention. This is the silent "deploy hangs and the team gets paged at 3 a.m." pattern.
3. **`.env` / secrets bind-mounted into prod.** Three things break: image reproducibility (different host = different secrets), secret rotation (you have to ssh + edit every host), and security model (Docker has actual `secrets:` machinery; bind mounts ignore it).
4. **`.localhost` in production VHOST.** RFC 6761 reserves `.localhost` for loopback resolution. A production compose with `VIRTUAL_HOST: api.localhost` will never serve external traffic — usually leftover from copy-paste of the dev compose.

V26 catches all four at hook-time so the regression dies before the deploy.

## Design rationale

- **Filename-based gate.** The fastest reliable way to tell a prod compose from a dev one is filename pattern (`*.production.yaml`, `*.prod.yaml`). Default patterns are sane; project may override via `docker.production_filename_patterns`.
- **Healthcheck rule is `error`, not warning.** A missing healthcheck on a `service_healthy` dependent always blocks the deploy. No legitimate exception.
- **Secret-mount detection is conservative.** V26 scans for filename patterns (`.env`, `*.pem`, `*.key`, `*.crt`, `*.p12`, `secrets/`), not arbitrary "is this a secret?" judgment. False positives are minimal because these literal patterns are almost never legitimate.
- **localhost detection allows `${VAR}`-templated environments.** A compose with `VIRTUAL_HOST: ${API_DOMAIN:-api.localhost}` is *suspicious* but doesn't produce a finding — the env override could replace the localhost default at deploy. Strict mode would require the user to set `API_DOMAIN` before V26 runs (untenable). Compromise: literal `.localhost` in the *value* triggers; a reference to `${VAR}` doesn't.
- **No per-rule INTENTIONAL escape hatch.** Each V26 rule has clear remediation; per-finding silencers would erode the gate.

## How it checks (implementation)

Lives in `hooks/validators/docker_prod_hardening.py`.

### `_is_production_file(name, patterns)` — gate

```python
def _is_production_file(name, patterns):
    return any(fnmatch(name, p) for p in patterns)

DEFAULT_PROD_PATTERNS = (
    "*.production.yaml", "*.production.yml",
    "*.prod.yaml", "*.prod.yml",
    "docker-compose.production.yaml", "docker-compose.production.yml",
)
```

`_prod_patterns(ctx)` reads `ctx.config.docker.production_filename_patterns` if non-empty, else falls back to defaults.

### `_scan(compose_file)` — per-file dispatch

```python
data = yaml.safe_load(compose_file.read_text()) or {}
services = data.get("services") or {}
for svc_name, svc in services.items():
    findings.extend(self._check_resource_limits(compose_file, svc_name, svc))
    findings.extend(self._check_healthcheck(compose_file, svc_name, svc, services))
    findings.extend(self._check_secret_mount(compose_file, svc_name, svc))
    findings.extend(self._check_localhost_vhost(compose_file, svc_name, svc))
```

### `_check_resource_limits` — V26-PROD-NO-RESOURCE-LIMITS

```python
deploy = svc.get("deploy") or {}
limits = ((deploy.get("resources") or {}).get("limits") or {})
if not (limits.get("memory") or limits.get("cpus")):
    yield Finding(rule="V26-PROD-NO-RESOURCE-LIMITS", ...)
```

### `_check_healthcheck` — V26-PROD-NO-HEALTHCHECK

```python
# Look for *other* services that depend on this one with service_healthy.
for other_name, other in services.items():
    depends = other.get("depends_on") or {}
    entry = depends.get(svc_name)
    if entry.get("condition") == "service_healthy" and "healthcheck" not in svc:
        yield Finding(rule="V26-PROD-NO-HEALTHCHECK", ...)
```

### `_check_secret_mount` — V26-PROD-SECRET-BIND-MOUNT

```python
for vol in svc.get("volumes") or []:
    host_path = vol.split(":", 1)[0] if isinstance(vol, str) else str(vol.get("source"))
    if host_path.endswith(".env") or "/.env" in host_path:
        yield Finding(...)
    if host_path.endswith((".pem", ".key", ".crt", ".p12")):
        yield Finding(...)
    if "/secrets" in host_path or host_path.startswith("./secrets"):
        yield Finding(...)
```

### `_check_localhost_vhost` — V26-PROD-LOCALHOST-VHOST

```python
LOCALHOST_RE = re.compile(r"\b(?:[\w-]+\.)?localhost\b", re.IGNORECASE)

# environment block
for key, value in svc_env_pairs:
    if key in ("VIRTUAL_HOST", "VIRTUAL_HOSTS") and LOCALHOST_RE.search(value):
        yield Finding(rule="V26-PROD-LOCALHOST-VHOST", ...)

# Traefik labels
for label in svc_labels:
    if "Host(" in label and LOCALHOST_RE.search(label):
        yield Finding(rule="V26-PROD-LOCALHOST-VHOST", ...)
```

### Could be more effective

- **Cross-file resolution.** A `docker-compose.yaml` + `docker-compose.production.yaml` setup uses Compose's *override merge*. V26 currently treats each file independently — a base compose with `volumes: ['./.env:...']` plus a production override that doesn't override volumes still leaks the bind mount. A real check would `docker compose -f base -f prod config` and inspect the merged result.
- **Replicas / rollout config.** A production compose without `deploy.replicas` (single-replica deploy is brittle) or `deploy.update_config` (no rolling-update strategy) is a fragile deploy. Future enhancement.
- **Resource-limit sanity.** Currently V26 just checks "is the limits block defined". A `memory: 8G` for a Go health-check service is a configuration smell. Heuristic-based check would help.
- **Observability stack.** A prod compose without a logging driver / metrics endpoint label is observability-blind. Could be a future V##.
- **`hadolint` integration.** Same as for V05 — running `hadolint` per Dockerfile referenced in prod compose would catch dozens of patterns V26 doesn't.

## References

- [Compose Specification — Deploy](https://compose-spec.io/) — Docker Inc. + community, *continuously updated*, retrieved 2026-04-30. The `deploy.resources.limits` block V26-PROD-NO-RESOURCE-LIMITS enforces.
- [docker-compose — `depends_on`](https://docs.docker.com/compose/compose-file/05-services/#depends_on) — Docker Inc., *continuously updated*, retrieved 2026-04-30. The `condition: service_healthy` mechanism + healthcheck requirement.
- [Docker — Use secrets in compose](https://docs.docker.com/compose/use-secrets/) — Docker Inc., *continuously updated*, retrieved 2026-04-30. The pattern V26-PROD-SECRET-BIND-MOUNT pushes users toward.
- [NIST SP 800-190 — Application Container Security Guide](https://csrc.nist.gov/publications/detail/sp/800-190/final) — NIST, *published 2017-09*, retrieved 2026-04-30. Why secret-on-host bind mounts are an anti-pattern.
- [OWASP Docker Top 10](https://owasp.org/www-project-docker-top-10/) — OWASP, *continuously updated*, retrieved 2026-04-30. D02 / D08 (secrets), D04 (resources).
- [Snyk — 10 Docker image security best practices](https://snyk.io/blog/10-docker-image-security-best-practices/) — Snyk, *published 2024-08*, retrieved 2026-04-30.
- [RFC 6761 — Special-Use Domain Names](https://datatracker.ietf.org/doc/html/rfc6761) — IETF, *published 2013-02*, retrieved 2026-04-30. The `.localhost` reservation V26-PROD-LOCALHOST-VHOST enforces.
- [nginx-proxy / nginx-proxy](https://github.com/nginx-proxy/nginx-proxy) — Jason Wilder, *continuously maintained*, retrieved 2026-04-30. The `VIRTUAL_HOST` convention.

## Examples

### ✓ Pass

```yaml
# docker-compose.production.yaml
services:
  api:
    image: app:1.4.2
    environment:
      VIRTUAL_HOST: ${API_DOMAIN}              # real domain via env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 10s
    deploy:
      resources:
        limits: { memory: 512M, cpus: "0.5" }
    secrets:
      - jwt_signing_key                          # Docker secret, not bind mount
secrets:
  jwt_signing_key:
    external: true
```

### ✗ Fail

```yaml
services:
  api:
    image: app:1.0
    volumes:
      - ./.env:/app/.env:ro                      # → V26-PROD-SECRET-BIND-MOUNT
      - ./certs/jwt.pem:/etc/jwt.pem:ro          # → V26-PROD-SECRET-BIND-MOUNT
    environment:
      VIRTUAL_HOST: api.localhost                # → V26-PROD-LOCALHOST-VHOST
    # → V26-PROD-NO-RESOURCE-LIMITS (no deploy.resources.limits)
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    # → V26-PROD-NO-HEALTHCHECK (api depends on db.service_healthy, db has no healthcheck)
```
