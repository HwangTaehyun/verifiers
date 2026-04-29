# V08 — security

> **Owner**: `hooks/validators/security.py` (Tier 2/3, configurable, full check set) + `hooks/security_hook.py` (Tier 1, zero-dep, regex-only, sub-100ms gate). Phase38 (A3 audit) put the SECRET_REGEXES + path-classification primitives in `lib/secret_regexes.py` so both tiers share one source of truth.
> **Tier**: 1 (PostToolUse, regex-only) for the fastest secret pass + Tier 2/3 (full V08) for everything else.
> **File patterns**: empty (matches every file). The exclusion logic lives in `lib/secret_regexes.is_excluded_path` so test fixtures, vendor, etc. are skipped.

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V08-HARDCODED-SECRET` | error | A literal value matches a high-confidence credential pattern (`AKIA[0-9A-Z]{16}`, `ghp_[A-Za-z0-9]{36}`, `sk_live_`, `xoxb-`, plus an 8+ character `password=...` literal that is not a `${VAR}` / `{{ env.X }}` template). |
| `V08-CORS-WILDCARD` | error | Go: `AllowAllOrigins: true`, `cors.Config{AllowOrigins:["*"]}`, raw header `Access-Control-Allow-Origin: *`. |
| `V08-PHI-LOGGING` | warning | Go: `log.Info().Str("email", x)`, `zerolog .Str()` / `.Msg()` with PHI fields. JS: `console.log(email)` or template literals containing PHI fields (`patient_name`, `ssn`, `dob`, etc.). HIPAA risk. PHI field set is configurable via `security.phi_fields` in `.verifiers/config.yaml`. |
| `V08-PHI-CHECK-ENABLED` | (gate) | Whole PHI rule is gated by `security.phi_check_enabled` config (default `true`). Non-medical projects can disable. |
| `V08-NO-GITIGNORE` | error | `.gitignore` does not exist at project root. |
| `V08-GITIGNORE-MISSING` | error | `.gitignore` exists but is missing one or more entries from `security.required_gitignore` (default `[".env", "*.pem", "*.key", ".env.local", "*.p12"]`). |

## Why this verifier exists

V08 is the highest-priority validator in the registry (`get_all_validators()` lists it first) because the failure modes are unrecoverable:

1. **Hardcoded secret in commit history.** Even if removed in the next commit, the credential is permanently leaked — rotate-or-die. Tier 1 must catch this *before* the Edit lands on disk.
2. **Wildcard CORS.** `Allow-Origin: *` lets every domain read auth-cookied responses. The fix is "name your origins"; the failure mode is "every cross-site request can be a CSRF + data-exfil".
3. **PHI in logs.** Logs go to centralized aggregators, retention systems, error trackers. PHI written to a log is PHI in a long-tail of systems the team didn't audit. HIPAA fines + breach notification obligations follow.
4. **Untracked `.env`.** The single most common secret leak path: `.env` exists locally, has secrets, and no `.gitignore` rule excludes it. One `git add .` ships it to the remote.

V08 + Tier 1 form a defense-in-depth: Tier 1 (regex-only, no yaml import, sub-100ms) catches the most common 7 patterns *on the Edit*, blocking the file from settling on disk in many cases. V08 (full set, slower) runs in Tier 2/3 to catch everything else.

## Design rationale

- **Two-tier split is load-bearing.** Tier 1 must be *fast and zero-dep* — it runs on every Edit/Write before the tool returns. If V08 (yaml-loading, multi-helper) ran there, it would blow the 100ms budget. Phase38's `lib/secret_regexes.py` extraction lets both tiers share regexes without coupling Tier 1 to yaml.
- **High-confidence patterns are `error`.** `AKIA[0-9A-Z]{16}` is AWS's own format; a literal match is virtually never a false positive. Same for `ghp_*36-char`, `sk_live_*`. The `password=...` 8+char regex is fuzzier — Tier 1's regex excludes `${...}` and `{{ ... }}` (template-engine vars) to keep false-positive rate manageable.
- **PHI list is configurable, default opinionated.** Default `phi_fields` is medical-domain (`patient_name`, `ssn`, `dob`, `mrn`, ...). Non-medical projects set `security.phi_check_enabled: false` or override `security.phi_fields: ["customer_email", ...]`. Empty list = use defaults (Phase19 SecurityConfig semantics).
- **Path exclusion is anchored, not substring (Phase21+, P2-3).** Pre-Phase21, `EXCLUDE_PATHS = ["mock", ...]` excluded `mockingbird/Real.go` because it contained `mock`. Now `EXCLUDE_DIRS = frozenset({"mock", "mocks", ...})` is matched on `Path.parts`. False-positive class closed.
- **`.gitignore` rules are structural, not stylistic.** Missing `.env` from gitignore = direct secret leak. The required set is small + opinionated; project override is a flat list, not a merge.

## How it checks (implementation)

### Tier 1 — `hooks/security_hook.py` (zero-dep gate)

```python
def main():
    cap = 10 * 1_048_576
    raw = sys.stdin.read(cap + 1)
    if len(raw) > cap:
        # Phase38b: silent-pass on truncated input is wrong for a security gate
        print(json.dumps({"decision": "block", "reason": "stdin truncated"}))
        return
    input_data = json.loads(raw)
    if input_data.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return  # only inspect file-modifying tools

    file_path = input_data["tool_input"]["file_path"]
    if _is_excluded_path(file_path):     # delegates to lib/secret_regexes
        return

    findings = check_secrets(file_path)
    if findings:
        # Block the tool: Claude sees the reason and self-corrects on next Edit
        print(json.dumps({"decision": "block", "reason": ..., "additionalContext": ...}))
```

`check_secrets` reads the file, scans line-by-line with `SECRET_REGEXES`, returns a list of `{file, line, rule, message, fix}` dicts. No yaml, no ProjectContext, no logger — Tier 1's whole runtime is < 100 ms.

### Tier 2/3 — `hooks/validators/security.py`

```python
def validate_file(self, ctx, file_path):
    sec_cfg = ctx.config.security
    phi_fields = sec_cfg.phi_fields or PHI_FIELDS
    return self._check_single_file(
        file_path,
        phi_fields=phi_fields,
        phi_enabled=sec_cfg.phi_check_enabled,
    )

def validate_project(self, ctx):
    sec_cfg = ctx.config.security
    return self._check_project_wide(
        ctx,
        phi_fields=sec_cfg.phi_fields or PHI_FIELDS,
        phi_enabled=sec_cfg.phi_check_enabled,
        required_gitignore=sec_cfg.required_gitignore or REQUIRED_GITIGNORE,
    )
```

#### `_check_single_file(file_path, phi_fields, phi_enabled)`

```python
findings: list[Finding] = []
findings.extend(self._check_secrets(file_path))
findings.extend(self._check_cors(file_path))
if phi_enabled:
    findings.extend(self._check_phi_logging(file_path, phi_fields=phi_fields))
return findings
```

#### `_check_secrets(file_path)` — V08-HARDCODED-SECRET

Same regex set as Tier 1, but applied to the file already on disk. Imports from `lib.secret_regexes`:

```python
from lib.secret_regexes import SECRET_REGEXES, is_excluded_path

if is_excluded_path(file_path):
    return []
content = Path(file_path).read_text(errors="replace")
for line_num, line in enumerate(content.splitlines(), 1):
    for pattern, desc in SECRET_REGEXES:
        if re.search(pattern, line):
            yield Finding(
                severity="error",
                rule="V08-HARDCODED-SECRET",
                message=f"{desc} detected in source",
                fix=f"Remove the hardcoded secret at {file_path}:{line_num}. "
                    "Move to env via APP_*, reference via ${{VAR}}.",
                ...
            )
            break  # one finding per line is enough
```

#### `_check_cors(file_path)` — V08-CORS-WILDCARD

```python
WILDCARD_PATTERNS = [
    r'AllowAllOrigins\s*:\s*true',
    r'cors\.Config\s*\{[^}]*AllowOrigins\s*:\s*\[\s*["\']\*["\']',
    r'Access-Control-Allow-Origin\s*:\s*\*',
    r'res\.headers\.set\s*\(\s*["\']Access-Control-Allow-Origin["\']\s*,\s*["\']\*["\']',
]
```

#### `_check_phi_logging(file_path, phi_fields)` — V08-PHI-LOGGING

```python
# Go: zerolog `.Str("field", value)` / Sprintf-named-field
# JS:  template literal or console.log(field)
PHI_RE = re.compile(
    r'(?:'
    r'\.Str\s*\(\s*["\'](?P<field_zerolog>\w+)["\']'      # Go zerolog
    r'|console\.log\s*\([^)]*\b(?P<field_console>\w+)'    # JS console
    r'|`[^`]*\$\{(?P<field_template>\w+)\}'               # JS template literal
    r')'
)
for m in PHI_RE.finditer(content):
    field = m.group("field_zerolog") or m.group("field_console") or m.group("field_template")
    if field in phi_fields:
        yield Finding(rule="V08-PHI-LOGGING", ...)
```

PHI rule was hardened in a prior phase: previously `console.log("email mentioned")` triggered (substring keyword match). Now the regex requires the field to actually be a *bound variable* in a logging call, drastically reducing false positives.

#### `_check_project_wide` — adds gitignore checks

```python
gitignore = ctx.project_root / ".gitignore"
if not gitignore.exists():
    yield Finding(rule="V08-NO-GITIGNORE", ...)
    return
contents = gitignore.read_text()
for required in (sec_cfg.required_gitignore or REQUIRED_GITIGNORE):
    if required not in contents:
        yield Finding(rule="V08-GITIGNORE-MISSING", message=f"missing {required}", ...)
```

The check is substring-based — `*.env` matches `.env*` or even `.env.example` if it appears as a literal in the file. False positive surface is small in practice.

### Could be more effective

- **Entropy-based secret detection (TruffleHog-style).** Current regexes catch known-format secrets (AWS, GitHub, OpenAI). They miss "generic high-entropy 32-byte strings". An entropy score (Shannon ≥ 4.5) over ≥20-char tokens would catch generic API keys. Higher false-positive rate; offset by allowlist.
- **`gitleaks` integration.** Shell out to `gitleaks detect --no-git` once per Stop and merge findings under `V08-GITLEAKS-<rule>`. Cheap; high signal. Strong candidate for Phase 27 follow-up.
- **`.env.example` ↔ `.env` divergence (locally).** A developer's local `.env` is gitignored, but `.env.example` should declare every key the local env carries. V01 covers part of this; combining gives full coverage.
- **PHI log redaction adapter detection.** Some projects use a logger middleware that auto-redacts PHI (`zerolog`'s `Hook` interface). V08 currently doesn't detect that the project has redaction in place — could overflag in well-defended projects. A future enhancement: scan for `RegisterHook` patterns and lower severity.
- **CORS scope checker.** Currently only the wildcard case is flagged. A real check would parse `AllowOrigins` and verify each origin is on a domain-allowlist (config-driven). Project-specific; out of V08's lane.
- **Proper YAML / Go AST scanning.** Regex misses multi-line values and string concat. Trade-off: AST cost is larger; current regex catches the high-frequency cases.

## References

- [OWASP Top 10 2021 — A02 Cryptographic Failures](https://owasp.org/Top10/A02_2021-Cryptographic_Failures/) — OWASP, *published 2021, continuously updated*, retrieved 2026-04-30. Hardcoded credentials are explicitly called out.
- [OWASP Top 10 2021 — A05 Security Misconfiguration (CORS)](https://owasp.org/Top10/A05_2021-Security_Misconfiguration/) — OWASP, *published 2021*, retrieved 2026-04-30. Wildcard `Access-Control-Allow-Origin` rationale.
- [HIPAA Security Rule (45 CFR §164.312)](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html) — U.S. Department of Health and Human Services, *published 2003, continuously enforced*, retrieved 2026-04-30. The legal basis for V08-PHI-LOGGING's existence.
- [GitGuardian — Anatomy of secrets in source code](https://www.gitguardian.com/state-of-secrets-sprawl-report-2024) — GitGuardian, *published 2024*, retrieved 2026-04-30. Empirical data on detected-secret patterns; informs the SECRET_REGEXES choice.
- [TruffleHog — Detector list](https://github.com/trufflesecurity/trufflehog) — Truffle Security, *continuously maintained*, retrieved 2026-04-30. Reference set for entropy-based detection (V08 uses a smaller hardcoded subset).
- [GitHub — Secret scanning patterns](https://docs.github.com/en/code-security/secret-scanning/secret-scanning-patterns) — GitHub, *continuously updated*, retrieved 2026-04-30. Authoritative source for `ghp_*`, `gho_*` formats.
- [AWS — Access key ID format](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html) — AWS, *continuously updated*, retrieved 2026-04-30. The `AKIA[0-9A-Z]{16}` pattern V08 detects.

## Examples

### ✓ Pass

```go
// secret comes from env, not source
db, err := sql.Open("postgres", os.Getenv("APP_DATABASE_DSN"))

// CORS scoped to known origins
cors.New(cors.Config{
    AllowOrigins: []string{"https://app.example.com", "https://admin.example.com"},
})

// log without PHI bound to the line
log.Info().Str("user_id", userID).Msg("login attempt")  // user_id not in PHI list
```

```gitignore
.env
.env.local
*.pem
*.key
*.p12
node_modules/
```

### ✗ Fail

```go
// hardcoded literal → V08-HARDCODED-SECRET (error)
const adminToken = "ghp_aBCDeFGHIJklMNOpqrstuvwxyz0123456789"

// wildcard → V08-CORS-WILDCARD (error)
cors.New(cors.Config{AllowOrigins: []string{"*"}})

// PHI bound to logger field → V08-PHI-LOGGING (warning)
log.Info().Str("ssn", patient.SSN).Msg("loaded patient")
```

```ts
// PHI in template literal → V08-PHI-LOGGING (warning)
console.log(`Patient ${patient_name} updated`);
```

```
# .gitignore — missing .env entry
node_modules/
*.log
# → V08-GITIGNORE-MISSING (error, missing .env)
```
