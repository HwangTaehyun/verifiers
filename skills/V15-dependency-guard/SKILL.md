# V15 — dependency-guard

> **Owner**: `hooks/validators/dependency_guard.py`
> **Tier**: 2 (PostToolUse) per-file import analysis; 3 (Stop) project-wide sweep.
> **File patterns**: `**/*.go`, `**/*.py`, `**/*.ts`, `**/*.tsx`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V15-WRONG-DEPENDENCY` | error | An import points outward in the layered architecture (an inner layer importing from an outer layer). Default per-language layer order: see below. |
| `V15-CIRCULAR-IMPORT` | warning | Two or more modules form an import cycle (simple BFS detection). |
| `V15-LAYER-SKIP` | warning | An import skips two or more layers (e.g., `domain` → `handler` directly, bypassing `service`). |

### Default layer ladders

| Language | Layers (inner → outer) |
|---|---|
| Go | `domain` (0) < `repository` (1) < `service` (2) < `handler` (3) < `cmd` (4) |
| TS | `types` (0) < `utils` (1) < `hooks` (2) < `components` (3) < `pages` (4) |
| Python | `models` (0) < `repositories` (1) < `services` (2) < `views` (3) |

Override via `.verifiers/layers.yaml`:

```yaml
go:
  domain: 0
  repository: 1
  service: 2
  handler: 3
  cmd: 4
ts:
  types: 0
  shared: 1
  features: 2
  pages: 3
```

## Why this verifier exists

Uncle Bob's Clean Architecture is paraphrased as **"the dependency rule"**: source code dependencies must point only inward. An outer-layer change should never force an inner-layer change.

The failure mode V15 prevents:

1. `domain/user.go` calls `handler.RegisterUser(...)` ← reverses the rule. Now `domain` (which should be reusable in tests, in CLI tools, in any other service) carries a hidden dependency on the HTTP layer.
2. `service/auth.go` imports `cmd/server/main.go` types ← `service` should not know there is a server.
3. `domain/user.go` imports `httpserver` directly, skipping `service` and `handler` ← the layer skip means `service` no longer mediates; transactional boundaries blur.

Each violation, individually, is plausible-looking. Collectively, they collapse the architecture.

## Design rationale

- **Inner-to-outer is the only rule.** Same-layer imports are fine (`service` ↔ `service`). Outer-to-inner is fine (`handler` → `service`). Inner-to-outer is the bug.
- **Layer detection by directory.** V15 reads the import path's directory (`internal/users/repository/user.go` has layer `repository`). The directory name *is* the layer name. Forces convention adherence.
- **`.verifiers/layers.yaml` is the customization point.** A project that uses different vocabulary (`adapters`, `usecases`, `entities` per Hexagonal Architecture) configures it once.
- **Cycle detection is BFS, not Tarjan.** A simple BFS over the import graph is enough for the small N of a single project; the cost of bringing in a real graph library isn't justified.
- **`V15-LAYER-SKIP` is warning, not error.** Layer skips are usually unintended but not always wrong (e.g., a typed DTO in `types/` directly used by `pages/`). Warning gives the user agency.

## How it checks (implementation)

Lives in `hooks/validators/dependency_guard.py`.

### `validate_file(ctx, file_path)` — Tier 2

```python
def validate_file(self, ctx, file_path):
    custom_layers = _load_custom_layers(ctx.project_root)  # .verifiers/layers.yaml
    return self._check_file(file_path, ctx, custom_layers)
```

### `_check_file(file_path, ctx, custom_layers)`

```python
ext = Path(file_path).suffix
imports = _extract_imports(file_path, ext)  # per-language
findings: list[Finding] = []

own_layer = _classify_layer(file_path, ext, custom_layers)
if own_layer is None:
    return findings  # file isn't in a recognized layer; skip

for imp_path in imports:
    target_layer = _classify_layer(imp_path, ext, custom_layers)
    if target_layer is None:
        continue  # external dep (e.g., npm package, external Go module)

    if target_layer["depth"] > own_layer["depth"]:
        # Inner importing outer → V15-WRONG-DEPENDENCY (error)
        yield Finding(...)
    elif target_layer["depth"] - own_layer["depth"] >= 2:
        # Skipping two or more layers
        yield Finding(rule="V15-LAYER-SKIP", ...)
```

### Per-language `_extract_imports`

**Go** — regex over import block:

```python
GO_IMPORT_BLOCK = re.compile(r'import\s*\(\s*([^)]*)\)', re.DOTALL)
GO_SINGLE_IMPORT = re.compile(r'import\s+["\']([^"\']+)["\']')
GO_PATH = re.compile(r'["\']([^"\']+)["\']')
```

**Python** — true AST:

```python
import ast
tree = ast.parse(file_path.read_text())
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield alias.name
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield node.module
```

**TS / TSX** — regex:

```python
TS_IMPORT = re.compile(
    r'^\s*import\s+(?:[^"\'\n]*?from\s+)?["\']([^"\']+)["\']',
    re.MULTILINE,
)
TS_REQUIRE = re.compile(r'\brequire\s*\(\s*["\']([^"\']+)["\']\s*\)')
```

External-package skip uses heuristics: imports starting with `@`, or `^[a-z][a-z0-9-]+/?` without `/internal/` are treated as third-party and ignored.

### `_classify_layer(import_path, ext, custom_layers)`

```python
# Pick the layer whose name appears as a path component
path_parts = Path(import_path).parts
LAYER_MAP = custom_layers.get(_lang_for_ext(ext), DEFAULT_LAYERS[_lang_for_ext(ext)])
for part in path_parts:
    if part in LAYER_MAP:
        return {"name": part, "depth": LAYER_MAP[part]}
return None
```

### `validate_project(ctx)` — Tier 3

```python
def validate_project(self, ctx):
    custom_layers = _load_custom_layers(ctx.project_root)
    return self._check_project(ctx, custom_layers)

def _check_project(self, ctx, custom_layers):
    findings: list[Finding] = []
    graph: dict[str, set[str]] = defaultdict(set)
    for src_file in self._iter_source_files(ctx):
        for imp in _extract_imports(src_file, Path(src_file).suffix):
            graph[src_file].add(imp)
        findings.extend(self._check_file(src_file, ctx, custom_layers))
    findings.extend(_detect_cycles(graph))   # V15-CIRCULAR-IMPORT
    return findings
```

### `_detect_cycles(graph)` — V15-CIRCULAR-IMPORT

```python
visiting: set[str] = set()
visited: set[str] = set()
cycles: list[list[str]] = []
def dfs(node, path):
    if node in visiting:
        # Cycle detected; trim to where it begins
        idx = path.index(node)
        cycles.append(path[idx:] + [node])
        return
    if node in visited:
        return
    visiting.add(node)
    for nxt in graph.get(node, ()):
        dfs(nxt, path + [node])
    visiting.discard(node)
    visited.add(node)
for n in list(graph):
    dfs(n, [])
```

### Could be more effective

- **Real Go AST.** `go/parser` plus `go list -deps -json` would expose every transitive import without regex; cleaner and complete. Cost: Python↔Go bridge.
- **Per-package SCC, not per-file.** Strongly-connected-component analysis on the import graph would surface multi-step cycles (`A → B → C → A`) more cleanly than the current path-prefix DFS.
- **Inversion-of-control awareness.** A `domain` package that imports an *interface* defined in `service` is fine (DI); the current rule flags it. A future enhancement: detect interface-only imports and exempt them.
- **Visualization.** Mermaid graph in the finding `fix` field showing the offending import. AI-readable; massively improves remediation rate.
- **External-dep version drift.** `go.mod` / `package.json` could be cross-checked against the actual imports — a `go.mod` listing X v1.5 but every file importing v1.4 is a footgun. Out of V15's lane; would belong to a future V##.

## References

- [Robert C. Martin — *Clean Architecture*](https://www.oreilly.com/library/view/clean-architecture-a/9780134494272/) — Robert C. Martin, *published 2017*, retrieved 2026-04-30. The Dependency Rule V15 enforces.
- [Alistair Cockburn — Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture/) — Alistair Cockburn, *published 2005, continuously linked-to*, retrieved 2026-04-30. Alternate layering vocabulary V15's `.verifiers/layers.yaml` accommodates.
- [Vaughn Vernon — *Implementing Domain-Driven Design*, ch. 4](https://www.amazon.com/Implementing-Domain-Driven-Design-Vaughn-Vernon/dp/0321834577) — Vaughn Vernon, *published 2013*, retrieved 2026-04-30. The bounded-context layering pattern V15's defaults reflect.
- [Go — Effective Go (Package design)](https://go.dev/doc/effective_go#package-names) — Go team, *continuously updated*, retrieved 2026-04-30. Background for Go's layer-by-directory convention.

## Examples

### ✓ Pass

```go
// internal/users/service/auth.go
import (
    "context"
    "internal/users/repository"      // service → repository (inward) ✓
    "internal/users/domain"          // service → domain (inward) ✓
)
```

### ✗ Fail

```go
// internal/users/domain/user.go (depth 0)
import (
    "internal/users/handler"          // domain → handler (depth 3 → outward)
                                      // → V15-WRONG-DEPENDENCY (error)
)
```

```go
// internal/users/domain/user.go (depth 0)
import "internal/users/cmd"           // domain → cmd (depth 4, skip 4 layers)
                                      // → V15-LAYER-SKIP (warning)
```

```python
# repositories/orders.py imports services.billing
# services/billing.py imports repositories/orders → V15-CIRCULAR-IMPORT (warning)
```
