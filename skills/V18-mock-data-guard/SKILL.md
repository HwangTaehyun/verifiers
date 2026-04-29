# V18 — mock-data-guard

> **Owner**: `hooks/validators/mock_data_guard.py`
> **Tier**: 2 (PostToolUse) per-file when the edited file is a React data-hook; 3 (Stop) sweeps every `use*Data.ts(x)` in `web/src/hooks/`.
> **File patterns**: `**/hooks/use*Data.ts`, `**/hooks/use*Data.tsx`, `**/hooks/use*.ts`, `**/hooks/use*.tsx`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V18-MOCK-VARIABLE` | error | A variable named `MOCK_*`, `mock*`, `FAKE_*`, `DUMMY_*`, or `STUB_*` is declared in a hook file. |
| `V18-MOCK-DATA` | error | An inline `setState([{rank/score/username/count/value/id}: ...])` literal is written. The shape (`{key: value}`) plus the keys give it away as fixture data. |
| `V18-FAKE-DELAY` | warning | A `new Promise(... setTimeout)` or `// Simulate network` comment — UI code faking the loading state without an actual API call. |
| `V18-TODO-API` | warning | A comment matching `// TODO: Replace|Connect|Wire with actual|real API` — explicit "this is fake, fix later" marker that AI agents leave behind. |
| `V18-NO-API-IMPORT` | warning | A `use*Data.ts(x)` file has no import from any of `**/api/**`, `@connectrpc/...`, `**/gen/**`, `**/client/**`, `**/service/**`. Strong signal the hook is mock-only. |

## Why this verifier exists

In React/UI development, the standard demo workflow is:

1. Write the component shell with hardcoded data.
2. Connect it to the API.

Step 2 is exactly the step that AI agents (and humans) forget. The PR reviewer sees a working demo and ships it. Production renders mock data forever.

V18 catches this **at the file convention level**: `web/src/hooks/use*Data.ts(x)` is *the* file pattern for "data hooks", and that file is the most reliable place to enforce "must call a real API". Other parts of the codebase legitimately use mock data (test fixtures, Storybook); V18 deliberately *only* targets data hooks.

## Design rationale

- **Hooks-only enforcement.** A blanket "no MOCK_* anywhere" rule would generate noise (test fixtures, Storybook stories). Targeting `web/src/hooks/use*Data.*` is the surgical strike that matches the bug surface.
- **Test/Storybook exempt by path.** Files containing `__tests__/`, `.test.`, `.spec.`, `.stories.` are skipped because they're allowed to use mocks.
- **Errors for declarations, warnings for comments.** A hardcoded `MOCK_USERS` in a hook is unambiguously the bug; a `// TODO: Replace` comment might accompany a real call already wired alongside. Severity reflects ambiguity.
- **`V18-NO-API-IMPORT` is the structural backstop.** Even if an AI agent renames `MOCK_DATA` → `defaultData` to dodge `V18-MOCK-VARIABLE`, the hook still won't import any API client — and the structural check fires.

## How it checks (implementation)

Lives in `hooks/validators/mock_data_guard.py`.

### `validate_file(ctx, file_path)` — Tier 2

```python
def validate_file(self, ctx, file_path):
    if not (ctx.web_dir and ctx.web_dir.exists()):
        return []
    if not self._is_hook_file(file_path):
        return []
    return self._scan_one(file_path)

@staticmethod
def _is_hook_file(file_path):
    name = Path(file_path).name
    return name.startswith("use") and name.endswith((".ts", ".tsx"))
```

### `_scan_one(file_path)`

```python
def _scan_one(self, file_path):
    findings: list[Finding] = []
    findings.extend(self._check_mock_variables(file_path))
    findings.extend(self._check_hardcoded_state(file_path))
    findings.extend(self._check_fake_delay(file_path))
    findings.extend(self._check_todo_api(file_path))
    findings.extend(self._check_no_api_import(file_path))
    return findings
```

### `_check_mock_variables(file_path)` — V18-MOCK-VARIABLE

```python
MOCK_VAR = re.compile(
    r'^\s*(?:const|let|var)\s+'
    r'(MOCK_[A-Z_]+|mock\w+|FAKE_[A-Z_]+|DUMMY_[A-Z_]+|STUB_[A-Z_]+)\s*=',
    re.MULTILINE,
)
content = Path(file_path).read_text()
# Skip if file is a test / fixture by path
if any(seg in file_path for seg in (".test.", ".spec.", "__tests__")):
    return []
for m in MOCK_VAR.finditer(content):
    line = content[:m.start()].count("\n") + 1
    yield Finding(severity="error", rule="V18-MOCK-VARIABLE", line=line, ...)
```

### `_check_hardcoded_state(file_path)` — V18-MOCK-DATA

```python
HARDCODED = re.compile(
    r'setState\s*\(\s*\[\s*\{\s*'
    r'(rank|score|username|count|value|id|name|email)\s*:'
)
```

The keys are tuned to common fixture-data shapes. Adding new keys is a one-line config change.

### `_check_fake_delay(file_path)` — V18-FAKE-DELAY

```python
FAKE = re.compile(
    r'new\s+Promise\s*\([^)]*setTimeout|//\s*Simulate\s+network'
)
```

### `_check_todo_api(file_path)` — V18-TODO-API

```python
TODO = re.compile(
    r'//\s*TODO\s*:\s*(?:Replace|Connect|Wire)\s+with\s+(?:actual|real)\s+API',
    re.IGNORECASE,
)
```

### `_check_no_api_import(file_path)` — V18-NO-API-IMPORT

```python
content = Path(file_path).read_text()
HAS_API = re.compile(
    r'from\s+["\'](?:'
    r'[^"\']*\.{0,2}/api/'           # ../api/...
    r'|@connectrpc/'                  # @connectrpc/connect-web etc.
    r'|[^"\']*\.{0,2}/gen/'           # ../gen/...
    r'|[^"\']*\.{0,2}/client'         # ../client
    r'|[^"\']*\.{0,2}/service'        # ../service
    r')'
)
if not HAS_API.search(content):
    yield Finding(severity="warning", rule="V18-NO-API-IMPORT", ...)
```

### `validate_project(ctx)` — Tier 3

```python
def validate_project(self, ctx):
    if not (ctx.web_dir and ctx.web_dir.exists()):
        return []
    hooks_dir = ctx.web_dir / "src" / "hooks"
    if not hooks_dir.exists():
        return []
    findings: list[Finding] = []
    for hook_file in hooks_dir.glob("use*Data.ts"):
        findings.extend(self._scan_one(str(hook_file)))
    for hook_file in hooks_dir.glob("use*Data.tsx"):
        findings.extend(self._scan_one(str(hook_file)))
    return findings
```

The Tier 3 sweep narrows to `use*Data.*` (a tighter pattern than `use*.*`) because data hooks are the high-signal subset.

### Could be more effective

- **AST instead of regex for variable declarations.** A regex misses destructuring, default-export consts, and `function` declarations. `@swc/parser` or `typescript` AST would close the gap.
- **Cross-reference with `gen/` types.** A hook importing types from `gen/` but never calling a generated function is a half-wired hook. Could be a future `V18-PARTIAL-WIRE` rule.
- **State-machine check.** If a hook declares `useState([])` and no later `setState(realData)` exists, the hook is suspended in mock mode. Detectable with control-flow analysis.
- **Storybook fixture extraction.** Currently `V18-MOCK-DATA` regex catches `.stories.tsx` mock data because the path-skip is on `.spec/.test/__tests__/` only. Adding `.stories.` to the skip list is a one-line fix.
- **Per-hook config.** A hook that legitimately shouldn't call an API (e.g., `useFeatureFlag` from a static config file) needs an exemption. Currently no per-rule disable; project-level `validators.disabled: ["V18-NO-API-IMPORT"]` is the only knob.

## References

- [Kent C. Dodds — *Avoid the test user*](https://kentcdodds.com/blog/avoid-the-test-user) — Kent C. Dodds, *published 2020-08-25*, retrieved 2026-04-30. Background on the mock-vs-real-state-of-system discipline V18 enforces in code.
- [TanStack Query — *Practical use cases*](https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults) — Tanner Linsley + community, *continuously updated*, retrieved 2026-04-30. The "real API behind a hook" pattern V18 expects.
- [Connect-RPC Web — Service clients](https://connectrpc.com/docs/web/getting-started/) — Connect Authors, *continuously updated*, retrieved 2026-04-30. The `@connectrpc/connect-web` import V18-NO-API-IMPORT considers a real API.
- [Storybook — *Stories vs Tests*](https://storybook.js.org/docs/writing-stories) — Storybook contributors, *continuously updated*, retrieved 2026-04-30. Why `.stories.tsx` should be exempt from `V18-MOCK-DATA`.

## Examples

### ✓ Pass

```ts
// web/src/hooks/useUserData.ts
import { useQuery } from "@tanstack/react-query";
import { userServiceClient } from "@/api/client";

export function useUserData(id: string) {
  return useQuery({
    queryKey: ["user", id],
    queryFn: () => userServiceClient.getUser({ id }),
  });
}
```

### ✗ Fail

```tsx
// web/src/hooks/useDashboardData.tsx
const MOCK_USERS = [                                // → V18-MOCK-VARIABLE
  { id: 1, name: "Alice", score: 100 },             // → V18-MOCK-DATA
];

export function useDashboardData() {
  const [data, setData] = useState(MOCK_USERS);
  // TODO: Replace with real API                    // → V18-TODO-API
  // Simulate network                               // → V18-FAKE-DELAY
  setTimeout(() => setData(MOCK_USERS), 500);
  return data;
}
// (no import from /api/, /gen/, /client/, /service/, @connectrpc) → V18-NO-API-IMPORT
```
