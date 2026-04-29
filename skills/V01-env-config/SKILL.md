# V01 — env-config

> **Owner**: `hooks/validators/env_config.py`
> **Tier**: 2 (PostToolUse) — same surface as Tier 3 (Stop) because every check is project-level (no per-file mode dispatch in the implementation)
> **File patterns**: `**/.env*`, `**/config/*.yaml/yml`, `**/*.go`, `**/*.ts(x)`, `**/docker-compose*.yaml/yml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V01-SECRET-IN-CONFIG` | error | `server/config/*.yaml` contains an 8+ character hardcoded `password:` / `secret:` / `api_key:` / `token:`. Values written as `${VAR}` or `${VAR:-default}` are exempt. |
| `V01-ENV-MISSING` | warning | `${VAR}` reference in `docker-compose*.yaml` (without `${VAR:-default}` fallback) **OR** `os.Getenv("APP_*")` call in Go that has no entry in `.env.example`. |
| `V01-CONFIG-KEY-MISSING` | warning | The set of YAML keys diverges across `<name>.yaml` / `<name>.local.yaml` / `<name>.docker.yaml` config variants. |
| `V01-VITE-ENV-MISSING` | warning | `import.meta.env.VITE_*` reference in `web/` source has no matching definition under `web/env/` or `.env.example`. |

## Why this verifier exists

The single biggest deploy-day failure mode in monorepo apps is **environment-variable drift**: a developer edits `docker-compose.yaml` to reference `${APP_NEW_FEATURE_FLAG}` but forgets to add it to `.env.example`, so production starts with the variable undefined. The same applies to Go reading `os.Getenv("APP_X")` and a frontend calling `import.meta.env.VITE_Y`. By the time CI catches it, the bug is "container starts but feature is silently broken" — exactly the class that AI agents are bad at noticing in code review.

V01 enforces the **3-Layer Separation** principle from the 12-factor app methodology:

1. **Code** — your Go / TS source.
2. **Config** — YAML files committed to the repo, holding non-secret defaults + structural shape.
3. **Env** — secrets and per-environment variation, never committed (only `.env.example` is).

A secret living in a committed config file (V01-SECRET-IN-CONFIG) is a 3-Layer violation. A YAML key drifting between local / docker / production variants (V01-CONFIG-KEY-MISSING) means the app is configured *differently* in different environments — which is the failure mode 12-factor's "config" factor exists to prevent.

## Design rationale

- **Substring + AST mix.** YAML scanning uses a regex because PyYAML loads cleanly but loses line numbers. The Go / TS scans use `re.findall` because cross-file AST analysis would be slower than the Tier 2 budget allows.
- **`${VAR}` exemption is structural.** Any value of the form `${...}` is treated as deferred — the secret lives in the env layer, not config. This is the only way to make YAML-as-config work at all.
- **`.env.example` is the source of truth.** Not `.env` (which is gitignored). The example file is what new developers and CI consult, so a drift between code and example is the actionable surface.
- **Multi-variant key check is per-prefix.** `app.local.yaml` keys are compared with `app.docker.yaml` and `app.yaml`; cross-prefix (`app.yaml` vs `worker.yaml`) is intentionally not compared because they describe different services.

## How it checks (implementation)

Lives in `hooks/validators/env_config.py`. Both `validate_file` (Tier 2) and `validate_project` (Tier 3) delegate to a single `_all_checks(ctx)` helper that runs four sub-scans, because every V01 check is project-level (config drift, .env-example completeness) — there is no useful per-file optimization.

### `_check_secret_in_config(ctx)` — V01-SECRET-IN-CONFIG

```python
# Regex over each line of server/config/*.yaml
re.compile(
    r'^(\s*)(\w+)\s*:\s*["\']?'
    r'([^"\'$\{\s][^"\'\s${}]{7,})'      # 8+ char literal, no $/{ start
    r'["\']?\s*(?:#.*)?$'
)
# Plus a name-keyword filter: key must contain password/secret/token/api_key/key
# Plus value heuristics: high-entropy, recognizable prefix (sk-, ghp_, AKIA, ...)
```
Each `.yaml` under `server/config/` is read, line-numbered, and matched. The `${VAR}` form short-circuits the literal regex by failing the `[^"\'$\{]` first character class.

### `_check_env_example_completeness(ctx)` — V01-ENV-MISSING

1. Parse every `docker-compose*.yaml` with `yaml.safe_load`, walk into `services.*.environment` and `services.*.env_file` to collect `${VAR}` references (handles both `KEY: ${VAR}` and `KEY=${VAR}`).
2. Walk `server/**/*.go` with `re.findall(r'os\.Getenv\("(APP_[A-Z0-9_]+)"\)', src)` to collect Go-side reads.
3. Read `.env.example` line by line, build the set of declared keys.
4. Diff the union of (1) ∪ (2) against (3); each missing key emits one finding with the source file & line of the *first* reference.

### `_check_config_consistency(ctx)` — V01-CONFIG-KEY-MISSING

```python
# Group config files by prefix
groups = defaultdict(list)
for f in (server / "config").glob("*.yaml"):
    prefix = f.stem.split(".")[0]      # "ax-finance.local" → "ax-finance"
    groups[prefix].append(f)

# For each prefix group with 2+ variants, flatten YAML keys and diff
for prefix, files in groups.items():
    key_sets = {f: set(_flatten_keys(yaml.safe_load(f.read_text()))) for f in files}
    union = set.union(*key_sets.values())
    for f, keys in key_sets.items():
        for missing in (union - keys):
            yield Finding(...)
```

`_flatten_keys` produces dotted paths (`database.host`, `auth.jwt.secret`). Comparison is on the path set, not values — variant differences are *expected* on values, not on shape.

### `_check_vite_env_sync(ctx)` — V01-VITE-ENV-MISSING

```python
# Collect references
re.findall(r'import\.meta\.env\.(VITE_[A-Z0-9_]+)', src)
# Collect declarations from web/env/*.ts and web/.env.example
# Diff; emit one finding per missing VITE_*
```

### Could be more effective

- **AST instead of regex for Go `os.Getenv`.** A `re.findall` misses `key := "APP_FOO"; os.Getenv(key)`. A `go/parser` walk would catch the indirect form, at the cost of a Go toolchain dependency from Python — currently rejected as not worth it.
- **viper key registry vs `.env.example`.** If the project uses Viper, `viper.GetString("database.password")` calls form a third source of truth. Currently V01 doesn't read these. A future enhancement could parse `viper.Get*("...")` calls and map them to expected env-var names by Viper's standard transformer (`database.password` → `APP_DATABASE_PASSWORD`).
- **Negative trigger on `.env`.** V01 doesn't check whether `.env` itself contains junk. A real CI workflow would diff `.env.example` ↔ `.env` (locally only) to flag stale dev configs. Currently out of scope because `.env` is gitignored.

## References

- [The Twelve-Factor App, III. Config](https://12factor.net/config) — Heroku, *originally published 2011, continuously maintained*, retrieved 2026-04-30. The canonical statement of "store config in the environment, never in code, treat it as one of the strict separations between codebase and deploy-time settings".
- [Viper — value precedence](https://github.com/spf13/viper#why-viper) — spf13, *maintained continuously since 2014*, retrieved 2026-04-30. Viper's documented precedence order (defaults < config files < env vars < flags) is what V01's three-variant check enforces in spirit: every variant must declare the same keys so the precedence chain is meaningful.
- [Vite — Env Variables and Modes](https://vite.dev/guide/env-and-mode) — Vite team, *continuously updated*, retrieved 2026-04-30. The `import.meta.env.VITE_*` interface, including the `web/env/` discovery rule V01 mirrors.
- [docker-compose — Environment variables](https://docs.docker.com/compose/environment-variables/) — Docker Inc., *continuously updated*, retrieved 2026-04-30. The `${VAR}` and `${VAR:-default}` substitution semantics V01 implements.

## Examples

### ✓ Pass

```yaml
# server/config/app.yaml — secret is ${VAR} reference, not literal
database:
  host: localhost
  password: ${APP_DATABASE_PASSWORD}
```

```env
# .env.example — every APP_* the code reads is documented
APP_DATABASE_PASSWORD=change-me-strong-db-password
```

```yaml
# docker-compose.yaml — every ${VAR} is in .env.example
services:
  api:
    environment:
      APP_DATABASE_PASSWORD: ${APP_DATABASE_PASSWORD}
```

### ✗ Fail

```yaml
# server/config/app.yaml — literal secret
database:
  password: SuperSecret123!     # → V01-SECRET-IN-CONFIG (error)
```

```yaml
# docker-compose.yaml — references ${NEW_FLAG}, missing from .env.example
services:
  api:
    environment:
      FEATURE_FLAG: ${NEW_FLAG} # → V01-ENV-MISSING (warning)
```

```yaml
# server/config/app.local.yaml has key `auth.jwt.refresh_secret`
# server/config/app.docker.yaml is missing it → V01-CONFIG-KEY-MISSING
```

```ts
// web/src/api/client.ts
const url = import.meta.env.VITE_NEW_API_URL;  // not in web/env/* → V01-VITE-ENV-MISSING
```
