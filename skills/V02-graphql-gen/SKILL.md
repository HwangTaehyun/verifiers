# V02 — graphql-gen

> **Owner**: `hooks/validators/graphql_gen.py`
> **Tier**: 2 (PostToolUse) per-file gen-staleness + yaml shape; 3 (Stop) adds project-wide function-reference sweep.
> **File patterns**: `**/graph/queries/**/*.graphql`, `**/graph/schemas/*.graphql`, `**/genqlient.yaml`, `**/gqlclient/*.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V02-YAML-MISSING-FIELD` | error | `genqlient.yaml` lacks any of `schema:`, `operations:`, `generated:`, or `package:`. genqlient cannot codegen without all four. |
| `V02-STALE-GEN` | warning | A `.graphql` input file's content hash diverges from the cached hash *or* `genqlient.go` `mtime` predates the latest input. The generated file is out of sync with its source. |
| `V02-OMITEMPTY` | error | A generated `*uuid.UUID` (or other pointer) field lacks `,omitempty` in its json tag — the JSON marshaler will emit `null` and Hasura will reject the mutation. |
| `V02-MISSING-FUNCTION` | warning | A repository file calls `gqlclient.SomeOp(...)` but the generated `genqlient.go` has no such symbol. Triggers when the user wrote the call ahead of regenerating the client. |

## Why this verifier exists

genqlient is "generate a Go client from your GraphQL queries", which means **every change to a `.graphql` file must be followed by a regen**. The single most common bug pattern is: the developer edits `userQueries.graphql`, forgets `go run github.com/Khan/genqlient`, and the codebase compiles fine because the old generated function still exists — until runtime returns a server error because the request body and the schema disagree.

V02 catches this *before* the test run that would expose it, which is essential for AI agents because they often write a query and the calling site in the same turn and would otherwise leave the regen for "later".

## Design rationale

- **Hash-based stale detection.** mtime alone is unreliable on systems where git checkout resets timestamps. V02 stores `sha256(file_bytes)` per input in a small JSON cache and compares.
- **Per-pointer omitempty rule.** Hasura's GraphQL strict-null behavior is the actual root cause: if you send `{"id": null}` for a non-nullable column, the mutation rejects with a vague schema-mismatch error. The omitempty tag is the only Go-side knob that prevents this — so V02 enforces it as `error`, not `warning`.
- **Function-reference scan is Stop-only.** Walking the repo to match every `gqlclient.X` call against generated symbols is expensive (regex + cross-file). Tier 2 (per-edit) only runs when the file edited *is* `genqlient.go` or contains `genqlient` in the path, keeping cost low.
- **`schema/operations/generated/package` check is shape-only.** It does not validate the *values* (paths, package names) — that's compile-time's job.

## How it checks (implementation)

Lives in `hooks/validators/graphql_gen.py`. `validate_file` runs the lighter set (yaml shape + omitempty + stale, plus per-file ref check when the edited path contains `genqlient`); `validate_project` runs the same plus a full repo-wide function-reference sweep.

### `_check_genqlient_yaml(ctx)` — V02-YAML-MISSING-FIELD

```python
data = yaml.safe_load(genqlient_yaml.read_text())
required = {"schema", "operations", "generated", "package"}
missing = required - set(data.keys())
for key in missing:
    yield Finding(rule="V02-YAML-MISSING-FIELD", ...)
```

Pure shape check — no values are validated. Genqlient itself fails noisily on a wrong path, so V02 only catches the "I forgot a key" class.

### `_check_stale_generated(ctx)` — V02-STALE-GEN

```python
# 1. Gather inputs
inputs = list(graph_dir.glob("queries/**/*.graphql")) + \
         list(graph_dir.glob("schemas/*.graphql")) + \
         [genqlient_yaml]

# 2. Hash each + load cache
cache_path = graph_dir / ".gen-hash-cache.json"
cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
current = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}

# 3. Compare hashes; also check generated mtime is newer than every input
generated = graph_dir / "gqlclient" / "genqlient.go"
gen_mtime = generated.stat().st_mtime
for input_path, h in current.items():
    if cache.get(input_path) != h:
        yield Finding(...)  # input changed since last regen
    if Path(input_path).stat().st_mtime > gen_mtime:
        yield Finding(...)  # input newer than generated

# 4. Persist cache for next run
cache_path.write_text(json.dumps(current))
```

Hash + mtime double-check survives `git checkout` (which resets mtime) and `cp -p` (which preserves mtime but breaks content).

### `_check_omitempty(ctx)` — V02-OMITEMPTY

```python
# Walk gqlclient/*.go and gen/**/*.go; regex for pointer fields
PTR_FIELD = re.compile(
    r'^\s*(\w+)\s+'              # field name
    r'\*([\w./]+)\s+'            # pointer type
    r'`json:"([^"]+)"`'          # json tag
)
for line in src.splitlines():
    m = PTR_FIELD.match(line)
    if m and "omitempty" not in m.group(3):
        yield Finding(rule="V02-OMITEMPTY", ...)
```

Pointer-only because non-pointer types serialize to zero values (`""`, `0`) which Hasura accepts. Pointer + null is the actual problem case.

### `_check_function_references(ctx)` — V02-MISSING-FUNCTION (Stop only)

```python
# 1. Build symbol set from genqlient.go
SYM = re.compile(r'^func\s+(?:\(\w+\s+\*\w+\)\s+)?(\w+)\s*\(')
generated_syms = {m.group(1) for line in genqlient_go.read_text().splitlines()
                  if (m := SYM.match(line))}

# 2. Walk repository repos/services/handlers; find gqlclient.X calls
CALL = re.compile(r'\bgqlclient\.(\w+)\s*\(')
for go_file in walk_go_files(ctx):
    for m in CALL.finditer(go_file.read_text()):
        if m.group(1) not in generated_syms:
            yield Finding(rule="V02-MISSING-FUNCTION", ...)
```

Heavier than the per-file scans (regex over every Go file in the repo), so it lives in Tier 3 only.

### Could be more effective

- **AST-based generated symbol extraction.** Regex `func\s+...\s+(\w+)\s*\(` misses methods on type parameters and embedded receivers. `go/parser` would be exact but adds Python↔Go bridge cost.
- **Schema-level validation.** V02 doesn't run `genqlient` itself to verify the queries are valid against the schema. A future enhancement could shell out to `go run github.com/Khan/genqlient -dry-run` once per Stop and surface its errors as findings.
- **Track per-query usage.** A query that's never called from any Go file is dead code. Currently V02 doesn't flag this. The function-reference scan already builds the right indices to do it cheaply — would be a one-loop addition.
- **Watcher mode.** Currently V02 only runs on hook events. A `--watch` mode could regen automatically when a `.graphql` file changes, removing the human-error step entirely. Out of V02's scope but worth noting as the upstream alternative.

## References

- [genqlient — Configuration](https://github.com/Khan/genqlient/blob/main/docs/genqlient.yaml) — Khan Academy, *continuously maintained*, retrieved 2026-04-30. The canonical list of required `genqlient.yaml` fields V02 enforces.
- [GraphQL — Type System / Non-Null](https://spec.graphql.org/October2021/#sec-Non-Null) — GraphQL Foundation, *published October 2021*, retrieved 2026-04-30. The strict-null contract that makes the omitempty rule load-bearing.
- [Effective Go — `omitempty` json tag](https://pkg.go.dev/encoding/json#Marshal) — Go team, *continuously updated*, retrieved 2026-04-30. The marshaling behavior V02-OMITEMPTY guards against.
- [Hasura — GraphQL mutations and `null` columns](https://hasura.io/docs/2.0/mutations/postgres/upsert/) — Hasura, *continuously updated*, retrieved 2026-04-30. The downstream pattern that fails when omitempty is missing.

## Examples

### ✓ Pass

```yaml
# server/graph/genqlient.yaml — all four required fields
schema:
  - schemas/schema.graphql
operations:
  - queries/users.graphql
generated: gqlclient/genqlient.go
package: gqlclient
```

```go
// server/graph/gqlclient/genqlient.go (generated, with omitempty)
type CreateUserInput struct {
    ID   *uuid.UUID `json:"id,omitempty"`
    Name string     `json:"name"`
}
```

### ✗ Fail

```yaml
# missing `package:` → V02-YAML-MISSING-FIELD (error)
schema: schemas/schema.graphql
operations: queries/users.graphql
generated: gqlclient/genqlient.go
```

```go
// generated UUID pointer without omitempty
type Input struct {
    ID *uuid.UUID `json:"id"`        // → V02-OMITEMPTY (error)
}
```

```go
// repository edited; client not regenerated
func (r *Repo) GetActiveUsers(ctx context.Context) {
    return r.gql.GetActiveUsersV2(ctx)  // → V02-MISSING-FUNCTION (no V2 exists yet)
}
```
