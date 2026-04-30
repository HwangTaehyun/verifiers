# V54 — commitlint-gate

> **Owner**: `hooks/validators/commitlint_gate.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `**/package.json`, `commitlint.config.*`, `.pre-commit-config.yaml`, `.pre-commit-config.yml`, `lefthook.yml`, `.husky/**`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V54-COMMITLINT-NOT-ENFORCED` | warning | Project **consumes** conventional commits (changelog generator in deps/scripts OR Keep-a-Changelog-formatted `CHANGELOG.md`) but has **no** enforcement gate (no commitlint config, no husky commit-msg hook, no lefthook commit-msg, no pre-commit conventional hook). |

## Why this verifier exists

Conventional Commits ([conventionalcommits.org v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) — *published 2019-04-01, retrieved 2026-04-30*) provides a structured commit message format (`feat:`, `fix:`, `chore:` …) that changelog generators parse to produce release notes automatically. When a project adopts a tool such as `conventional-changelog` or maintains a [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — *spec v1.1.0 published 2019-08-23, retrieved 2026-04-30* formatted `CHANGELOG.md`, it implicitly depends on every contributor following the convention.

Without an enforcement gate the contract is invisible:

1. **Silent changelog gaps.** A commit like `"fix auth bug"` is dropped from the auto-generated changelog because it has no `fix:` prefix. The release notes appear complete but omit real fixes.
2. **Reviewer blind spot.** A PR reviewer cannot tell whether a commit message will affect the next release.
3. **Onboarding friction.** New contributors are not told about the convention until their first changelog-breaking PR is merged.

[commitlint](https://commitlint.js.org/) — *continuously developed since 2017-01, retrieved 2026-04-30* solves this by running a check at `commit-msg` time, before the commit is ever recorded. V54 detects the gap and offers three concrete remediation paths.

## Design rationale

- **Only fires when consumption is proven.** The validator never penalises projects that do not use conventional commits. It only fires when the project already invested in the convention (changelog tooling or a formatted CHANGELOG.md).
- **Multiple consumption signals.** Both tooling-based (`conventional-changelog` in `dependencies`/`devDependencies` or in `scripts`) and artifact-based (a `CHANGELOG.md` that matches Keep-a-Changelog headers) count as proof of consumption. Either alone is sufficient.
- **Multiple enforcement signals.** Any one of commitlint config, `.husky/commit-msg`, `lefthook.yml commit-msg:`, or `pre-commit conventional-pre-commit` repo counts as enforced. Teams pick their preferred toolchain.
- **Walks all `package.json` files.** Monorepos place `package.json` in `server/`, `web/`, root, etc. The validator collects every one and checks all of them for changelog generators.
- **YAML parse failure is non-fatal.** A malformed `lefthook.yml` or `.pre-commit-config.yaml` is logged and treated as absent for enforcement purposes. The project is not penalised for a parse error.

## How it checks

Lives in `hooks/validators/commitlint_gate.py`.

### Step 1 — detect consumption

```python
# Search every package.json under root
for pkg_path in root.rglob("package.json"):
    data = json.loads(pkg_path.read_text())
    all_deps = {**data.get("dependencies", {}),
                **data.get("devDependencies", {})}
    scripts = " ".join(data.get("scripts", {}).values())
    if any("conventional-changelog" in k for k in all_deps) or \
       "conventional-changelog" in scripts:
        CONSUMES = True

# Heuristic: CHANGELOG.md with Keep-a-Changelog headers
changelog = root / "CHANGELOG.md"
if changelog.is_file():
    text = changelog.read_text()
    if re.search(r"^## \[Unreleased\]|^## \[\d+\.\d+", text, re.MULTILINE):
        CONSUMES = True
```

### Step 2 — detect enforcement

Any one of the following is sufficient:

| Signal | File / pattern |
|---|---|
| commitlint config | `commitlint.config.{js,ts,cjs,mjs,json}` at root |
| commitlint rc | `.commitlintrc.{js,ts,json,yml,yaml,cjs,mjs}` at root |
| husky hook | `.husky/commit-msg` exists |
| lefthook | `lefthook.yml` contains `commit-msg:` |
| pre-commit | `.pre-commit-config.yaml` contains `conventional-pre-commit` |
| deps | `commitlint` in any `package.json` deps |

### Step 3 — emit finding

```python
if CONSUMES and not ENFORCED:
    yield Finding(rule="V54-COMMITLINT-NOT-ENFORCED", severity="warning", ...)
elif not CONSUMES:
    return []   # out of scope
```

Both `validate_file` and `validate_project` delegate to `_check(ctx)` so Tier 2 and Tier 3 behave identically.

## Could be more effective

- **Detect partial enforcement.** A project might have commitlint installed but no husky hook wired — the binary exists but nothing calls it at commit time. A deeper check would verify that `.husky/commit-msg` actually invokes `commitlint`.
- **Scope per-package in monorepos.** A root changelog generator combined with per-package conventional commits could be enforced per workspace. Currently a single enforcement signal satisfies the whole repo.
- **Warn on `commitlint` without `@commitlint/config-conventional`.** Installing the CLI without an `extends` config produces silent pass-through (no rules active). Could detect an empty or missing `extends` array.
- **Detect `standard-version` / `semantic-release`.** These tools also consume conventional commits and could be recognised as additional consumption signals.
- **Stricter: check `commit-msg` is executable.** On Linux/macOS a non-executable `.husky/commit-msg` is silently skipped by git. Could `os.access(..., os.X_OK)`.

## References

- [Conventional Commits v1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) — specification for human- and machine-readable commit messages — *published 2019-04-01, retrieved 2026-04-30*
- [commitlint](https://commitlint.js.org/) — lint tool that enforces the Conventional Commits spec at commit-msg time — *continuously developed since 2017-01, retrieved 2026-04-30*
- [Keep a Changelog v1.1.0](https://keepachangelog.com/en/1.1.0/) — format spec for human-readable changelogs that changelog generators target — *spec v1.1.0 published 2019-08-23, retrieved 2026-04-30*
- [conventional-changelog](https://github.com/conventional-changelog/conventional-changelog) — Node.js changelog generator that parses conventional commits — *continuously developed since 2014-07, retrieved 2026-04-30*
- [lefthook](https://github.com/evilmartians/lefthook) — Git hooks manager (Go-native alternative to husky) — *continuously developed since 2019-01, retrieved 2026-04-30*

## Examples

### Pass — commitlint.config.js present

```js
// commitlint.config.js
export default { extends: ['@commitlint/config-conventional'] };
```

### Pass — lefthook.yml with commit-msg hook

```yaml
# lefthook.yml
commit-msg:
  commands:
    commitlint:
      run: bunx commitlint --edit {1}
```

### Pass — .husky/commit-msg exists

```sh
#!/bin/sh
bunx commitlint --edit "$1"
```

### Pass — pre-commit with conventional-pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/compilerla/conventional-pre-commit
    rev: v3.4.0
    hooks:
      - id: conventional-pre-commit
```

### Fail — conventional-changelog in devDeps, no enforcement

```json
// package.json
{
  "devDependencies": {
    "conventional-changelog-cli": "^4.1.0"
  }
}
// No commitlint.config.js, no .husky/commit-msg, no lefthook.yml commit-msg
// → V54-COMMITLINT-NOT-ENFORCED
```

### Fail — CHANGELOG.md with Keep-a-Changelog headers, no enforcement

```markdown
# Changelog
## [Unreleased]
## [1.2.0] - 2026-01-15
```

```
# No commitlint config present
→ V54-COMMITLINT-NOT-ENFORCED
```

### Not in scope

```
# package.json has no conventional-changelog dependency
# CHANGELOG.md does not exist (or has no ## [Unreleased] / ## [N.N.N] headers)
→ No findings (project does not consume conventional commits)
```
