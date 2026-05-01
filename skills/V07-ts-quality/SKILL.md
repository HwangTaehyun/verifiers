# V07 — ts-quality

> **Owner**: `hooks/validators/ts_quality.py`
> **Tier**: 2 (PostToolUse) — per-file regex (any/colors/console/MUI) + single-file `eslint`. 3 (Stop) — adds project-wide `tsc --noEmit`, full `eslint`, `madge --circular`, `knip` (dead code).
> **File patterns**: `**/*.ts`, `**/*.tsx`, `**/package.json`, `**/tsconfig.json`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V07-NO-ANY` | warning | Edited file uses `: any` / `as any` / `<any>` (matched outside comments and strings). |
| `V07-HARDCODED-COLOR` | warning | Inline style with literal `#hex` / `rgb(...)` / `rgba(...)` / `hsl(...)`. Theme-token recommended. |
| `V07-NO-CONSOLE` | warning | `console.log` / `console.debug` / `console.info` outside test / Storybook files. |
| `V07-DEPRECATED-MUI` | warning | `makeStyles`, `withStyles`, or `@material-ui/` import (MUI v4 patterns deprecated in v5). |
| `V07-ESLINT-<rule>` | error | `eslint --format json` reported an issue; original ESLint rule name preserved. |
| `V07-TSC-<code>` | error | `tsc --noEmit --pretty` printed `(.+)\((\d+),\d+\): error (TS\d+): (.+)`. |
| `V07-CIRCULAR-IMPORT` | warning | `madge --circular --json src/` returned a cycle. |
| `V07-UNUSED-CODE` | warning | `knip` reported dead exports / files / dependencies. |
| `V07-VITE-ENV-TYPED` | warning | `import.meta.env.VITE_*` referenced in code but not declared in `web/src/vite-env.d.ts` (or `env.d.ts`). |

## Why this verifier exists

The TypeScript ecosystem's value comes from the *combination* of `tsc`, `eslint`, and per-tool conventions — each by itself is incomplete:

- `tsc --noEmit` proves type correctness but says nothing about style.
- `eslint` catches style + suspicious patterns but defers to `tsc` for types.
- `madge` finds cycles that don't fail compilation but cause runtime initialization order bugs.
- `knip` finds dead code that bloats bundles + maintenance surface.

V07 packages all four into hook checks so the user doesn't have to remember which tool to run for which problem. Plus the four cheap regexes (`any`, hardcoded colors, console.log, MUI v4 imports) catch the most-common-instant-fix issues that AI agents are slowest to self-correct.

## Design rationale

### Cache strategy

V07 uses two tool-native cache mechanisms to speed up repeated runs:

**ESLint — `--cache --cache-strategy content --cache-location .verifiers/cache/eslint/`**

Applied in both `_check_eslint_single` (Tier 2) and `_check_eslint_full` (Tier 3). ESLint writes per-file cache entries inside `.verifiers/cache/eslint/`. `--cache-strategy content` uses SHA-based invalidation instead of mtime, which is more reliable on CI and across checkouts.

A lock-file gate (`_invalidate_eslint_cache_if_lock_changed`) computes a SHA-256 of `web/bun.lockb` (falling back to `package-lock.json` / `yarn.lock`) and stores it in `.verifiers/cache/eslint/.lock-hash`. If the lock hash changes (e.g. after `bun add`), the entire cache directory is deleted before ESLint runs, guarding against "plugin upgraded but cache still says PASS".

**TypeScript — `--incremental --tsBuildInfoFile .verifiers/cache/tsc.tsbuildinfo`**

Applied in `_check_tsc` (Tier 3 only). TypeScript writes a single `.tsbuildinfo` JSON file that tracks per-file type state. Only enabled when TypeScript ≥ 5.0 is detected (`_supports_incremental`), because TS 4.x had known bugs with `noEmit + incremental` combined.

**Cache paths**

| Tool | Path |
|---|---|
| ESLint cache dir | `<project_root>/.verifiers/cache/eslint/` |
| ESLint lock hash | `<project_root>/.verifiers/cache/eslint/.lock-hash` |
| TypeScript build info | `<project_root>/.verifiers/cache/tsc.tsbuildinfo` |

**Escape hatch — `VERIFIERS_NO_CACHE=1`**

Set this env var to disable all cache flags for both ESLint and tsc. Useful for CI jobs that must verify from a clean state or when debugging stale-cache issues:

```bash
VERIFIERS_NO_CACHE=1 python hooks/validators/ts_quality.py
```

- **`any` warning, not error.** Sometimes `any` is the right escape hatch (e.g. third-party lib without types). The warning surfaces it for review without blocking.
- **Hardcoded color regex is style-attribute-only.** A regex that flagged `const RED = "#f00"` would explode with false positives. V07 only flags when the literal is in a `style={{...}}` JSX prop or `style.color =` assignment.
- **`console.log` exempt for test / Storybook.** Files matching `*.test.ts(x)`, `*.spec.ts(x)`, `*.stories.tsx`, `__tests__/` are skipped — those are the legitimate use cases.
- **MUI deprecation rule.** `makeStyles` / `withStyles` survived v5 via `@mui/styles` but are officially deprecated. V07 doesn't enforce migration per se — just warns so it's visible in code review.
- **JSON output for tsc / eslint / madge / knip.** Each tool has a `--format json` flag; V07 uses it. Text-format parsing is fragile across versions.
- **`bun` / `bunx` over `npm` / `npx`.** V07 invokes via `bun run eslint --format json` and `bunx madge --circular --json src/`. The user's project chose Bun; running the wrong package manager spawns a separate dependency tree and produces inconsistent results.

## How it checks (implementation)

Lives in `hooks/validators/ts_quality.py`. `validate_file` runs the per-edit fast-path; `validate_project` runs the project-wide heavy-path.

### Tier 2 (`validate_file`)

```python
def validate_file(self, ctx, file_path):
    if not (ctx.web_dir and ctx.web_dir.exists()):
        return []
    if not file_path.endswith((".ts", ".tsx")):
        return []
    findings: list[Finding] = []
    findings.extend(self._check_any_type(file_path))
    findings.extend(self._check_hardcoded_colors(file_path))
    findings.extend(self._check_console_log(file_path))
    findings.extend(self._check_deprecated_mui(file_path))
    findings.extend(self._check_eslint_single(ctx, file_path))
    return findings
```

### `_check_any_type(file_path)` — V07-NO-ANY

```python
ANY = re.compile(r'(?:^|\s|[<,(=])(:?\s*any\b|as\s+any\b|<any[,>])')
COMMENT = re.compile(r'^\s*(//|\*|/\*)')
for line_num, line in enumerate(file.read_text().splitlines(), 1):
    if COMMENT.match(line):
        continue
    if ANY.search(line):
        yield Finding(rule="V07-NO-ANY", line=line_num, ...)
```

The character-class boundary (`[<,(=]`) avoids matching identifiers like `manyHandlers`. Comment skip is naive (only line-prefix); a real tokenizer would also skip JSX comments and strings — currently a known false-positive source.

### `_check_hardcoded_colors(file_path)` — V07-HARDCODED-COLOR

```python
STYLE = re.compile(
    r'style\s*=\s*\{\{[^}]*?(#[0-9a-f]{3,8}|rgba?\(|hsla?\()',
    re.IGNORECASE
)
```

Triggers only inside `style={{...}}` so a `const TOKEN_RED = "#f00"` constant doesn't fire.

### `_check_console_log(file_path)` — V07-NO-CONSOLE

```python
TEST_PATH = ("__tests__/", ".test.", ".spec.", ".stories.")
if any(p in file_path for p in TEST_PATH):
    return
LOG = re.compile(r'\bconsole\.(log|debug|info)\s*\(')
```

### `_check_deprecated_mui(file_path)` — V07-DEPRECATED-MUI

```python
DEPRECATED = re.compile(
    r'(makeStyles|withStyles|@material-ui/)'
)
```

### `_check_eslint_single(ctx, file_path)` — V07-ESLINT-<rule>

```python
result = subprocess.run(
    ["bun", "run", "eslint", "--format", "json", file_path],
    cwd=str(ctx.web_dir),
    capture_output=True, text=True, timeout=15,
)
# Output: list of file objects, each with `messages: [...]`
data = json.loads(result.stdout) if result.stdout else []
for file_entry in data:
    for msg in file_entry.get("messages") or []:
        yield Finding(
            severity="error" if msg["severity"] == 2 else "warning",
            file=file_entry["filePath"],
            line=msg.get("line"),
            rule=f"V07-ESLINT-{msg.get('ruleId', 'unknown')}",
            message=msg["message"],
            ...
        )
```

### Tier 3 (`validate_project`)

```python
def validate_project(self, ctx):
    if not (ctx.web_dir and ctx.web_dir.exists()):
        return []
    findings: list[Finding] = []
    findings.extend(self._check_tsc(ctx))                # tsc --noEmit
    findings.extend(self._check_eslint_full(ctx))        # eslint .
    findings.extend(self._check_circular_imports(ctx))   # madge --circular
    findings.extend(self._check_unused_code(ctx))        # knip
    findings.extend(self._check_vite_env_typed(ctx))     # vite-env.d.ts
    return findings
```

#### `_check_tsc(ctx)` — V07-TSC-<TS####>

```python
result = subprocess.run(
    ["bun", "run", "tsc", "--noEmit", "--pretty", "false"],
    cwd=str(ctx.web_dir),
    capture_output=True, text=True, timeout=180,
)
TSC = re.compile(r'^(.+?)\((\d+),\d+\): error (TS\d+): (.+)$')
for line in result.stdout.splitlines():
    if (m := TSC.match(line)):
        yield Finding(
            severity="error",
            file=m.group(1),
            line=int(m.group(2)),
            rule=f"V07-TSC-{m.group(3)}",
            message=m.group(4),
            ...
        )
```

#### `_check_circular_imports(ctx)` — V07-CIRCULAR-IMPORT

```python
result = subprocess.run(
    ["bunx", "madge", "--circular", "--json", "src/"],
    cwd=str(ctx.web_dir),
    capture_output=True, text=True, timeout=60,
)
cycles = json.loads(result.stdout) if result.stdout else []
for cycle in cycles:
    yield Finding(
        severity="warning",
        rule="V07-CIRCULAR-IMPORT",
        message=" → ".join(cycle) + " → ..." ,
        ...
    )
```

#### `_check_unused_code(ctx)` — V07-UNUSED-CODE

```python
result = subprocess.run(
    ["bunx", "knip", "--reporter", "json"],
    cwd=str(ctx.web_dir),
    capture_output=True, text=True, timeout=60,
)
data = json.loads(result.stdout) if result.stdout else {}
for category in ("files", "exports", "dependencies"):
    for item in data.get(category) or []:
        yield Finding(severity="warning", rule="V07-UNUSED-CODE", ...)
```

#### `_check_vite_env_typed(ctx)` — V07-VITE-ENV-TYPED (Phase48)

```python
VITE_REF = re.compile(r"\bimport\.meta\.env\.(VITE_[A-Z0-9_]+)")
DECL = re.compile(r"\b(VITE_[A-Z0-9_]+)\s*[:?]")

# 1. Find env.d.ts (Vite default first, then fallback)
env_dts = (ctx.web_dir / "src" / "vite-env.d.ts")
if not env_dts.is_file():
    env_dts = (ctx.web_dir / "src" / "env.d.ts")
    if not env_dts.is_file():
        env_dts = None

# 2. Sweep web/src/**/*.{ts,tsx} (excluding env.d.ts itself)
vite_refs: dict[str, tuple[str, int]] = {}
for ts_file in (ctx.web_dir/"src").rglob("*.ts*"):
    if env_dts and ts_file.resolve() == env_dts.resolve():
        continue
    for line_no, line in enumerate(ts_file.read_text(...).splitlines(), 1):
        for m in VITE_REF.finditer(line):
            vite_refs.setdefault(m.group(1), (str(ts_file), line_no))

# 3a. No env.d.ts → flag every reference
# 3b. env.d.ts present → flag only undeclared keys
declared = {m.group(1) for m in DECL.finditer(env_dts.read_text(...))}
for name, (file_path, line_no) in vite_refs.items():
    if name not in declared:
        yield Finding(severity="warning", rule="V07-VITE-ENV-TYPED", ...)
```

The two-regex split (reference vs. declaration) is intentional: declarations live as
`readonly VITE_X: string;` (TypeScript field syntax), while references are
property accesses `import.meta.env.VITE_X`. A unified regex would over-match
on either side; `[:?]` in `DECL` constrains to a property declaration boundary.

The `env.d.ts` file is **excluded from the scan loop** so its own example
comments (`// import.meta.env.VITE_FOO`) don't generate self-referential
findings.

### Could be more effective

- **JSX-aware comment skip.** Current regex misses JSX block comments (`{/* ... */}`) and template-literal-embedded `console.log`. A tiny TS lexer (or `swc-cli`) would eliminate the false positives.
- **TS strict-mode enforcement.** A `tsconfig.json` without `"strict": true` is a quality red flag. Currently V07 doesn't check the tsconfig itself; one extra rule (`V07-TSCONFIG-NOT-STRICT`) would close the gap. Trivial to add.
- **`.env.example` ↔ `vite-env.d.ts` cross-check.** A natural next step on top of V07-VITE-ENV-TYPED: every `VITE_*` declared in the .d.ts should also appear in `.env.example` (and vice versa). Currently V01 / V22 own the env-side, V07 owns the type-side; tying them together would catch "typed but never set" drift.
- **Bundle size delta.** `vite build --json` output → compare against a stored baseline. Out of V07's scope but a natural next-validator.
- **`eslint` config validation.** A `.eslintrc.*` with `"@typescript-eslint/no-unused-vars": "off"` is a self-defeating config. V16 (linter-config-guard) covers this category — V07 stays in-lane.

## References

- [TypeScript Handbook — Strictness](https://www.typescriptlang.org/docs/handbook/2/basic-types.html#strictness) — Microsoft, *continuously updated*, retrieved 2026-04-30. Why `any` is a quality smell even when permitted.
- [ESLint — `--format json`](https://eslint.org/docs/latest/use/command-line-interface) — OpenJS Foundation, *continuously updated*, retrieved 2026-04-30. The JSON shape V07 parses.
- [tsc — Compiler Options (`--noEmit`, `--pretty`)](https://www.typescriptlang.org/docs/handbook/compiler-options.html) — Microsoft, *continuously updated*, retrieved 2026-04-30.
- [madge — Find circular dependencies](https://github.com/pahen/madge) — Patrik Henningsson, *continuously maintained*, retrieved 2026-04-30. The cycle-detection algorithm V07-CIRCULAR-IMPORT relies on.
- [knip — Find unused files, dependencies and exports](https://knip.dev/) — Lars Kappert, *continuously maintained*, retrieved 2026-04-30.
- [MUI v5 migration guide](https://mui.com/material-ui/migration/migration-v4/) — Material UI, *continuously updated*, retrieved 2026-04-30. Why `makeStyles` / `@material-ui/` imports are flagged.
- [Vercel — React performance best practices](https://vercel.com/docs/frameworks/react) — Vercel, *continuously updated*, retrieved 2026-04-30. Theme-token + dead-code rationale.

## Examples

### ✓ Pass

```tsx
import { Button } from "@mui/material";
import { theme } from "@/theme";

export function MyButton({ label }: { label: string }) {
    return (
        <Button style={{ color: theme.palette.primary.main }}>
            {label}
        </Button>
    );
}
```

### ✗ Fail

```tsx
import { makeStyles } from "@material-ui/core";   // → V07-DEPRECATED-MUI

export function Component(props: any) {           // → V07-NO-ANY
    console.log("rendering", props);              // → V07-NO-CONSOLE (not a test file)
    return <div style={{ color: "#ff0000" }} />;  // → V07-HARDCODED-COLOR
}
```

```ts
// foo.ts imports bar; bar imports foo → V07-CIRCULAR-IMPORT (Stop)
// utils/legacy.ts is no longer imported anywhere → V07-UNUSED-CODE (Stop)
```

### V07-VITE-ENV-TYPED examples

✓ **Pass** — every `VITE_*` reference is declared in `web/src/vite-env.d.ts`:

```ts
// web/src/vite-env.d.ts
interface ImportMetaEnv {
    readonly VITE_API_URL: string;
    readonly VITE_AUTH_KEY: string;
}

// web/src/api.ts
const url = import.meta.env.VITE_API_URL;   // typed: string
const key = import.meta.env.VITE_AUTH_KEY;  // typed: string
```

✗ **Fail** — `VITE_NEW_FLAG` referenced in code but missing from the .d.ts:

```ts
// web/src/vite-env.d.ts
interface ImportMetaEnv {
    readonly VITE_API_URL: string;
}

// web/src/feature.ts
const enabled = import.meta.env.VITE_NEW_FLAG;
//                                ^^^^^^^^^^^^^^ → V07-VITE-ENV-TYPED
//   "Add `readonly VITE_NEW_FLAG: string` to interface ImportMetaEnv
//    in src/vite-env.d.ts."
```
