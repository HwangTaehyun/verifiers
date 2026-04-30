# V40 — actions-sha-pin

> **Owner**: `hooks/validators/actions_sha_pin.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `.github/workflows/*.yml`, `.github/workflows/*.yaml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V40-ACTION-NOT-PINNED` | error | A third-party `uses:` line in a workflow references an action with a floating tag (`@v1`, `@v2`, `@latest`, `@main`) instead of a 40-character SHA. |
| `V40-FIRST-PARTY-NOT-PINNED` | warning | A GitHub-owned action (`actions/*`) also uses a floating tag instead of SHA. (Lower risk than third-party, but still a drift surface.) |

## Why this verifier exists

Third-party action supply-chain attacks are a documented attack surface. If a maintainer account is compromised, an attacker can retroactively modify code released under a floating tag. Evidence: `.github/workflows/ci.yml` lines 18, 20, 66, 106 and `e2e.yml` lines 49, 52, 102, 110 contain 8 third-party actions all using floating tags: `oven-sh/setup-bun@v2`, `gitleaks/gitleaks-action@v2`, `actions/cache@v4`, etc. If any maintainer account is compromised tomorrow, every future run of this workflow could execute malicious code.

Pinning to a 40-character commit SHA (immutable) prevents retroactive code changes. If a maintainer is compromised, their old code remains unchanged; users are protected unless they update the pin.

[GitHub Actions security hardening — Using third-party actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions) — GitHub, *continuously updated*, retrieved 2026-04-30. [OpenSSF Scorecard — Pinned-Dependencies check](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies) — OpenSSF, *continuously developed since 2020-11*, retrieved 2026-04-30 — both recommend pinning.

## Design rationale

- **Third-party actions are `error`, first-party are `warning`.** GitHub-owned actions (`actions/*`) are lower risk — GitHub's internal controls are stronger than open-source maintainer accounts. However, they should still be pinned for best practice. Two severity levels allow teams to fix critical third-party risks first.
- **40-character SHA is the only valid pin.** Commit SHAs are immutable; tags and branches can be force-pushed. Some projects use commit refs or short SHAs; V40 requires the full 40-char form for maximum auditability.
- **Inline comments are allowed.** A line like `uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4` is valid — the comment preserves the human-readable version for reviewers.
- **Detection is per-`uses:` line.** Each action is checked independently; if a workflow has 5 actions, all 5 must be pinned.

## How it checks (implementation plan)

Lives in `hooks/validators/actions_sha_pin.py`. Scans all workflow files in `.github/workflows/`.

### Top-level check

```python
def validate_file(self, ctx, file_path):
    findings = []
    src = file_path.read_text()
    
    findings.extend(self._check_actions(src, file_path))
    return findings
```

### `_check_actions` — V40-ACTION-NOT-PINNED / V40-FIRST-PARTY-NOT-PINNED

```python
# Match lines like: uses: owner/repo@tag or uses: owner/repo@sha
USES_LINE = re.compile(
    r"^\s*uses:\s+(?P<action>[a-zA-Z0-9\-_.]+/[a-zA-Z0-9\-_.]+)"
    r"@(?P<ref>[a-zA-Z0-9.:\-_]+)\s*(?:#.*)?$",
    re.MULTILINE
)

# 40-char hex = valid SHA; anything else = floating tag
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")

def _check_actions(self, src: str, file_path):
    for m in USES_LINE.finditer(src):
        action = m.group("action")
        ref = m.group("ref")
        line_no = src[:m.start()].count("\n") + 1
        
        # Check if ref is a valid 40-char SHA
        if SHA_PATTERN.match(ref):
            continue  # ✓ Pinned correctly
        
        # Determine if first-party (actions/*) or third-party
        is_first_party = action.startswith("actions/")
        
        if is_first_party:
            yield Finding(
                rule="V40-FIRST-PARTY-NOT-PINNED",
                file=str(file_path),
                line=line_no,
                message=f"Action {action} uses floating tag {ref}; pin to 40-char SHA",
                severity="warning"
            )
        else:
            yield Finding(
                rule="V40-ACTION-NOT-PINNED",
                file=str(file_path),
                line=line_no,
                message=f"Action {action} uses floating tag {ref}; pin to 40-char SHA",
                severity="error"
            )
```

## Could be more effective

- **SHA upgrade helper.** Parse the floating tag, query GitHub API to resolve it to a SHA, and suggest the pin in the error message.
- **Deprecation warnings.** Flag uses of actions that have been archived or deprecated by the maintainer.
- **License transparency.** Verify the action's license is compatible with the project's license (e.g., GPL actions in proprietary software).
- **Action source verification.** Warn if an action is hosted on a suspicious registries (not github.com).
- **Cross-workflow consistency.** If the same action is pinned to different SHAs in different workflows, flag it as drift.

## References

- [GitHub Actions — Security hardening with third-party actions](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions) — GitHub, *continuously updated*, retrieved 2026-04-30. The recommendation to pin actions to specific versions/SHAs.
- [GitHub Actions — Keeping your actions up to date](https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#keeping-your-actions-up-to-date-with-dependabot) — GitHub, *continuously updated*, retrieved 2026-04-30. Guidance on managing action pin updates.
- [OpenSSF Scorecard — Pinned Dependencies](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies) — OpenSSF, *continuously developed since 2020-11*, retrieved 2026-04-30. The automated security check that flags unpinned dependencies.
- [CISA — Software Supply Chain Security](https://www.cisa.gov/sites/default/files/publications/2023-08-software_supply_chain_security_guidance.pdf) — CISA, published 2023-08, retrieved 2026-04-30. Federal guidance on supply-chain risks.

## Examples

### ✓ Pass

```yaml
# .github/workflows/ci.yml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4
        # ✓ Pinned to 40-char SHA (comment is fine)
      
      - uses: oven-sh/setup-bun@e95805dcecc81a08789bbbd6420e38945cc2e8bc6 # v2
        # ✓ Third-party pinned to SHA
      
      - uses: gitleaks/gitleaks-action@a2a2333e969330a36f65f61a64db18db0c7d4ed6 # v2
        # ✓ Third-party pinned to SHA
```

### ✗ Fail

```yaml
# .github/workflows/ci.yml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        # ✗ V40-FIRST-PARTY-NOT-PINNED (floating tag)
      
      - uses: oven-sh/setup-bun@v2
        # ✗ V40-ACTION-NOT-PINNED (third-party, floating tag)
      
      - uses: actions/cache@latest
        # ✗ V40-FIRST-PARTY-NOT-PINNED (floating tag @latest)
      
      - uses: gitleaks/gitleaks-action@main
        # ✗ V40-ACTION-NOT-PINNED (third-party, floating tag @main)
```
