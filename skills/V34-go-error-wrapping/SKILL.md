# V34 — go-error-wrapping

> **Owner**: `hooks/validators/error_wrapping.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse) — runs on `.go` file edits under `cmd/` and `internal/`
> **File patterns**: `**/*.go` (excludes `**/gen/**`, `**/*.generated.go`)

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V34-BARE-ERROR-RETURN` | warning | A `return err` or `return ..., err` statement exists in non-generated `.go` files where the preceding statement is not a wrapping call (`fmt.Errorf("...: %w", ...)` / `errors.New(...)` / `connect.NewError(...)`). |

## Why this verifier exists

Go 1.13+ introduced error wrapping via the `%w` verb in `fmt.Errorf`. This enables error inspection chains: downstream code can call `errors.Is(err, ErrFoo)` or `errors.As(err, &detail)` to understand what went wrong.

Bare error returns — `return err` without wrapping — lose all context. Callers see the leaf error only. Example from `server/cmd/normalize-cmf/main.go:113,117,275,279,284,287,307` — 7 bare returns in production code:

```go
pdf, err := renderer.Render(ctx, cmf)  // from S3, may timeout or 500
if err != nil {
    return err  // ← Lost: did S3 timeout? Did PDF fail? Caller has no idea.
}
```

Best practice: every error return adds context:

```go
pdf, err := renderer.Render(ctx, cmf)
if err != nil {
    return fmt.Errorf("failed to render CMF to PDF: %w", err)
}
```

Now the error chain is readable, and `errors.Is(err, io.ErrTimeout)` works.

V34 catches bare returns at commit-time so the error chain stays intact.

**Primary citation**: [Go blog: Working with Errors in Go 1.13](https://go.dev/blog/go1.13-errors) — published 2019-10-17, retrieved 2026-04-30.

## Design rationale

- **Scope: `cmd/` and `internal/` only.** These are user-facing and internal library code. Generated files (`gen/*.go`, `*.generated.go`) are excluded — they're not hand-written.
- **Severity: warning, not error.** Wrapping is best practice but has legitimate exceptions:
  - A function at the program root that doesn't wrap upward (nothing reads it) can bare-return.
  - Propagating sentinel errors (`ErrNotFound`, `ErrAuthFailed`) may intentionally skip wrapping.
  - A helper that calls one leaf function and re-returns its error may be designed as a pass-through.
- **Three wrapping patterns counted as OK:**
  - `fmt.Errorf("describe: %w", err)` — explicit wrapping.
  - `errors.New(...)` — new error (not a return pass-through).
  - `connect.NewError(code, err)` — Connect-RPC wrapping (includes metadata).
- **Previous statement is the filter.** If the line before `return err` is not a wrapping call, the rule fires. This avoids false positives where `err` is already wrapped inside a function call (e.g., `return someFunc(err)` where `someFunc` does the wrapping).
- **Same-line wrapping short-circuits.** A return like `return fmt.Errorf("fail: %w", err)` (wrapping on the return line itself) is correct and doesn't fire the rule.

## How it checks (implementation plan)

Lives in `hooks/validators/error_wrapping.py`.

### Top-level

```python
def validate_file(self, file_path, ctx):
    if not self._is_eligible(file_path):
        return []
    return self._check_bare_returns(file_path, ctx)

def _is_eligible(self, file_path: Path) -> bool:
    """Skip generated files."""
    return (
        file_path.suffix == ".go"
        and "/gen/" not in str(file_path)
        and not file_path.name.endswith(".generated.go")
    )
```

### `_check_bare_returns(file_path, ctx)` — V34-BARE-ERROR-RETURN

```python
BARE_RETURN = re.compile(
    r"^\s*return\s+(?:(?:[^,\s]+\s*,\s*)*)?err\b",
    re.MULTILINE
)

WRAPPING_PATTERNS = (
    r"\bfmt\.Errorf\s*\(",
    r"\berrors\.New\s*\(",
    r"\bconnect\.NewError\s*\(",
)

WRAPPING_REGEX = re.compile("|".join(WRAPPING_PATTERNS))

lines = file_path.read_text().splitlines(keepends=True)

for i, line in enumerate(lines):
    if BARE_RETURN.search(line):
        # Check previous line for wrapping
        if i > 0:
            prev_line = lines[i - 1]
            if not WRAPPING_REGEX.search(prev_line):
                yield Finding(
                    rule="V34-BARE-ERROR-RETURN",
                    file=str(file_path),
                    line=i + 1,
                    message=f"bare error return; wrap with fmt.Errorf(...%w...)",
                )
        else:
            # First line with bare return — unlikely but flag it
            yield Finding(
                rule="V34-BARE-ERROR-RETURN",
                file=str(file_path),
                line=i + 1,
            )
```

## Could be more effective

- **AST-based receiver detection.** Current regex can't distinguish `return err` from `return err, nil` or multi-value returns. `go/parser` would be precise.
- **Dataflow analysis.** Track where `err` came from. If it's from a function that's already guaranteed-wrapped (e.g., `errors.Wrap`), skip the flag.
- **Leaf function exemption.** A function that calls only one error-returning function could be marked `// go:noinline` or `// v34:leaf` to exempt it from wrapping. Not yet implemented.
- **Sentinel-error registry.** Build a list of project-specific `ErrFoo` constants from the codebase and allow bare-returning them without wrapping.
- **Context-aware wrapping.** If a function already returns context via a separate field (e.g., `errCode int`), wrapping may be redundant. Could model this via config.

## References

- [Go blog: Working with Errors in Go 1.13](https://go.dev/blog/go1.13-errors) — published 2019-10-17, retrieved 2026-04-30. Introduces the `%w` verb and error-wrapping pattern.
- [pkg.go.dev: fmt.Errorf](https://pkg.go.dev/fmt#Errorf) — continuously updated, retrieved 2026-04-30. The `%w` formatting verb.
- [pkg.go.dev: errors.Is and errors.As](https://pkg.go.dev/errors#Is) — continuously updated, retrieved 2026-04-30. How wrapped errors are inspected.
- [Dave Cheney: Don't just check errors, handle them gracefully](https://dave.cheney.net/2016/04/27/dont-just-check-errors-handle-them-gracefully) — published 2016-04-27, retrieved 2026-04-30. Foundational error-handling philosophy.

## Examples

### ✓ Pass

```go
// server/cmd/normalize-cmf/main.go
pdf, err := renderer.Render(ctx, cmf)
if err != nil {
    return fmt.Errorf("failed to render CMF to PDF: %w", err)  // ✓ wrapped
}
return pdf, nil
```

```go
// server/internal/finance/invoice.go
inv, err := repo.Find(ctx, id)
if err != nil {
    if errors.Is(err, sql.ErrNoRows) {
        return nil, errors.New("invoice not found")  // ✓ explicit new error
    }
    return nil, fmt.Errorf("find invoice: %w", err)  // ✓ wrapped
}
return inv, nil
```

```go
// server/internal/api/handler.go
user, err := s.userService.Get(ctx, id)
if err != nil {
    return nil, connect.NewError(connect.CodeNotFound, err)  // ✓ Connect wrapping
}
return user, nil
```

### ✗ Fail

```go
// server/cmd/normalize-cmf/main.go
pdf, err := renderer.Render(ctx, cmf)
if err != nil {
    return err  // → V34-BARE-ERROR-RETURN (no context added)
}
```

```go
// server/internal/finance/invoice.go
inv, err := repo.Find(ctx, id)
if err != nil {
    return nil, err  // → V34-BARE-ERROR-RETURN (caller sees only leaf error)
}
```
