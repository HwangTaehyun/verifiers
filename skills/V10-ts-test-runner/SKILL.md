# V10 — ts-test-runner

> **Owner**: `hooks/validators/ts_test_runner.py`
> **Tier**: 2 (PostToolUse) only. Stop is intentionally a no-op — V07's `validate_project` already runs the full TS surface (`tsc`, `eslint`, `madge`, `knip`).
> **File patterns**: `**/*.ts`, `**/*.tsx`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V10-TEST-FAIL` | error | The test corresponding to the edited TS/TSX file is failing. |
| `V10-NO-TEST` | warning | Edited a non-test source file and no `*.test.ts(x)` / `*.spec.ts(x)` exists alongside it. |
| `V10-REPEATED-FAIL` | warning | The same `<file>::<testName>` failed ≥ N times in a row (default 3). |

## Why this verifier exists

The TS/React ecosystem has three popular test runners (`vitest`, `jest`, `bun test`) with overlapping config files. AI agents often run "the wrong one" — invoking `npm run test` when the project actually uses `vitest --run`, or invoking `vitest` in a project still on `jest`. V10 auto-detects from filesystem signals so the right tool always runs.

The same per-edit fast-feedback rationale as V09 applies: scoped to the changed file, run only its tests, fast turnaround. The full surface remains V07's job.

## Design rationale

- **Auto-detect runner via priority order.** `vitest.config.{ts,js}` > `jest.config.{ts,js}` > `package.json` `"test"` script > `bun test` fallback. Matches what a human user would do mentally: "what's actually configured here?".
- **Resolve test file by convention.** For `Foo.tsx`, V10 tries `Foo.test.tsx`, `Foo.spec.tsx`, `__tests__/Foo.test.tsx` in that order.
- **Stop is no-op (V07 covers it).** Same rationale as V09 → V06.
- **Repeated-fail tracker shared with V09/V11.** One source of truth for "this test keeps failing".

## How it checks (implementation)

Lives in `hooks/validators/ts_test_runner.py`.

### `validate_file(ctx, file_path)`

```python
def validate_file(self, ctx, file_path):
    if not (ctx.web_dir and ctx.web_dir.exists()):
        return []
    if not file_path.endswith((".ts", ".tsx")):
        return []
    if self._is_excluded(file_path):
        return []

    threshold = ctx.config.thresholds.test_runner.repeated_failure_count
    findings: list[Finding] = []

    if self._is_test_file(file_path):
        # Test file edited: run it directly
        findings.extend(self._run_test_file(ctx, file_path, threshold))
    else:
        # Source file: resolve corresponding test
        test_file = self._resolve_test_file(ctx, file_path)
        if test_file:
            findings.extend(self._run_test_file(ctx, test_file, threshold))
        else:
            findings.extend(self._check_test_exists(file_path))
    return findings
```

### `_detect_runner(ctx)`

```python
web = ctx.web_dir
if any((web / f).exists() for f in ("vitest.config.ts", "vitest.config.js", "vitest.config.mts")):
    return ["bunx", "vitest", "run", "--reporter=json"]
if any((web / f).exists() for f in ("jest.config.ts", "jest.config.js", "jest.config.cjs")):
    return ["bunx", "jest", "--json"]
# package.json `"test"` script
pkg = json.loads((web / "package.json").read_text())
test_script = (pkg.get("scripts") or {}).get("test", "")
if "vitest" in test_script:
    return ["bun", "run", "test", "--", "--reporter=json"]
if "jest" in test_script:
    return ["bun", "run", "test", "--", "--json"]
return ["bun", "test"]  # fallback (no JSON; less rich findings)
```

### `_run_test_file(ctx, test_file, threshold)` — V10-TEST-FAIL / V10-REPEATED-FAIL

```python
cmd = self._detect_runner(ctx) + [str(test_file)]
result = subprocess.run(cmd, cwd=str(ctx.web_dir), capture_output=True, text=True, timeout=60)
data = json.loads(result.stdout) if result.stdout.startswith(("{", "[")) else None
tracker = FeedbackTracker(ctx)
for failure in self._extract_failures(data):
    sig = f"V10::{failure.file}::{failure.name}"
    streak = tracker.record_failure(sig)
    yield Finding(
        severity="error",
        rule="V10-REPEATED-FAIL" if streak >= threshold else "V10-TEST-FAIL",
        file=failure.file,
        message=failure.message,
        ...
    )
```

`_extract_failures` is per-runner: vitest's JSON has `testResults[].assertionResults[]`, jest's has the same shape (compatible by intent). bun-test fallback parses text output.

### `_check_test_exists(file_path)` — V10-NO-TEST

```python
candidates = [
    Path(file_path).with_suffix(".test.tsx"),
    Path(file_path).with_suffix(".test.ts"),
    Path(file_path).with_suffix(".spec.tsx"),
    Path(file_path).with_suffix(".spec.ts"),
    Path(file_path).parent / "__tests__" / Path(file_path).name.replace(".tsx", ".test.tsx"),
]
if not any(c.exists() for c in candidates):
    yield Finding(severity="warning", rule="V10-NO-TEST", ...)
```

The file gets a free pass if it's a `*.d.ts`, an index re-export, or only contains `import` statements (heuristic: `<10 non-import non-blank lines`). These are exempt because they have nothing to test.

### Could be more effective

- **Vitest watch mode integration.** `vitest --watch` already does fast scoped runs; V10 currently spawns a fresh process per Edit. A persistent vitest watcher with V10 reading its events would cut latency from ~1-2 s to ~100 ms per Edit. Architecturally heavy (long-running process vs short-lived hook).
- **TypeScript-side type-coverage.** A test that imports a type and calls a function passes type-check trivially. Adding `tsc --noEmit` only on the edited test file would catch a class of "type-passes-but-runtime-fails" bugs. Currently V07's Stop-mode covers project-wide.
- **Per-export coverage check.** Currently V10-NO-TEST checks file-level. A symbol-level check ("exported function `foo` has no `describe('foo')` block") would be more precise. Trade-off: requires a TS parser.
- **Snapshot-test detection.** `expect(...).toMatchSnapshot()` is often updated unconditionally during AI iterations, masking real regressions. V13 (ai-cheating-guard) already partially covers this; could be sharpened with snapshot-file-mtime comparison.
- **Bun test JSON reporter.** Bun's test runner has a `--reporter=junit` flag; converting to a structured form would let the bun fallback path emit findings as rich as the vitest/jest paths.

## References

- [Vitest — Reporters](https://vitest.dev/guide/reporters.html) — Vitest team, *continuously updated*, retrieved 2026-04-30. The JSON reporter shape V10 parses.
- [Jest — CLI Options (`--json`)](https://jestjs.io/docs/cli) — Meta + community, *continuously updated*, retrieved 2026-04-30.
- [Bun — Bun test](https://bun.sh/docs/cli/test) — Oven, *continuously updated*, retrieved 2026-04-30. The fallback runner V10 invokes when no jest/vitest config exists.
- [Kent C. Dodds — Common Testing Mistakes](https://kentcdodds.com/blog/common-testing-mistakes) — Kent C. Dodds, *published 2018-09-25, updated*, retrieved 2026-04-30. Background for the "no-test" warning being warning-not-error.

## Examples

### ✓ Pass

```ts
// src/utils/format.ts
export function formatCurrency(n: number): string { ... }
```

```ts
// src/utils/format.test.ts (vitest auto-discovers)
import { formatCurrency } from "./format";
describe("formatCurrency", () => {
  it("renders dollars", () => {
    expect(formatCurrency(1234)).toBe("$1,234.00");
  });
});
```

### ✗ Fail

```ts
// src/components/Login.tsx — exported component, no Login.test.tsx
// → V10-NO-TEST (warning)

export function Login() { return <div>...</div>; }
```

```
vitest output:
{ "testResults": [{ "assertionResults": [
    { "status": "failed", "title": "renders dollars", "failureMessages": [...] }
] }]}
→ V10-TEST-FAIL (error). After 3 turns, V10-REPEATED-FAIL.
```
