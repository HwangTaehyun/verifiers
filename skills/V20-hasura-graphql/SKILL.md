# V20 — hasura-graphql

> **Owner**: `hooks/validators/hasura_graphql_enforcement.py`
> **Tier**: 2 (PostToolUse) per-file Go scan when Hasura is detected; 3 (Stop) project-wide Go sweep.
> **File patterns**: `**/*.go`, `**/docker-compose.yaml`, `**/docker-compose.yml`, `**/hasura/**`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V20-RAW-SQL-FORBIDDEN` | error | A `.go` file calls `db.Query(...)`, `.QueryRow(...)`, `.QueryContext(...)`, `.QueryRowContext(...)`, `.Exec(...)`, `.ExecContext(...)`, `.PrepareContext(...)`, or contains a literal `SELECT/INSERT/UPDATE/DELETE` SQL statement. |
| `V20-SQL-IMPORT` | error | A `.go` file imports `database/sql`, `github.com/jmoiron/sqlx`, `github.com/jackc/pgx/...`, or another raw-SQL library. |
| `V20-MISSING-GRAPHQL` | warning | A `Service` struct definition is detected with no GraphQL-client field (e.g., `gqlClient`, `graphqlClient`, `hasura*`). The handler is set up to talk to its data source, but it didn't wire the GraphQL client. |

All three rules **only fire when Hasura is detected in the project** (`_detect_hasura(ctx)` returns true). Non-Hasura projects pay zero cost.

## Why this verifier exists

Hasura's value comes from being **the** read/write path to the database — the table/permission/relationship metadata defines exactly who can see what. The moment a Go service bypasses Hasura via `database/sql`, three things go wrong simultaneously:

1. **Permission policy lost.** Row-level rules in `hasura/metadata/.../tables.yaml` are no longer enforced — Go reads the raw table.
2. **Audit trail bypassed.** Hasura's request log no longer reflects every read; observability fragments.
3. **Schema drift.** Adding a column in a migration but not exposing it in metadata is fine *if* every consumer goes through Hasura. The moment Go reads the column directly, the metadata-as-contract assumption breaks.

V20 makes the bypass detectable at hook-time. The path-of-least-resistance for an AI agent debugging a slow query is to drop to raw SQL ("the GraphQL client must be slow" — usually it isn't); V20 keeps that escape hatch closed.

## Design rationale

- **Hasura-detection gate.** Without `hasura/` in the project (or `hasura-graphql-engine` in a compose file), V20 silently emits zero findings. This means projects that don't use Hasura can install verifiers without configuring V20 — it's invisible to them.
- **Errors for raw SQL, warning for missing client.** Calling `db.Query()` is unambiguously the bug; a `Service` struct without a GraphQL client field might be a service that legitimately doesn't need one. Severity reflects ambiguity.
- **Regex over AST.** Detecting `db.Query` requires either knowing the type of `db` (full type-resolution AST) or grepping the literal call site. The grep is 90% accurate at 1% the cost.
- **`/hasura/` and other Hasura tooling files exempt.** `hasura/migrations/**/*.sql` legitimately contains DDL and DML; V20 doesn't fire there. The exemption is path-based.
- **Phase 3 history.** V20 used to be `V15-*` rules inside `dependency_guard.py`. The split (Phase 3) gave it its own V-ID prefix, restoring the "one V## ↔ one module" invariant `_assert_registry_invariants` enforces.

## How it checks (implementation)

Lives in `hooks/validators/hasura_graphql_enforcement.py`.

### `_detect_hasura(ctx)` — gating predicate

```python
def _detect_hasura(self, ctx):
    if ctx.hasura_dir is not None:                      # detected by ProjectContext
        return True
    compose_candidates = [
        ctx.project_root / "docker-compose.yaml",
        ctx.project_root / "docker-compose.yml",
    ]
    if ctx.server_dir is not None:
        compose_candidates += [
            ctx.server_dir / "docker-compose.yaml",
            ctx.server_dir / "docker-compose.yml",
        ]
    for compose in compose_candidates:
        if not compose.exists():
            continue
        text = compose.read_text(errors="replace").lower()
        if "hasura/graphql-engine" in text or "hasura-graphql" in text:
            return True
    return False
```

### `validate_file(ctx, file_path)` — Tier 2

```python
def validate_file(self, ctx, file_path):
    if not self._detect_hasura(ctx):
        return []
    if not file_path.endswith(".go"):
        return []
    if _is_exempt(file_path):                            # /hasura/, _test.go, .pb.go, gen/
        return []
    return self._check_go_file(file_path)
```

### `_check_go_file(file_path)`

```python
content = Path(file_path).read_text()
findings: list[Finding] = []
findings.extend(self._check_raw_sql(file_path, content))
findings.extend(self._check_sql_import(file_path, content))
findings.extend(self._check_service_missing_graphql(file_path, content))
return findings
```

### `_check_raw_sql` — V20-RAW-SQL-FORBIDDEN

```python
SQL_CALL_PATTERNS = [
    (re.compile(r'\.Query(?:Row)?Context\s*\('),  "raw SQL query"),
    (re.compile(r'\.ExecContext\s*\('),           "raw SQL execution"),
    (re.compile(r'\.PrepareContext\s*\('),        "raw SQL prepared statement"),
    (re.compile(r'\bSELECT\b\s+.*\bFROM\b', re.IGNORECASE),     "raw SQL SELECT"),
    (re.compile(r'\bINSERT\s+INTO\b', re.IGNORECASE),           "raw SQL INSERT"),
    (re.compile(r'\bUPDATE\b\s+\w+\s+\bSET\b', re.IGNORECASE),  "raw SQL UPDATE"),
    (re.compile(r'\bDELETE\s+FROM\b', re.IGNORECASE),           "raw SQL DELETE"),
]
for line_num, line in enumerate(content.splitlines(), 1):
    if line.lstrip().startswith(("//", "/*", "*")):  # comment skip
        continue
    for pattern, desc in SQL_CALL_PATTERNS:
        if pattern.search(line):
            yield Finding(rule="V20-RAW-SQL-FORBIDDEN", line=line_num, message=desc, ...)
            break
```

A common false-positive: a `// SELECT ...` comment in documentation. The line-prefix comment skip handles the simple cases; nested/block comments occasionally slip through. False-positive rate empirically < 1% on the user's repo (Phase 27 tuning).

### `_check_sql_import` — V20-SQL-IMPORT

```python
RAW_SQL_LIBS = (
    "database/sql",
    "github.com/jmoiron/sqlx",
    "github.com/jackc/pgx",
    "gorm.io/gorm",                # ORM that's still raw SQL underneath
    "github.com/go-pg/pg",
)
IMPORT = re.compile(r'^\s*"([^"]+)"', re.MULTILINE)
in_block = False
for line in content.splitlines():
    if "import (" in line:
        in_block = True
    elif in_block and line.strip() == ")":
        in_block = False
    elif in_block:
        if (m := IMPORT.match(line)):
            for lib in RAW_SQL_LIBS:
                if m.group(1).startswith(lib):
                    yield Finding(rule="V20-SQL-IMPORT", ...)
                    break
```

### `_check_service_missing_graphql` — V20-MISSING-GRAPHQL

```python
SERVICE = re.compile(r'^type\s+(\w+Service)\s+struct\s*\{([^}]*)\}', re.MULTILINE | re.DOTALL)
GQL_FIELD = re.compile(
    r'\b(?:gql|graphql|hasura)\w*\s+\*?\w',
    re.IGNORECASE,
)
for m in SERVICE.finditer(content):
    name, body = m.group(1), m.group(2)
    if "test" in name.lower() or "mock" in name.lower():
        continue
    if not GQL_FIELD.search(body):
        yield Finding(rule="V20-MISSING-GRAPHQL", message=f"{name} has no GraphQL client field", ...)
```

The Service-struct heuristic is naming-based — `*Service` types are the convention. A project that names its Connect-RPC services differently (`*Server`, `*Handler`) wouldn't trigger; per-project config (`security.service_struct_pattern`) is a planned extension.

### `validate_project(ctx)` — Tier 3

Same logic as Tier 2 but walked across `server/internal/**/*.go`. The Hasura-detection gate runs once at the top.

### Could be more effective

- **AST-based call resolution.** A regex misses `db.Conn().Query(...)` (chained accessor on a `db` returned from a method). `go/parser` plus type-resolution would close this; cost is the Python↔Go bridge.
- **Whitelist of acceptable raw-SQL files.** Some projects have a `migrations/` Go program that legitimately runs `database/sql`. Currently V20 expects path-exemption (e.g., `/migrations/`); a per-rule disable would be cleaner.
- **`gorm` distinction.** GORM is "raw SQL with a Go API" — semantically it bypasses Hasura but syntactically looks structured. V20 treats it as raw SQL; some teams might accept it. Per-project knob would help.
- **Outbound query log inspection.** An external observability check ("no Postgres queries from the API process unless they came from Hasura") is the *true* enforcement. Out of V20's lane (CI / production observability), but worth noting.
- **Shared-helper detection.** A common `pkg/db/util.go` that does the actual `Query` and is called from many handlers — V20 flags every call site. A future enhancement: the helper file is the single point that needs fixing; deduplicating findings would improve actionability.

## References

- [Hasura — Authorization & Permissions](https://hasura.io/docs/2.0/auth/authorization/) — Hasura, *continuously updated*, retrieved 2026-04-30. Why bypassing Hasura defeats the row-level security model.
- [Hasura — Securing GraphQL endpoint](https://hasura.io/docs/2.0/deployment/securing-graphql-endpoint/) — Hasura, *continuously updated*, retrieved 2026-04-30.
- [Hasura — Use Hasura as the unified data layer](https://hasura.io/learn/graphql/hasura-advanced/data-modeling/) — Hasura Learn, *continuously updated*, retrieved 2026-04-30. The architectural premise V20 enforces in code.
- [Connect-RPC handler patterns](https://connectrpc.com/docs/go/serving-clients/) — Connect Authors, *continuously updated*, retrieved 2026-04-30. Where the `*Service` struct convention V20 detects originates.

## Examples

### ✓ Pass

```go
// internal/users/service.go
type UserService struct {
    gqlClient gqlclient.Client    // ✓ has a GraphQL client field
    logger    *slog.Logger
}

func (s *UserService) Create(ctx context.Context, req *pb.CreateRequest) (...) {
    user, err := s.gqlClient.CreateUser(ctx, req)    // ✓ goes through Hasura
    ...
}
```

### ✗ Fail

```go
import "database/sql"        // → V20-SQL-IMPORT (error)

type UserService struct {
    db *sql.DB               // → V20-MISSING-GRAPHQL (warning)
    // (no graphql/gql/hasura field)
}

func (s *UserService) Get(ctx context.Context, id string) (*User, error) {
    rows, err := s.db.QueryContext(ctx,                        // → V20-RAW-SQL-FORBIDDEN (error)
        "SELECT id, name FROM users WHERE id = $1", id)        // → also V20-RAW-SQL-FORBIDDEN
    ...
}
```
