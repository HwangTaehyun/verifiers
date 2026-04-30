# V52 — readme-badges

> **Owner**: `hooks/validators/readme_badges.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `README.md`, `README.rst`, `readme.md`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V52-NO-CI-BADGE` | info | Root `README.md` exists but contains no CI status badge (GitHub Actions or shields.io workflow variant). |
| `V52-NO-LICENSE-BADGE` | info | Root `README.md` exists but contains no license badge (shields.io github/license or badge/license- variant, or a `[![License]` markdown badge). |

## Why this verifier exists

A project README is the first page most contributors, consumers, and evaluators see. Two pieces of metadata matter immediately:

1. **CI status** — is the main branch currently green? A passing-CI badge answers this at a glance without clicking into the Actions tab. Projects without one force visitors to hunt for build status, which reduces trust and slows down integration decisions.

2. **License terms** — what can I do with this code? Open-source consumers — whether humans evaluating adoption or dependency-scanning tools — need to know the license before integrating. A license badge surfaces this in two seconds; without it, the reader has to open a separate `LICENSE` file or search SPDX identifiers.

Neither absence is a bug in the traditional sense — the project still runs. That is why both rules are `info` severity. The findings are nudges, not blockers. But over many projects and teams they compound: a missing badge is a small friction that, multiplied across all first-time visitors, adds up to real discoverability cost.

## Design rationale

- **Both `validate_file` and `validate_project` delegate to `_check(ctx)`**. The README is a project-level artifact; whether the trigger is "you just edited README.md" (Tier 2) or "stop hook full sweep" (Tier 3), the same single function runs. There is no per-file logic that differs between tiers.
- **Root-only scan**. Only the root-level README is checked. Sub-packages may have their own READMEs (e.g. `packages/cli/README.md`), but the badge convention applies to the project homepage, not every sub-module doc. V52 does not recurse.
- **Case-insensitive filename lookup**. `readme.md` and `README.md` are both accepted. The `_find_readme` helper iterates root children if the canonical casing isn't found.
- **Absent README → silent pass**. If there is no README at all, V52 returns no findings. Other verifiers (V53 covers community files) may flag the missing README itself; V52 is not responsible for README existence.
- **Codecov ≠ CI**. A codecov coverage badge (`codecov.io`) is not a CI status badge. It reflects test coverage, not build pass/fail. V52 is intentionally specific: only GitHub Actions workflow URLs and shields.io workflow/status variants satisfy the CI check.
- **Info severity**. Neither finding blocks a commit or turn. They are surfaced as improvement opportunities, not errors.

## How it checks (implementation)

Lives in `hooks/validators/readme_badges.py`.

### Top-level `_check(ctx)`

```python
def _check(self, ctx):
    readme_path = self._find_readme(ctx.project_root)
    if readme_path is None:
        return []

    content = readme_path.read_text(errors="replace")
    findings = []

    if not _has_ci_badge(content):
        findings.append(Finding(rule="V52-NO-CI-BADGE", ...))

    if not _has_license_badge(content):
        findings.append(Finding(rule="V52-NO-LICENSE-BADGE", ...))

    return findings
```

### CI badge patterns (any match satisfies)

```python
re.compile(r"https://github\.com/.+/actions/workflows/")
re.compile(r"https://img\.shields\.io/github/actions/workflow/status/")
re.compile(r"https://img\.shields\.io/github/workflow/status/")
```

### License badge patterns (any match satisfies)

```python
re.compile(r"https://img\.shields\.io/github/license/")
re.compile(r"https://img\.shields\.io/badge/license-")
re.compile(r"\[!\[License\]")          # generic [![License](...) markdown badge
```

## Could be more effective

- **RST badge syntax**. The current patterns match Markdown `[![...](...)]` syntax. RST badges use `.. image::` directives. V52 currently flags RST READMEs even if they have badges written in RST format. A follow-up could add RST-aware pattern variants.
- **Custom CI providers**. The patterns cover GitHub Actions and shields.io. Projects using CircleCI, Travis CI, or Buildkite badges would still trigger `V52-NO-CI-BADGE`. The pattern list could be extended as usage demands.
- **Badge placement hint**. The fix message says "near the top" but does not verify placement. A badge at line 200 satisfies the rule but offers poor UX. A future variant could check that the badge appears within the first 20 lines.
- **Per-org defaults**. Some organisations publish a centralised status page instead of per-repo badges. A project-level opt-out marker (e.g. `# v52: no-badges-by-design`) could suppress both findings for those cases.

## References

- [Standard README spec — RichardLitt](https://github.com/RichardLitt/standard-readme) — *Defines the README convention this verifier enforces; includes badge placement guidance. Continuously developed since 2015-08, retrieved 2026-04-30.*
- [Best README Template — othneildrew](https://github.com/othneildrew/Best-README-Template) — *Widely adopted template that includes CI and license badges as default elements. Continuously developed since 2020-03, retrieved 2026-04-30.*
- [shields.io](https://shields.io/) — *The badge hosting service whose URLs V52 pattern-matches for both CI status and license variants. Continuously developed since 2014-01, retrieved 2026-04-30.*

## Examples

### ✓ Pass — GitHub Actions badge + shields.io license

```markdown
[![CI](https://github.com/owner/repo/actions/workflows/ci.yml/badge.svg)](https://github.com/owner/repo/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/owner/repo.svg)](LICENSE)

# My Project
```

### ✓ Pass — shields.io workflow status CI + static license badge

```markdown
![CI](https://img.shields.io/github/actions/workflow/status/owner/repo/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

# My Project
```

### ✗ Fail — codecov only (coverage ≠ CI status)

```markdown
[![codecov](https://codecov.io/gh/owner/repo/branch/main/graph/badge.svg)](https://codecov.io/gh/owner/repo)

# My Project
```

→ `V52-NO-CI-BADGE` (codecov does not confirm the build passed)
→ `V52-NO-LICENSE-BADGE` (no license badge present)

### ✗ Fail — No badges at all

```markdown
# My Project

Some description here.
```

→ `V52-NO-CI-BADGE`
→ `V52-NO-LICENSE-BADGE`
