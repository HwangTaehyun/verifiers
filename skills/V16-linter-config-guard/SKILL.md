# V16 — linter-config-guard

> **Owner**: `hooks/validators/linter_config_guard.py`
> **Tier**: 3 (Stop) only — `file_patterns = []` (project-wide analysis with no per-file mode).
> **File patterns**: empty.

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V16-NO-LINTER-CONFIG` | warning | A language is detected in the project (Go / Python / TS) but its linter config file is missing. |
| `V16-MISSING-ERROR-RULES` | warning | The linter config exists but disables core error-handling rules (`errcheck`, `E722`, `no-empty`). |
| `V16-MISSING-UNUSED-RULES` | warning | Core unused-code rules disabled (`unused`, `F401`, `no-unused-vars`). |
| `V16-MISSING-SECURITY-RULES` | warning | Core security rules disabled (`gosec`, Bandit `S*`, `no-eval`). |

## Why this verifier exists

"Has a linter config" ≠ "is actually linted". A `.golangci.yml` that disables `errcheck` is worse than no config: it gives the *appearance* of safety while permitting the most common Go bug (ignoring an `error` return). Same for ESLint with `no-unused-vars: off`, or ruff with all `S*` (Bandit-like security) rules disabled.

V16's whole job is to pull this curtain back. It's not "you should turn on every rule"; it's "**you should not turn off the load-bearing rules** (errors, unused, security) that the linter exists to enforce".

## Design rationale

- **Stop-only.** Linter config doesn't change between Edits, so checking on every PostToolUse is wasteful. Once per turn (Stop) is the right cadence.
- **Detect-then-check.** V16 first detects which languages the project uses (presence of `.go` files / `pyproject.toml` / `package.json`) and only checks the configs for those languages. A frontend-only project doesn't get a Go warning.
- **Three categorical checks (error / unused / security), not per-rule.** A finer-grained rule set would generate noise; the categorical check captures the real concern: did you turn off the *kind* of check that matters?
- **Warning, not error.** Some rules legitimately need to be off (e.g., `errcheck` against `defer rows.Close()` cases). Hard-failing would be wrong; warning surfaces the disabled rule for review.
- **Per-language config discovery is layered.** ESLint goes through `.eslintrc.*` → `eslint.config.{js,ts,mjs}` → `package.json` `eslintConfig`. The validator tries each in order.

## How it checks (implementation)

Lives in `hooks/validators/linter_config_guard.py`. `validate_project` is the only entry point.

### Top-level dispatch

```python
def validate_project(self, ctx):
    findings: list[Finding] = []
    root = ctx.project_root

    # Go
    if _has_go_files(root):
        config = _find_golangci_config(root)
        if config is None:
            yield Finding(
                severity="warning",
                rule="V16-NO-LINTER-CONFIG",
                message="Go project has no golangci-lint config (.golangci.yml).",
                fix="Create a .golangci.yml with at least errcheck, unused, gosec enabled.",
                ...
            )
        else:
            findings.extend(_check_golangci(config))

    # Python
    if _has_python_files(root):
        ruff_config = _find_ruff_config(root)
        if ruff_config is None:
            yield Finding(rule="V16-NO-LINTER-CONFIG", ...)
        else:
            findings.extend(_check_ruff(ruff_config))

    # TypeScript
    if _has_ts_files(root):
        eslint_config = _find_eslint_config(root)
        if eslint_config is None:
            yield Finding(rule="V16-NO-LINTER-CONFIG", ...)
        else:
            findings.extend(_check_eslint(eslint_config))

    return findings
```

### `_check_golangci(config_path)` — Go

```python
data = yaml.safe_load(config_path.read_text()) or {}
linters = data.get("linters", {})
disable_set = set(linters.get("disable") or [])

# If `disable-all: true`, only what's in `enable` is on
if linters.get("disable-all"):
    enabled = set(linters.get("enable") or [])
    if "errcheck" not in enabled:
        yield Finding(rule="V16-MISSING-ERROR-RULES", message="errcheck not enabled", ...)
    if "unused" not in enabled:
        yield Finding(rule="V16-MISSING-UNUSED-RULES", message="unused not enabled", ...)
    if "gosec" not in enabled:
        yield Finding(rule="V16-MISSING-SECURITY-RULES", message="gosec not enabled", ...)
else:
    if "errcheck" in disable_set:
        yield Finding(rule="V16-MISSING-ERROR-RULES", message="errcheck explicitly disabled", ...)
    if "unused" in disable_set:
        yield Finding(rule="V16-MISSING-UNUSED-RULES", ...)
    if "gosec" in disable_set:
        yield Finding(rule="V16-MISSING-SECURITY-RULES", ...)
```

### `_check_ruff(config_path)` — Python

```python
# config_path can be ruff.toml, pyproject.toml [tool.ruff], or .ruff.toml
data = _load_ruff_config(config_path)
ignore = set(data.get("ignore") or []) | set(data.get("lint", {}).get("ignore") or [])
select = set(data.get("select") or []) | set(data.get("lint", {}).get("select") or [])

# Heuristic: if the user is on the explicit-select model and didn't pick
# E/F/S/B at all, that's a missing category
if select and not (select & {"E", "ALL"}):
    yield Finding(rule="V16-MISSING-ERROR-RULES", ...)
if select and not (select & {"F", "ALL"}):
    yield Finding(rule="V16-MISSING-UNUSED-RULES", ...)
if select and not (select & {"S", "ALL"}):
    yield Finding(rule="V16-MISSING-SECURITY-RULES", ...)

# Or: explicit ignore of E722 / F401 / S* etc.
if "E722" in ignore:
    yield Finding(rule="V16-MISSING-ERROR-RULES", ...)
if "F401" in ignore:
    yield Finding(rule="V16-MISSING-UNUSED-RULES", ...)
if any(r.startswith("S") for r in ignore):
    yield Finding(rule="V16-MISSING-SECURITY-RULES", ...)
```

### `_check_eslint(config_path)` — TypeScript

```python
# ESLint can be JS/TS/JSON/YAML; for non-JS we parse with ast/yaml/json,
# for JS/TS we fall back to regex on the rules block.
rules = _extract_eslint_rules(config_path)

if rules.get("no-empty") in ("off", 0):
    yield Finding(rule="V16-MISSING-ERROR-RULES", ...)
if rules.get("no-unused-vars") in ("off", 0) \
   and rules.get("@typescript-eslint/no-unused-vars") in ("off", 0):
    yield Finding(rule="V16-MISSING-UNUSED-RULES", ...)
if rules.get("no-eval") in ("off", 0):
    yield Finding(rule="V16-MISSING-SECURITY-RULES", ...)
```

### Could be more effective

- **Run the linter once and inspect the active rule set.** `golangci-lint linters` and `eslint --print-config <file>` both emit the actual effective config, including extends/inherit chains. Checking against the *resolved* config catches the "inherits a default that disables errcheck" case V16 currently misses.
- **`.eslintignore` and per-rule overrides.** A rule on globally is fine, but per-folder `overrides: [{ files: "src/legacy/**", rules: { "no-unused-vars": "off" }}]` is exactly the kind of footgun V16 should surface.
- **Severity-level inspection.** `errcheck: warning` is *worse* than enabled-as-error but better than disabled. Tracking severity, not boolean enable, gives a third tier of granularity.
- **Linter version pin.** `golangci-lint v1.40` and `v1.55` ship different default rule sets. A future V##: enforce that `.golangci.yml` declares a `lintroller` version, or that `package.json` pins ESLint. Not V16's lane.

## References

- [golangci-lint — Configuration](https://golangci-lint.run/usage/configuration/) — golangci-lint contributors, *continuously updated*, retrieved 2026-04-30. Source for the `linters.disable` / `linters.enable` / `disable-all` semantics V16 reads.
- [Ruff — Settings (`select`, `ignore`)](https://docs.astral.sh/ruff/configuration/) — Astral, *continuously updated*, retrieved 2026-04-30.
- [Ruff — Rules](https://docs.astral.sh/ruff/rules/) — Astral, *continuously updated*, retrieved 2026-04-30. Categorical prefixes (`E`, `F`, `S`, `B`) V16 maps to error/unused/security.
- [ESLint — `--print-config`](https://eslint.org/docs/latest/use/command-line-interface#--print-config) — OpenJS Foundation, *continuously updated*, retrieved 2026-04-30. Reference upgrade path for resolved-config inspection.
- [Bandit — Test list (`S101`-`S612`)](https://bandit.readthedocs.io/en/latest/plugins/index.html) — PyCQA, *continuously updated*, retrieved 2026-04-30. The `S*` rule prefix Ruff inherits.
- [Errcheck README](https://github.com/kisielk/errcheck) — Kamil Kisiel, *continuously maintained*, retrieved 2026-04-30. Why `errcheck` is the load-bearing Go rule.

## Examples

### ✓ Pass

```yaml
# .golangci.yml
linters:
  enable:
    - errcheck
    - unused
    - gosec
    - govet
    - staticcheck
```

```toml
# pyproject.toml
[tool.ruff.lint]
select = ["E", "F", "B", "S", "W", "I"]
```

### ✗ Fail

```yaml
# .golangci.yml
linters:
  disable-all: true
  enable:
    - govet                # → V16-MISSING-ERROR-RULES (errcheck not enabled)
                           # → V16-MISSING-UNUSED-RULES (unused not enabled)
                           # → V16-MISSING-SECURITY-RULES (gosec not enabled)
```

```json
// .eslintrc.json
{
  "rules": {
    "no-unused-vars": "off"   // → V16-MISSING-UNUSED-RULES
  }
}
```
