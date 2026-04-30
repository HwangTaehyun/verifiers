# V41 — actions-permissions-block

> **Owner**: `hooks/validators/actions_permissions_block.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `.github/workflows/*.yml`, `.github/workflows/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V41-NO-PERMISSIONS-BLOCK` | warning | A workflow file has neither a top-level `permissions:` key nor any job with a job-level `permissions:` key defined. |

## Why this verifier exists

GitHub Actions provides `GITHUB_TOKEN` automatically to every workflow, but its default scope is organization-config-dependent and undefined per the principle of least privilege. If a third-party action is compromised, it can use the token to make API calls with all the scopes it's granted. Evidence: `.github/workflows/ci.yml` and `.github/workflows/e2e.yml` contain no `permissions:` blocks at either workflow or job level. The token's scope is unknown and potentially overprivileged.

Best practice is to set `permissions: {}` (deny-all) at the top level, then grant only what each job actually needs:
- A checkout-only job needs `contents: read`.
- A release job needs `contents: write` + `id-token: write`.
- A read-only test needs no token permissions at all.

This restricts the blast radius if any action is compromised.

[GitHub Actions — Controlling permissions for GITHUB_TOKEN](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/controlling-permissions-for-github_token) — GitHub, *continuously updated*, retrieved 2026-04-30. [OpenSSF Scorecard — Token-Permissions check](https://github.com/ossf/scorecore/blob/main/docs/checks.md#token-permissions) — OpenSSF, *continuously developed since 2020-11*, retrieved 2026-04-30 — both recommend explicit permission blocks.

## Design rationale

- **Rule is warning, not error.** Some projects legitimately operate with org-wide default scopes and no per-workflow gating. However, explicit scoping is a best practice and should be encouraged.
- **Either top-level OR job-level permissions satisfies the rule.** If the workflow has `permissions:` at the top, all jobs inherit it (unless overridden). If individual jobs have `permissions:`, that's fine too (job-level overrides top-level). The rule only flags the case where neither exists.
- **Empty `permissions: {}` is a valid and encouraged pattern.** It sets the baseline to deny-all, forcing developers to opt-in to required scopes per-job.
- **Detection is YAML key presence, not scope validation.** V41 does not verify the scopes are correct (e.g., that a release job actually has `contents: write`); it only checks that the permission structure exists. A separate linter could validate scope correctness.

## How it checks (implementation plan)

Lives in `hooks/validators/actions_permissions_block.py`. Scans all workflow files in `.github/workflows/`.

### Top-level check

```python
def validate_file(self, ctx, file_path):
    findings = []
    
    workflow = yaml.safe_load(file_path.read_text())
    if not workflow:
        return []
    
    # Check if workflow has top-level permissions
    if "permissions" in workflow:
        return []  # ✓ Top-level permissions present
    
    # Check if any job has permissions
    jobs = workflow.get("jobs", {})
    for job_name, job_def in jobs.items():
        if isinstance(job_def, dict) and "permissions" in job_def:
            return []  # ✓ Job-level permissions present
    
    # Neither top-level nor job-level permissions found
    findings.append(Finding(
        rule="V41-NO-PERMISSIONS-BLOCK",
        file=str(file_path),
        message="No permissions block found at workflow or job level; "
                "use 'permissions: {}' at top level and grant per-job scopes"
    ))
    
    return findings
```

## Could be more effective

- **Validate scope correctness.** Check that each job has appropriate scopes for its actions. For example, a job using `actions/checkout` needs `contents: read`; a job using `github-script` to write needs `contents: write`.
- **Detect overprivileged scopes.** Flag jobs that request `write` access when only `read` is needed (e.g., a test job with `contents: write`).
- **Cross-action scope inference.** Parse the actions used in each job and warn if the declared `permissions:` don't match the inferred needs.
- **Default-scope detection.** Flag workflows that run on untrusted trigger events (e.g., `pull_request_target`) without explicit `permissions: {}`, since PRs from untrusted forks could abuse the token.
- **OIDC token options.** Suggest replacing `GITHUB_TOKEN` scopes with OIDC `id-token` for cloud credential exchange (more secure pattern for deployments).

## References

- [GitHub Actions — Controlling permissions for GITHUB_TOKEN](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/controlling-permissions-for-github_token) — GitHub, *continuously updated*, retrieved 2026-04-30. The complete API documenting scopes and job-level overrides.
- [GitHub Actions — Permission scopes](https://docs.github.com/en/actions/security-guides/automatic-token-authentication#permissions-for-the-github_token) — GitHub, *continuously updated*, retrieved 2026-04-30. The list of available scopes.
- [OpenSSF Scorecard — Token-Permissions](https://github.com/ossf/scorecard/blob/main/docs/checks.md#token-permissions) — OpenSSF, *continuously developed since 2020-11*, retrieved 2026-04-30. Automated security check for explicit permission blocks.
- [CISA — GitHub Actions Security Hardening](https://www.cisa.gov/sites/default/files/publications/GitHub-Actions-Security-Hardening.pdf) — CISA, published 2023-06, retrieved 2026-04-30 — federal guidance on GitHub Actions security.

## Examples

### ✓ Pass

```yaml
# .github/workflows/ci.yml — Top-level permissions
permissions:
  contents: read
  packages: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: npm test
```

```yaml
# .github/workflows/release.yml — Job-level permissions
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: npm run build

  release:
    runs-on: ubuntu-latest
    needs: build
    permissions:
      contents: write
      id-token: write
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: npm publish
```

```yaml
# .github/workflows/minimal.yml — Deny-all baseline
permissions: {}

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: npx eslint .
```

### ✗ Fail

```yaml
# .github/workflows/ci.yml
# No top-level permissions, no job-level permissions
# → V41-NO-PERMISSIONS-BLOCK

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: npm test
```

```yaml
# .github/workflows/build.yml
# No permissions at all
# → V41-NO-PERMISSIONS-BLOCK

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-go@v4
      - run: go build ./...
  
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
      - run: go test ./...
```
