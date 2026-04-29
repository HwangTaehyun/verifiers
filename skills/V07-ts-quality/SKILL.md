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

## Why this verifier exists

The TypeScript ecosystem's value comes from the *combination* of `tsc`, `eslint`, and per-tool conventions — each by itself is incomplete:

- `tsc --noEmit` proves type correctness but says nothing about style.
- `eslint` catches style + suspicious patterns but defers to `tsc` for types.
- `madge` finds cycles that don't fail compilation but cause runtime initialization order bugs.
- `knip` finds dead code that bloats bundles + maintenance surface.

V07 packages all four into hook checks so the user doesn't have to remember which tool to run for which problem. Plus the four cheap regexes (`any`, hardcoded colors, console.log, MUI v4 imports) catch the most-common-instant-fix issues that AI agents are slowest to self-correct.

## Design rationale

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

### Could be more effective

- **JSX-aware comment skip.** Current regex misses JSX block comments (`{/* ... */}`) and template-literal-embedded `console.log`. A tiny TS lexer (or `swc-cli`) would eliminate the false positives.
- **TS strict-mode enforcement.** A `tsconfig.json` without `"strict": true` is a quality red flag. Currently V07 doesn't check the tsconfig itself; one extra rule (`V07-TSCONFIG-NOT-STRICT`) would close the gap. Trivial to add.
- **`vite-env.d.ts` typing strictness.** The user's project uses `import.meta.env.VITE_*`; V07 doesn't enforce that every used `VITE_*` is typed in `vite-env.d.ts`. Phase 27 audit's V07-VITE-ENV-TYPED proposal directly addresses this.
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
