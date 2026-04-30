# V53 — github-community-files

> **Owner**: `hooks/validators/github_community_files.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `.github/**`, `docs/**`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V53-NO-PR-TEMPLATE` | warning | Neither `.github/PULL_REQUEST_TEMPLATE.md`, `.github/pull_request_template.md`, nor `docs/PULL_REQUEST_TEMPLATE.md` exists. |
| `V53-NO-ISSUE-TEMPLATE` | warning | `.github/ISSUE_TEMPLATE/` doesn't exist or is empty (no `.md`/`.yml` files), AND `.github/ISSUE_TEMPLATE.md` (legacy) is also absent. |
| `V53-NO-CODEOWNERS` | warning | None of `.github/CODEOWNERS`, `docs/CODEOWNERS`, or root `CODEOWNERS` exists. |

Not applicable when `.git/` is absent (not a repo) or when `.github/` directory is absent.

## Why this verifier exists

Open-source and team repositories rely on three community health files to make contribution workflows consistent and reviewable:

1. **PR descriptions diverge without a template.** Without `.github/PULL_REQUEST_TEMPLATE.md`, each contributor writes their own format — some include a test plan, others don't; some link issues, others forget. Reviewers lose the ability to enforce a checklist, and the PR history becomes unstructured. GitHub surfaces a PR template automatically when a contributor opens a new PR; zero configuration overhead for contributors.

2. **Bug reports miss repro steps.** Issue templates with structured frontmatter (`name`, `about`, `labels`, `assignees`) and body fields (`## Steps to reproduce`, `## Expected behaviour`, `## Actual behaviour`) standardise the information a maintainer needs to triage. Without them, issues arrive in free-form prose missing environment versions, stack traces, or reproduction steps. Maintainers spend time asking follow-up questions instead of reproducing the bug.

3. **Sensitive paths merge without domain-owner sign-off.** A CODEOWNERS file enables GitHub's branch-protection feature "Require review from Code Owners" ([GitHub docs — About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) — *continuously updated, retrieved 2026-04-30*)). Without it, a change to `server/internal/auth/` or `hasura/metadata/` can be approved and merged by any reviewer — including one unfamiliar with the security or data implications. CODEOWNERS maps path patterns to required reviewers, turning "anyone can approve" into "the right person must approve".

## Design rationale

- **Three independent checks; all run together.** PR template, issue templates, and CODEOWNERS solve distinct problems. A repo could have two of the three. Running them as independent rules lets the validator report all gaps in one pass rather than stopping at the first failure.
- **Lowercase PR template is an alias.** GitHub accepts both `PULL_REQUEST_TEMPLATE.md` and `pull_request_template.md`. V53 accepts either. Only the `docs/` location is case-sensitive (uppercase only) because that is what GitHub documents.
- **Directory-with-files beats empty directory.** `.github/ISSUE_TEMPLATE/` existing as an empty directory provides no value. V53 requires at least one `.md` or `.yml` file inside before accepting it as satisfied.
- **Legacy single-file issue template accepted.** Some older repos use `.github/ISSUE_TEMPLATE.md` (a single file, not a directory). V53 accepts this form as legacy-compatible.
- **CODEOWNERS location flexibility.** GitHub accepts CODEOWNERS in three locations: `.github/CODEOWNERS`, `docs/CODEOWNERS`, or root `CODEOWNERS`. V53 checks all three.
- **Not-a-repo and no-.github are skipped.** Directories without `.git/` (not a repository) and repositories without `.github/` produce no findings — the check is not applicable.

## How it checks

Lives in `hooks/validators/github_community_files.py`.

### Top-level gate

```python
def _check_files(self, ctx):
    root = Path(ctx.project_root)
    if not (root / ".git").exists():
        return []          # not a repo
    github_dir = root / ".github"
    if not github_dir.is_dir():
        return []          # no .github dir
    # ... run three independent checks
```

### Rule 1 — V53-NO-PR-TEMPLATE

```python
pr_missing = not any(
    p.is_file() for p in [
        github_dir / "PULL_REQUEST_TEMPLATE.md",
        github_dir / "pull_request_template.md",
        root / "docs" / "PULL_REQUEST_TEMPLATE.md",
    ]
)
```

### Rule 2 — V53-NO-ISSUE-TEMPLATE

```python
has_dir = issue_template_dir.is_dir() and any(
    f.suffix in (".md", ".yml")
    for f in issue_template_dir.iterdir() if f.is_file()
)
has_legacy = (github_dir / "ISSUE_TEMPLATE.md").is_file()

if not has_dir and not has_legacy:
    yield Finding(rule="V53-NO-ISSUE-TEMPLATE", ...)
```

### Rule 3 — V53-NO-CODEOWNERS

```python
codeowners_missing = not any(
    p.is_file() for p in [
        github_dir / "CODEOWNERS",
        root / "docs" / "CODEOWNERS",
        root / "CODEOWNERS",
    ]
)
```

Both `validate_file` and `validate_project` delegate to `_check_files(ctx)` — a single implementation path serves both Tier 2 (PostToolUse) and Tier 3 (Stop) triggers.

## Could be more effective

- **Template content validation.** V53 only checks presence. A PR template containing just `## Summary` with no `## Test plan` or `## Risk` section is structurally incomplete. A content scan (required section headings) would enforce richer quality.
- **Issue template frontmatter validation.** YAML frontmatter in `.github/ISSUE_TEMPLATE/*.yml` can declare `labels` and `assignees`. V53 doesn't inspect whether those fields map to real labels/teams in the repo.
- **CODEOWNERS syntax check.** A CODEOWNERS file with a typo in a team name (`@security` instead of `@org/security-team`) silently fails to enforce review requirements. V53 could parse the file and warn on obvious syntax errors or unknown patterns.
- **Branch protection integration.** CODEOWNERS is only effective when the branch protection rule "Require review from Code Owners" is enabled. V53 can't check the GitHub API for branch protection status, but a companion check or documentation note could bridge this.
- **Multiple PR templates.** GitHub supports a PR template chooser via `.github/PULL_REQUEST_TEMPLATE/` (a directory of multiple templates). V53 doesn't detect or validate multi-template setups.

## References

- [GitHub — About issue and pull request templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates) — GitHub, *continuously updated, retrieved 2026-04-30*. Canonical reference for PR template and issue template locations, formats, and GitHub rendering behaviour.
- [GitHub — About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) — GitHub, *continuously updated, retrieved 2026-04-30*. Documents CODEOWNERS file syntax, accepted locations (`.github/`, `docs/`, root), and branch-protection integration.
- [GitHub — Creating a pull request template](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository) — GitHub, *continuously updated, retrieved 2026-04-30*. Lists accepted filename variants including lowercase `pull_request_template.md`.
- [GitHub — Configuring issue templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository) — GitHub, *continuously updated, retrieved 2026-04-30*. Documents ISSUE_TEMPLATE directory structure, YAML form vs Markdown form, and legacy single-file form.

## Examples

### All three present — pass

```
.github/
  PULL_REQUEST_TEMPLATE.md   ← PR template
  ISSUE_TEMPLATE/
    bug_report.md             ← at least one issue template file
    feature_request.yml
  CODEOWNERS                  ← CODEOWNERS
```

### PR template — lowercase variant also passes

```
.github/
  pull_request_template.md   ← lowercase accepted
  ISSUE_TEMPLATE/
    bug_report.md
  CODEOWNERS
```

### CODEOWNERS at root — passes

```
CODEOWNERS                    ← root location accepted
.github/
  PULL_REQUEST_TEMPLATE.md
  ISSUE_TEMPLATE/
    bug_report.md
```

### CODEOWNERS in docs/ — passes

```
docs/
  CODEOWNERS                  ← docs/ location accepted
.github/
  PULL_REQUEST_TEMPLATE.md
  ISSUE_TEMPLATE/
    bug_report.md
```

### Missing all three — three warnings

```
.github/
  workflows/
    ci.yml
# → V53-NO-PR-TEMPLATE
# → V53-NO-ISSUE-TEMPLATE
# → V53-NO-CODEOWNERS
```

### Empty ISSUE_TEMPLATE dir — still flags

```
.github/
  PULL_REQUEST_TEMPLATE.md
  ISSUE_TEMPLATE/             ← directory exists but contains no .md or .yml
  CODEOWNERS
# → V53-NO-ISSUE-TEMPLATE
```
