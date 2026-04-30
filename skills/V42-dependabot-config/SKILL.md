# V42 — dependabot-config

> **Owner**: `hooks/validators/dependabot_config.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `.github/dependabot.yml`, `.github/renovate.json`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V42-NO-DEPENDABOT` | warning | `.github/dependabot.yml` and `.github/renovate.json` both absent, OR `dependabot.yml` exists but missing required package ecosystems (`gomod`, `npm`, `github-actions`). |

## Why this verifier exists

Multi-ecosystem monorepos (Go modules + Node.js + GitHub Actions) accumulate dependency drift silently:

1. **CVE backlog invisible.** A Go module inherits a critical CVE; without automated PRs, the team doesn't discover it until incident.
2. **Ecosystem gaps.** A project declares `npm` dependencies for scanning but forgets `gomod` and `github-actions`. Two-thirds of the surface area receives zero automated dependency updates.
3. **Compliance & audit.** Medical and fintech contexts require demonstrable dependency freshness. Manual sporadic updates produce audit friction. Automated weekly/monthly PR flows show systematic control.

V42 enforces `.github/dependabot.yml` (GitHub's native choice) or `.github/renovate.json` (alternative), covering all three ecosystems with a documented schedule, so CVE response is fast and auditable.

Evidence: `ai-project-template/.github/` contains only `workflows/` — no `dependabot.yml`, no `renovate.json`. The repo has Go modules (`server/go.mod`), Bun/npm (`web/package.json`), and GitHub Actions — none receive automated dependency PRs (verified at `/Users/taehyun/github/ai-project-template/.github/`).

## Design rationale

- **Dependabot is the default enforcement target.** GitHub's native tool requires zero extra setup; Renovate is acknowledged as an alternative for projects that prefer it.
- **Three ecosystems are mandatory.** `gomod` (Go modules), `npm` (covers Bun and npm), `github-actions` (workflow runner dependencies). A monorepo incomplete on any one of these has a gap.
- **Schedule is opinionated.** `interval: weekly` is the default recommendation. Medical and fintech may prefer `daily`; `monthly` is acceptable for stable projects. The key: explicit, not ad-hoc.
- **Ecosystem name is case-sensitive.** Dependabot uses lowercase: `gomod`, `npm`, `docker`, `github-actions`. A typo (`Go-Mod`, `NPM`) silently disables scanning.
- **Version is not enforced.** Dependabot API evolves; V42 just ensures the file exists and declares the three core ecosystems.

## How it checks (implementation plan)

Lives in `hooks/validators/dependabot_config.py`.

### Top-level gate

```python
def validate_project(self, ctx):
    # Only runs on root of repo (not on individual file changes)
    github_dir = ctx.root / ".github"
    if not github_dir.exists():
        yield Finding(rule="V42-NO-DEPENDABOT", ...)
        return
    
    dependabot_path = github_dir / "dependabot.yml"
    renovate_path = github_dir / "renovate.json"
    
    if not dependabot_path.exists() and not renovate_path.exists():
        yield Finding(rule="V42-NO-DEPENDABOT", ...)
        return
    
    # If dependabot.yml exists, check for required ecosystems
    if dependabot_path.exists():
        yield from self._check_dependabot_content(dependabot_path)
```

### `_check_dependabot_content(path)` — V42-NO-DEPENDABOT

```python
REQUIRED_ECOSYSTEMS = {"gomod", "npm", "github-actions"}

def _check_dependabot_content(self, path):
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        yield Finding(rule="V42-NO-DEPENDABOT", 
                      message="dependabot.yml: invalid YAML",
                      file=str(path))
        return
    
    if not isinstance(data, dict) or "updates" not in data:
        yield Finding(rule="V42-NO-DEPENDABOT",
                      message="dependabot.yml: missing 'updates' key",
                      file=str(path))
        return
    
    updates = data.get("updates", [])
    if not isinstance(updates, list):
        return
    
    declared = {u.get("package-ecosystem") for u in updates 
                if isinstance(u, dict)}
    
    missing = REQUIRED_ECOSYSTEMS - declared
    if missing:
        yield Finding(rule="V42-NO-DEPENDABOT",
                      message=f"Missing ecosystems: {', '.join(sorted(missing))}",
                      file=str(path))
```

### Could be more effective

- **Detect stale config.** A `dependabot.yml` with `interval: monthly` for a security-critical project could be flagged as too slow; a config knob `dependabot.min_frequency: weekly` would enforce stricter SLAs.
- **Version pin freshness.** Renovate and Dependabot have major version releases; a `renovate.json` locked to schema v2 when v3+ is available is silently out of support. Could detect and warn.
- **Ecosystem-specific schedules.** GitHub Actions may need `daily` but npm `weekly`. Currently V42 only checks presence; per-ecosystem schedule validation is out of scope.
- **Open vs. pinned PRs.** A config with `open-pull-requests-limit: 1` may bottleneck a team that needs parallel PRs. Could suggest tuning.
- **Auto-merge rules.** Projects that want hands-free dependency updates (patch-only auto-merge) could declare that intent; V42 doesn't validate auto-merge config yet.

## References

- [Dependabot — Configuring version updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuring-dependabot-version-updates) — GitHub, *continuously updated*, retrieved 2026-04-30. The canonical reference for `.github/dependabot.yml` structure and supported ecosystems.
- [Dependabot — Package ecosystems](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates#about-compatibility-with-your-repositories) — GitHub, *continuously updated*, retrieved 2026-04-30. Official list of supported ecosystems including `gomod`, `npm`, `github-actions`.
- [Renovate documentation](https://docs.renovatebot.com/) — Renovate maintainers, *continuously updated*, retrieved 2026-04-30. Alternative to Dependabot; same semantic scope (multiple ecosystems, scheduled PR cadence).
- [OWASP — Vulnerable and Outdated Components](https://owasp.org/www-project-top-ten/2021/A06_2021-Vulnerable_and_Outdated_Components/) — OWASP, *published 2021-10*, retrieved 2026-04-30. Security rationale for automated dependency scanning.
- [CIS Software Supply Chain Security](https://www.cisecurity.org/controls/cis-controls-v8-2-1) — CIS, *published 2023-03*, retrieved 2026-04-30. Industry expectation for dependency freshness tracking.

## Examples

### ✓ Pass

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: gomod
    directory: /server
    schedule:
      interval: weekly
  
  - package-ecosystem: npm
    directory: /web
    schedule:
      interval: weekly
  
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
```

### ✗ Fail

```yaml
# .github/dependabot.yml — missing npm and github-actions
version: 2
updates:
  - package-ecosystem: gomod
    directory: /server
    schedule:
      interval: weekly
# → V42-NO-DEPENDABOT (missing npm, github-actions)
```

```
# No .github/dependabot.yml or .github/renovate.json present
→ V42-NO-DEPENDABOT
```

```yaml
# .github/dependabot.yml — typo in ecosystem name
version: 2
updates:
  - package-ecosystem: Go-Mod      # Should be 'gomod'
    directory: /server
    schedule:
      interval: weekly
  - package-ecosystem: NPM          # Should be 'npm'
    directory: /web
    schedule:
      interval: weekly
# → V42-NO-DEPENDABOT (gomod and npm not found)
```
