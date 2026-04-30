# V22 — multi-env

> **Owner**: `hooks/validators/multi_env.py`
> **Tier**: 2 (PostToolUse) and 3 (Stop) — same project-wide consistency sweep on both because every check is project-level.
> **File patterns**: `**/.env*`, `**/config/*.yaml`, `**/config/*.yml`, `**/docker-compose*.yaml`, `**/docker-compose*.yml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V22-NON-APP-PREFIX` | warning | A var declared in `server/.env.example` doesn't start with `APP_` AND doesn't match an allowed external-tool prefix (`AIRFLOW_`, `POSTGRES_`, `HASURA_`, `SF_`, etc.) AND isn't in the allowed-bare list (`DOMAIN`, `API_DOMAIN`, ...). |
| `V22-ROOT-SERVER-DRIFT` | warning | An `APP_*` variable is declared in `root/.env.example` but missing from `server/.env.example`. **Asymmetric**: the reverse direction (server-only `APP_*` vars) is *not* flagged because server is the canonical source for the `APP_*` namespace. |
| `V22-VIPER-KEY-NO-ENV` | warning | A YAML key in a canonical `server/config/<name>.yaml` (no `.local` / `.docker` suffix) maps via Viper convention to an `APP_*` env var that's not in `server/.env.example`. |

## Why this verifier exists

Multi-env monorepos accumulate three kinds of silent drift, each of which only surfaces at deploy time:

1. **Naming inconsistency.** A new env var lands as `JWT_SECRET` instead of `APP_JWT_SECRET` because the developer forgot the project convention. Viper's `automaticEnv` + `SetEnvPrefix("APP")` won't bind it; the app starts, the secret is empty string, login is broken in subtle ways.
2. **Cross-file drift (one direction).** `root/.env.example` declares `APP_NEW_FEATURE` but `server/.env.example` (the canonical source) doesn't — root is making up an `APP_*` key that the server doesn't recognize. The reverse case (`APP_*` only in server) is legitimate: server owns the `APP_*` namespace; root only carries compose-orchestration vars (`DOMAIN`, `*_PORT`, `AIRFLOW_*`).
3. **Config without env binding.** `server/config/app.yaml` references `database.password` (which Viper expects as `APP_DATABASE_PASSWORD`) but `.env.example` doesn't declare it. New developer clones the repo, runs the app, gets a cryptic Postgres auth failure.

V22 catches all three at hook-time so the regression dies before commit.

## Design rationale

- **All three rules are warnings, not errors.** Each has legitimate edge cases:
  - External-tool prefixes are an open list (V22 ships sensible defaults; project may add).
  - Some `APP_*` variables legitimately differ between root and server scopes (e.g., a webhook-proxy var defined only at root).
  - A YAML key may be hardcoded by design (`environment: development` not env-overridable).
- **Server is the canonical env-example source — drift check is asymmetric.** Server owns the `APP_*` namespace; root carries compose-orchestration vars (`DOMAIN`, `*_PORT`, `AIRFLOW_*`, `SF_*`) that legitimately don't appear in server. So V22 flags `root → server` drift only (root has `APP_*` not in server = root unilaterally introducing a key). The reverse direction (`APP_*` only in server) is normal and intentionally NOT flagged — it would generate noise on every server-private secret. The user (taehyun) requested this in 2026-04-30 after seeing every `APP_DATABASE_*` / `APP_JWT_*` flagged as drift in `ai-project-template`.
- **Variant files (`<name>.local.yaml` / `.docker.yaml`) are skipped for Viper-mapping.** A local-override key not present in production is intentional; flagging it would generate noise.
- **External-tool prefix list is opinionated default + project extension.** V22 ships `APP_, AIRFLOW_, _AIRFLOW_, POSTGRES_, PG_, HASURA_, SF_` which covers ~95% of real monorepo cases. Projects with proprietary prefixes add via `.verifiers/config.yaml`:

  ```yaml
  multi_env:
    allowed_prefixes: ["LEGACY_", "MYORG_"]
    allowed_bare: ["EXTERNAL_HOOK_URL"]
  ```

- **Drift check is `APP_*`-only, not all vars.** Cross-prefix comparison would generate noise — `AIRFLOW_FERNET_KEY` legitimately appears only in server-side `.env.example`. Restricting to `APP_*` (project-owned vars) keeps the drift signal sharp.
- **Viper convention is hard-coded.** V22 implements the standard `database.password` → `APP_DATABASE_PASSWORD` translation. Projects using a different env-key replacer would need a config knob; not yet implemented.

## How it checks (implementation)

Lives in `hooks/validators/multi_env.py`. `validate_file` and `validate_project` both delegate to `_all_checks(ctx)` because every check is project-level — no per-file optimization possible.

### Top-level

```python
def _all_checks(self, ctx):
    findings = []
    findings.extend(self._check_app_prefix(ctx))
    findings.extend(self._check_drift_root_vs_server(ctx))
    findings.extend(self._check_viper_mapping(ctx))
    return findings
```

### `_check_app_prefix(ctx)` — V22-NON-APP-PREFIX

```python
ENV_LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=")

env_file = self._server_env_path(ctx)   # server/.env.example
allowed_prefixes = self._allowed_prefixes(ctx)
allowed_bare = self._allowed_bare(ctx)

for line_no, line in enumerate(env_file.read_text().splitlines(), 1):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        continue
    m = ENV_LINE.match(stripped)
    if not m:
        continue
    key = m.group(1)
    if any(key.startswith(p) for p in allowed_prefixes):
        continue
    if key in allowed_bare:
        continue
    yield Finding(rule="V22-NON-APP-PREFIX", line=line_no, ...)
```

### `_check_drift_root_vs_server(ctx)` — V22-ROOT-SERVER-DRIFT

```python
root_keys = _parse_env_keys(root_env)
server_keys = _parse_env_keys(server_env)

# Restrict to APP_* — only those should mirror across files
root_app = {k for k in root_keys if k.startswith("APP_")}
server_app = {k for k in server_keys if k.startswith("APP_")}

# Asymmetric: only root → server direction is flagged.
# Server-only APP_* is legitimate (server owns the namespace).
# Root-only APP_* is a structural mistake (root shouldn't
# unilaterally introduce APP_* keys).
for missing in sorted(root_app - server_app):
    yield Finding(rule="V22-ROOT-SERVER-DRIFT", file=str(server_env), ...)
```

### `_check_viper_mapping(ctx)` — V22-VIPER-KEY-NO-ENV

```python
# Pick canonical config files only (no .local/.docker variants)
canonical_files = []
for path in sorted(config_dir.glob("*.yaml")):
    if len(path.stem.split(".")) == 1:    # ax-finance.yaml — yes
        canonical_files.append(path)      # ax-finance.local.yaml — no

env_keys = _parse_env_keys(server_env)

for cfg_file in canonical_files:
    data = yaml.safe_load(cfg_file.read_text())
    for yaml_key in _flatten_yaml(data):
        expected = _viper_env_name(yaml_key)   # database.password → APP_DATABASE_PASSWORD
        if expected not in env_keys:
            yield Finding(rule="V22-VIPER-KEY-NO-ENV", ...)
```

### `_viper_env_name(yaml_key)`

```python
def _viper_env_name(yaml_key: str) -> str:
    """database.password → APP_DATABASE_PASSWORD"""
    return "APP_" + yaml_key.upper().replace(".", "_").replace("-", "_")
```

This matches Viper's standard pattern:
```go
viper.SetEnvPrefix("APP")
viper.SetEnvKeyReplacer(strings.NewReplacer(".", "_", "-", "_"))
viper.AutomaticEnv()
```

### Could be more effective

- **Strict mode for drift.** Currently warning-only. A future config knob `multi_env.drift_strict: true` would upgrade `V22-ROOT-SERVER-DRIFT` to error for projects that actively maintain root-as-mirror.
- **Inverse Viper check (env declared but no config consumer).** Currently V22 only checks "config key needs env var". The reverse — "env var declared but never read by Viper / `os.Getenv`" — is an indicator of dead env vars. Detectable; not yet implemented.
- **Detect non-canonical config-key paths.** A typo like `databse.password` (missing `a`) would be silently accepted by Viper (returns empty string). V22 doesn't have a typo detector; could pair with a known-keys list extracted from the Go source's `viper.GetString("...")` calls.
- **Cross-tool drift.** `terraform/main.tf` may declare `var.app_database_password`; if it's not aligned with `APP_DATABASE_PASSWORD` the Terraform-managed deployment skips it. V22 doesn't reach into Terraform; future scope.
- **Vite env mapping.** The frontend has its own convention (`import.meta.env.VITE_*`) which V01-VITE-ENV-MISSING already covers. V22 stays in the server-side lane.

## References

- [The Twelve-Factor App, III. Config](https://12factor.net/config) — Heroku, *originally 2011, continuously maintained*, retrieved 2026-04-30. The principle that config lives in env, separate from code.
- [Viper — Working with environment variables](https://github.com/spf13/viper#working-with-environment-variables) — spf13, *continuously maintained*, retrieved 2026-04-30. The `SetEnvPrefix` + `SetEnvKeyReplacer` + `AutomaticEnv` pattern V22 enforces in spirit.
- [Apache Airflow — Environment variables](https://airflow.apache.org/docs/apache-airflow/stable/configurations-ref.html) — Apache Airflow, *continuously updated*, retrieved 2026-04-30. The `AIRFLOW_*` prefix V22's allow-list inherits.
- [Postgres image — Environment variables](https://github.com/docker-library/docs/blob/master/postgres/README.md) — Docker, *continuously updated*, retrieved 2026-04-30. The `POSTGRES_*` prefix.
- [Hasura — Environment variables](https://hasura.io/docs/2.0/deployment/graphql-engine-flags/reference/) — Hasura, *continuously updated*, retrieved 2026-04-30. The `HASURA_*` prefix.
- [Salesforce dlt source](https://dlthub.com/docs/dlt-ecosystem/verified-sources/salesforce) — dltHub, *continuously updated*, retrieved 2026-04-30. The `SF_*` prefix.

## Examples

### ✓ Pass

```env
# root/.env.example
APP_DATABASE_PASSWORD=change-me
DOMAIN=example.com
```

```env
# server/.env.example
APP_DATABASE_PASSWORD=change-me           # APP_ prefix ✓
AIRFLOW_FERNET_KEY=                        # external-tool prefix ✓
POSTGRES_PORT=5432                         # external-tool prefix ✓
DOMAIN=example.com                         # allowed-bare ✓
```

```yaml
# server/config/app.yaml
database:
  password: ${APP_DATABASE_PASSWORD}        # config key matches APP_DATABASE_PASSWORD ✓
```

### ✗ Fail

```env
# server/.env.example
APP_DATABASE_PASSWORD=x
JWT_SECRET=y                                # → V22-NON-APP-PREFIX (no APP_/external prefix)
APP_NEW_FEATURE=z                           # OK — server-only APP_* is allowed
```

```env
# root/.env.example
APP_DATABASE_PASSWORD=x
APP_LEGACY_KEY=y                            # → V22-ROOT-SERVER-DRIFT
                                            #   (root unilaterally introduces APP_*
                                            #    not present in server canonical)
```

```yaml
# server/config/app.yaml
database:
  password: ${APP_DATABASE_PASSWORD}
  host: localhost                           # ← APP_DATABASE_HOST missing from .env.example
```

```env
# server/.env.example
APP_DATABASE_PASSWORD=x
# APP_DATABASE_HOST not declared            → V22-VIPER-KEY-NO-ENV
```
