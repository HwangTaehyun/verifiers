# V04 — hasura-migration

> **Owner**: `hooks/validators/hasura_migration.py`
> **Tier**: 2 (PostToolUse) per-file timestamp / DDL / pair check; 3 (Stop) adds full DDL sweep + metadata orphan detection.
> **File patterns**: `**/hasura/migrations/**/*.sql`, `**/hasura/metadata/**/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V04-TIMESTAMP-ORDER` | error | A migration directory's name does not sort lexicographically (`<timestamp>_<name>/`) — Hasura applies in directory-name order. |
| `V04-DUPLICATE-TIMESTAMP` | error | Two migration dirs share the same timestamp prefix; only one will run on a fresh DB. |
| `V04-MISSING-FILE` | error | A migration dir has `up.sql` but no `down.sql` (or vice versa). Rollback requires both. |
| `V04-DANGEROUS-DDL` | warning | `up.sql` contains `DROP TABLE` (without `IF EXISTS`), `DROP COLUMN`, `TRUNCATE`, or `ALTER TYPE`. Bypass with a `-- INTENTIONAL: <reason>` comment on the same line. |
| `V04-METADATA-ORPHAN` | error | `hasura/metadata/databases/.../tables.yaml` references a table that no `up.sql` ever creates. The metadata will fail on apply. |

## Why this verifier exists

Hasura migrations are **append-only** in the strict sense — you can never edit a committed migration without diverging your DB from your teammates'. That makes the up-front correctness checks load-bearing:

- A bad timestamp ordering means the migration silently runs after a later one, leaving the DB in an inconsistent state.
- A missing `down.sql` means rollback is impossible — and rollback is the only escape hatch when a migration breaks production.
- `DROP COLUMN` is irreversible: the data is gone the moment Hasura applies it.
- A metadata-orphan table means `hasura metadata apply` will fail mid-batch, half-applying the metadata and leaving the project in a stuck state.

V04 fires on the SQL file edit *before* the developer commits, so the bad pattern dies in the editor.

## Design rationale

- **Timestamp order is lexicographic.** Hasura sorts dir names; ISO-like prefixes (`20260415_120000_add_users`) sort correctly as strings. V04 just checks `sorted(...) == list_in_fs_order`.
- **`-- INTENTIONAL:` escape hatch.** A `DROP COLUMN` in a deliberately destructive migration (data deprecation, re-architecture) needs to be possible. Requiring a same-line comment forces the developer to write down *why*, which is enough friction to make the comment a postmortem-quality artifact.
- **Metadata orphan is `error`, not `warning`.** Because `hasura metadata apply` will fail on it. There is no "proceed anyway" mode that makes sense.
- **DDL scan does NOT parse SQL.** It uses regex on the raw `.sql` text. A SQL parser would be more accurate but adds dependency cost; V04 trades precision for simplicity (`-- INTENTIONAL:` carries the false-positive escape).

## How it checks (implementation)

Lives in `hooks/validators/hasura_migration.py`. `validate_file` runs the cheap shape checks (timestamp / pair / single-file DDL); `validate_project` runs the same plus a project-wide DDL sweep and the metadata-orphan check.

### Common helpers (run in both modes)

```python
def _find_migration_dir(ctx) -> Path | None:
    # Hasura layout: hasura/migrations/<db_name>/
    for db_dir in (ctx.hasura_dir / "migrations").iterdir():
        if db_dir.is_dir():
            return db_dir
    return None
```

### `_check_timestamp_ordering(migration_dir)` — V04-TIMESTAMP-ORDER

```python
dirs = sorted(
    [d for d in migration_dir.iterdir() if d.is_dir()],
    key=lambda d: d.name
)
fs_order = list(migration_dir.iterdir())
if [d.name for d in dirs] != [d.name for d in fs_order if d.is_dir()]:
    yield Finding(...)
```

Compares the lexicographic sort against the filesystem-order list. Mismatch means a directory is named in a way that breaks the prefix convention.

### `_check_duplicate_timestamps(migration_dir)` — V04-DUPLICATE-TIMESTAMP

```python
TS = re.compile(r'^(\d{14}|\d{10})_')   # 14-digit yyyymmddhhmmss or 10-digit unix
seen: dict[str, Path] = {}
for d in migration_dir.iterdir():
    if not d.is_dir():
        continue
    m = TS.match(d.name)
    if m:
        ts = m.group(1)
        if ts in seen:
            yield Finding(...)  # collision
        seen[ts] = d
```

### `_check_up_down_pairing(migration_dir)` — V04-MISSING-FILE

```python
for d in migration_dir.iterdir():
    if not d.is_dir():
        continue
    has_up = (d / "up.sql").exists()
    has_down = (d / "down.sql").exists()
    if has_up and not has_down:
        yield Finding(rule="V04-MISSING-FILE", message="no down.sql")
    elif has_down and not has_up:
        yield Finding(rule="V04-MISSING-FILE", message="no up.sql")
```

### `_check_dangerous_ddl(sql_file)` — V04-DANGEROUS-DDL

```python
DANGER = re.compile(
    r'^(?P<ddl>\s*(DROP\s+TABLE(?!\s+IF\s+EXISTS)|'
    r'DROP\s+COLUMN|TRUNCATE|ALTER\s+TYPE)[^;]*;)',
    re.IGNORECASE | re.MULTILINE,
)
INTENTIONAL = re.compile(r'--\s*INTENTIONAL:', re.IGNORECASE)
for line_num, line in enumerate(sql_file.read_text().splitlines(), 1):
    if DANGER.search(line) and not INTENTIONAL.search(line):
        yield Finding(rule="V04-DANGEROUS-DDL", line=line_num, ...)
```

The `-- INTENTIONAL:` exemption is single-line; placing it on a different line does NOT bypass the rule (deliberate — forces the comment next to the dangerous statement).

### `_check_metadata_consistency(ctx, migration_dir)` — V04-METADATA-ORPHAN (Stop)

```python
# 1. Collect declared tables from metadata
declared: set[tuple[str, str]] = set()  # (schema, table)
for tables_yaml in (ctx.hasura_dir / "metadata" / "databases").rglob("tables.yaml"):
    for entry in yaml.safe_load(tables_yaml.read_text()) or []:
        t = entry.get("table", {})
        declared.add((t.get("schema", "public"), t["name"]))

# 2. Collect created tables from all up.sql
CREATE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'(?:(\w+)\.)?(\w+)',
    re.IGNORECASE
)
created: set[tuple[str, str]] = set()
for up in migration_dir.rglob("up.sql"):
    for m in CREATE.finditer(up.read_text()):
        created.add((m.group(1) or "public", m.group(2)))

# 3. Diff
for schema, table in declared - created:
    yield Finding(rule="V04-METADATA-ORPHAN", ...)
```

The reverse direction (a CREATE TABLE without metadata) is **not** flagged because it's a valid pattern (internal table not exposed via Hasura).

### Could be more effective

- **Real SQL parser.** `sqlparse` or `sqlglot` would catch `DROP TABLE foo` followed by `CREATE TABLE foo` in the same migration (rename pattern that's safe), avoiding the current false positive. Adds ~5 MB of deps; deferred.
- **Apply the migrations against a temp DB.** The most reliable check is "does Hasura accept this stack of migrations?" — running `hasura migrate apply --up` against a disposable Postgres in a sandbox would catch every issue V04 misses. CI-grade, not hook-grade (slow + needs Docker).
- **Detect data-mutating up.sql with no corresponding rollback strategy in down.sql.** Currently `_check_up_down_pairing` only verifies presence. A future enhancement could detect `UPDATE`/`DELETE` in up that has no inverse data-restoration in down (impossible to fully automate, but the pattern is detectable enough to warn).
- **Track applied vs declared on the live DB.** Out of scope — that's `hasura migrate status`'s job. V04 stays at the source-of-truth-only layer.

## References

- [Hasura — Migrations and metadata workflow](https://hasura.io/docs/2.0/migrations-metadata-seeds/migrations-metadata-setup/) — Hasura, *continuously updated*, retrieved 2026-04-30. Source of the directory-naming + up.sql/down.sql convention V04 enforces.
- [PostgreSQL — DROP TABLE](https://www.postgresql.org/docs/current/sql-droptable.html) — PostgreSQL, *continuously updated*, retrieved 2026-04-30. The `IF EXISTS` clause that V04-DANGEROUS-DDL exempts (because it's idempotent).
- [Liquibase — Best practices for forward-only migrations](https://www.liquibase.com/blog/database-migration-best-practices) — Liquibase, *published 2023, retrieved 2026-04-30*. The append-only migration philosophy V04 inherits, even though Hasura's tooling differs.
- [Database migrations: how to do them right](https://www.brunton-spall.co.uk/post/2014/05/06/database-migrations-modify-your-schema-with-confidence/) — Michael Brunton-Spall, *published 2014-05-06*, retrieved 2026-04-30. Foundational write-up on why every up needs a down.

## Examples

### ✓ Pass

```
hasura/migrations/default/
├── 20260101_120000_create_users/
│   ├── up.sql          (CREATE TABLE users ...)
│   └── down.sql        (DROP TABLE users)
└── 20260102_090000_add_email_index/
    ├── up.sql          (CREATE INDEX ...)
    └── down.sql        (DROP INDEX ...)
```

```sql
-- 20260415_010000_drop_legacy/up.sql
-- INTENTIONAL: legacy_audit table is no longer used after Q1 2026 retention cutover (#PR-1234)
DROP TABLE legacy_audit;
```

### ✗ Fail

```sql
-- up.sql — no IF EXISTS, no INTENTIONAL comment
DROP TABLE users;       -- → V04-DANGEROUS-DDL (warning)
```

```yaml
# hasura/metadata/databases/default/tables/public_orders.yaml
table:
  schema: public
  name: orders
# But no migration ever runs CREATE TABLE orders → V04-METADATA-ORPHAN (error)
```

```
hasura/migrations/default/
├── 20260101_120000_create_users/   ← timestamp X
└── 20260101_120000_create_orders/  ← same timestamp → V04-DUPLICATE-TIMESTAMP
```
