# V38 — golangci-strictness

> **Owner**: `hooks/validators/golangci_strictness.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `**/.golangci.{yaml,yml}`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V38-NO-WRAPCHECK` | error | `.golangci.yaml` does not have `wrapcheck` in `linters.enable` list. |
| `V38-WEAK-NOLINTLINT` | error | `.golangci.yaml` has `nolintlint` section but either `require-specific: true` or `require-explanation: true` is not set (both required). |
| `V38-NO-GOFUMPT` | warning | `.golangci.yaml` does not have `gofumpt` in `linters.enable` list. |

## Why this verifier exists

Three linter-strictness gaps emerge in medical/finance codebases where error handling and code consistency are high-stakes:

1. **Missing `wrapcheck` → silent sentinel errors.** A handler returns `return err` instead of `return fmt.Errorf("%w", err)` and the error chain is broken. `wrapcheck` catches this at lint time, but only if enabled. Evidence: `server/.golangci.yaml` line ~50 lists 15 linters in `enable:` but omits `wrapcheck`. This is the automation equivalent of V34's manual check — it should never ship uncaught.

2. **Weak `nolintlint` → suppression audit trail vanishes.** A developer writes `//nolint` without naming the specific linter or providing a reason. Reviewers cannot audit why the suppression exists ("Was this a real false positive, or did someone just skip the check?"). Evidence: `server/.golangci.yaml` lines 125–127 show `nolintlint` enabled but both `require-specific: false` and `require-explanation: false`, making the tool a no-op. Every `//nolint` should require `//nolint:lintername // reason` so the audit trail survives.

3. **Missing `gofumpt` → inconsistent formatting.** `gofmt` is standard but `gofumpt` enforces stricter grouping rules (imports, struct fields, function arguments). Without it, developers use their local `gofmt`/editor settings and commits have spurious formatting diffs. Not a hard blocker (hence warning), but consistency matters in large teams.

[golangci-lint linters documentation](https://golangci-lint.run/usage/linters/) — continuously updated, retrieved 2026-04-30. [gofumpt README](https://github.com/mvdan/gofumpt) — continuously developed since 2019-11, retrieved 2026-04-30.

## Design rationale

- **`wrapcheck` and `nolintlint` are errors.** Both directly impact auditability and error semantics in regulated domains. If either is misconfigured, the codebase silently loses safety guarantees.
- **`gofumpt` is warning-only.** It's a style concern, not a correctness issue. Projects may legitimately prefer standard `gofmt`.
- **`nolintlint` requires BOTH settings.** Just `require-specific` without `require-explanation` (or vice versa) still allows bare `//nolint` if you only check one. Both must be `true` for full auditability.
- **Linter presence is checked via `enable:` key only.** The validator does not verify the full configuration (e.g., nested settings under each linter); it only ensures the linter is registered.

## How it checks (implementation plan)

Lives in `hooks/validators/golangci_strictness.py`. Operates on any `.golangci.yaml` or `.golangci.yml` in the project.

### Top-level check

```python
def _all_checks(self, ctx, file_path):
    findings = []
    config = yaml.safe_load(file_path.read_text())
    
    findings.extend(self._check_wrapcheck(config, file_path))
    findings.extend(self._check_nolintlint(config, file_path))
    findings.extend(self._check_gofumpt(config, file_path))
    
    return findings
```

### `_check_wrapcheck` — V38-NO-WRAPCHECK

```python
def _check_wrapcheck(self, config, file_path):
    linters = config.get("linters", {}).get("enable", [])
    if "wrapcheck" not in linters:
        yield Finding(
            rule="V38-NO-WRAPCHECK",
            file=str(file_path),
            message="wrapcheck linter not enabled in linters.enable list"
        )
```

### `_check_nolintlint` — V38-WEAK-NOLINTLINT

```python
def _check_nolintlint(self, config, file_path):
    # nolintlint is enabled
    linters = config.get("linters", {}).get("enable", [])
    if "nolintlint" not in linters:
        return  # If nolintlint itself is off, skip check
    
    # Check its settings
    nolintlint_cfg = config.get("linters-settings", {}).get("nolintlint", {})
    require_specific = nolintlint_cfg.get("require-specific", False)
    require_explanation = nolintlint_cfg.get("require-explanation", False)
    
    if not (require_specific and require_explanation):
        yield Finding(
            rule="V38-WEAK-NOLINTLINT",
            file=str(file_path),
            message="nolintlint requires both require-specific: true and require-explanation: true"
        )
```

### `_check_gofumpt` — V38-NO-GOFUMPT

```python
def _check_gofumpt(self, config, file_path):
    linters = config.get("linters", {}).get("enable", [])
    if "gofumpt" not in linters:
        yield Finding(
            rule="V38-NO-GOFUMPT",
            file=str(file_path),
            severity="warning",
            message="gofumpt linter not enabled; consider enabling for stricter formatting"
        )
```

## Could be more effective

- **Validate full linter settings.** Currently checks only presence in `enable:`. Could validate nested config (e.g., `nolintlint.allow-unused: false` for maximum strictness).
- **Check other high-value linters.** `bodyclose`, `errcheck`, `vet` are also critical for correctness. Could expand the rules.
- **Detect linter version drifts.** Different versions of golangci-lint ship different linter implementations. Could pin a minimum version.
- **Per-directory overrides.** `.golangci.yaml` allows directory-scoped rule disables; could flag overly permissive overrides.
- **Cross-reference with actual `//nolint` usage.** Count existing `//nolint` comments and warn if `nolintlint` is weak but the codebase has many suppressions.

## References

- [golangci-lint — Linters](https://golangci-lint.run/usage/linters/) — golangci-lint Authors, *continuously updated*, retrieved 2026-04-30. Official linter catalog including `wrapcheck`, `nolintlint`, `gofumpt`.
- [golangci-lint — Configuration](https://golangci-lint.run/usage/configuration/) — golangci-lint Authors, *continuously updated*, retrieved 2026-04-30. The YAML schema and `linters.enable` / `linters-settings` structure.
- [wrapcheck — GitHub](https://github.com/tomarrell/wrapcheck) — tomarrell, *continuously developed since 2021-01*, retrieved 2026-04-30. Automated error-wrapping enforcement.
- [nolintlint — GitHub](https://github.com/golangci/nolintlint) — golangci, *continuously developed since 2019-10*, retrieved 2026-04-30. Linter-suppression auditing.
- [gofumpt — GitHub](https://github.com/mvdan/gofumpt) — mvdan, *continuously developed since 2019-11*, retrieved 2026-04-30. Strict Go formatting.

## Examples

### ✓ Pass

```yaml
# .golangci.yaml
linters:
  enable:
    - errcheck
    - gosimple
    - govet
    - ineffassign
    - staticcheck
    - typecheck
    - unused
    - wrapcheck                  # ✓ Present
    - gofumpt                    # ✓ Present
    - nolintlint

linters-settings:
  nolintlint:
    require-specific: true       # ✓ Both required
    require-explanation: true
```

### ✗ Fail

```yaml
# .golangci.yaml
linters:
  enable:
    - errcheck
    - gosimple
    - govet
    # ✗ wrapcheck missing → V38-NO-WRAPCHECK
    # ✗ gofumpt missing → V38-NO-GOFUMPT

linters-settings:
  nolintlint:
    require-specific: false      # ✗ V38-WEAK-NOLINTLINT
    require-explanation: false   # (both must be true)
```

```yaml
# Another fail: nolintlint partially configured
linters:
  enable:
    - wrapcheck
    - gofumpt
    - nolintlint

linters-settings:
  nolintlint:
    require-specific: true
    require-explanation: false   # ✗ V38-WEAK-NOLINTLINT
```
