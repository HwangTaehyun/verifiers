# Verifiers — Category Map

> **Status**: Phase50 organizational refactor (2026-04-30).
> **Audience**: contributors adding a new validator or trying to find
> "which verifier owns rule X". For per-validator detail, see each
> `skills/V##-{name}/SKILL.md`.

The verifier surface has grown to **25 active validators** (V01–V27, with
V17 deferred and V24 deliberately removed in Phase46). Without a
categorization document, "should this new check go in V05 or V26?" or
"why is V03-UNIMPLEMENTED-RPC the same as V27-UNIMPLEMENTED-RPC?" became
recurring questions. This file is the answer.

## Categories

The 25 validators partition cleanly into 7 categories:

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. CODE QUALITY (per-language)                                     │
│       V06 go-quality                                                │
│       V07 ts-quality                                                │
│       V19 py-quality                                                │
│       V14 complexity-guard          (cross-language)                │
├─────────────────────────────────────────────────────────────────────┤
│  2. TEST EXECUTION                                                  │
│       V09 go-test                                                   │
│       V10 ts-test                                                   │
│       V11 py-test                                                   │
│       V21 pytest                    (Stop-only, smart-gated)        │
├─────────────────────────────────────────────────────────────────────┤
│  3. ENVIRONMENT / CONFIGURATION                                     │
│       V01 env-config                (env vars + config keys)        │
│       V22 multi-env                 (root↔server drift + Viper map) │
├─────────────────────────────────────────────────────────────────────┤
│  4. DOCKER / INFRASTRUCTURE                                         │
│       V05 docker                    (compose + Dockerfile, all)     │
│       V26 docker-prod               (production strict hardening)   │
│       V25 go-multibinary            (cmd/ structure, .air.toml)     │
├─────────────────────────────────────────────────────────────────────┤
│  5. API / PROTO / RPC / DATA                                        │
│       V03 proto-connect             (proto language: lint + gen)    │
│       V23 buf-governance            (lock + breaking + protovalid.) │
│       V27 connect-handler           (handler runtime: rpc + intercp)│
│       V02 graphql-gen               (genqlient stale-gen)           │
│       V20 hasura-graphql            (raw SQL forbidden in Go)       │
│       V04 hasura-migration          (migration safety + ordering)   │
├─────────────────────────────────────────────────────────────────────┤
│  6. SECURITY                                                        │
│       V08 security                  (secrets + CORS + PHI + ignore) │
│       V18 mock-data-guard           (test-only data leaking to prod)│
├─────────────────────────────────────────────────────────────────────┤
│  7. PROCESS / DISCIPLINE                                            │
│       V12 commit-discipline                                         │
│       V13 ai-cheating-guard                                         │
│       V15 dependency-guard          (import direction)              │
│       V16 linter-config-guard                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Ownership boundaries — the cross-cutting clarifications

Three category-internal carvings caused real overlap before Phase50.
Documented here so future contributors don't reintroduce the duplication.

### Docker (V05 vs V26)

```
                  ┌─ V05 docker (broad — all compose files) ─┐
                  │   V05-PORT-CONFLICT, V05-VHOST-NO-NETWORK│
                  │   V05-MISSING-HEALTHCHECK (warning)      │
                  │   V05-DOCKERFILE-* (always)              │
                  │   V05-PROD-* (filename-heuristic prod)   │
                  │     - PORT-EXPOSED, DEV-MODE,            │
                  │       WILDCARD-CORS, NO-TRAEFIK-LABELS   │
                  │   V05-DEV-* (override files only)        │
                  └──────────────────────────────────────────┘
                                   │
                                   │ NARROWS TO
                                   ▼
                  ┌─ V26 docker-prod (strict — *.prod.yaml) ─┐
                  │   V26-PROD-NO-RESOURCE-LIMITS (warning)  │
                  │   V26-PROD-NO-HEALTHCHECK (error)        │
                  │   V26-PROD-SECRET-BIND-MOUNT (error)     │
                  │   V26-PROD-LOCALHOST-VHOST (error)       │
                  └──────────────────────────────────────────┘
```

**Rule of thumb:**
- If the check is meaningful for any compose file (dev included), put it in **V05**.
- If the check applies *only* to strict-pattern production files (`*.production.yaml`, `*.prod.yaml`) AND the violation should be `error`-severity (block deploy), put it in **V26**.
- Healthcheck and resource-limit are the canonical examples: V05 has the permissive warning; V26 has the strict error. They coexist by design.

### Proto / RPC (V03 vs V23 vs V27)

```
        proto file
            │
            ├──> V03 proto-connect (LANGUAGE)
            │      V03-BUF-LINT      (style + naming)
            │      V03-STALE-GEN     (gen/ vs proto/ mtime + hash)
            │
            ├──> V23 buf-governance (CONTRACT)
            │      V23-LOCK-DRIFT          (buf.yaml ↔ buf.lock)
            │      V23-BREAKING-<RULE>     (wire compatibility)
            │      V23-PROTOVALIDATE-MISSING (schema validation hint)
            │
            ▼
        handler code
            │
            └──> V27 connect-handler (RUNTIME)
                  V27-UNIMPLEMENTED-RPC          (proto rpc ↔ go method)
                  V27-NO-INTERCEPTORS            (auth + logging + valid.)
                  V27-MISSING-{AUTH,LOG,VAL}-INTERCEPTOR
                  V27-RAW-ERROR-RETURN           (must wrap in connect.NewError)
```

**Rule of thumb:**
- Anything you can answer by reading only `.proto` files → V03 or V23.
- Style + freshness → V03. Wire-contract governance → V23.
- Anything that needs to walk Go handler code → V27.
- The Phase50 consolidation removed `V03-UNIMPLEMENTED-RPC` and `V03-BREAKING`; V27 and V23 own those concerns now.

### Env / Config (V01 vs V22)

```
        .env files          server/config/*.yaml         Go source
              │                       │                       │
              ▼                       ▼                       ▼
      ┌── V01 env-config (consumer-side) ──────────────────────┐
      │     V01-SECRET-IN-CONFIG  (secret literal in YAML)     │
      │     V01-ENV-MISSING       (env var read but undeclared)│
      │     V01-CONFIG-KEY-MISSING                             │
      │     V01-VITE-ENV-MISSING  (frontend VITE_*)            │
      └────────────────────────────────────────────────────────┘
                                │
                                │ MIRRORS / VALIDATES
                                ▼
      ┌── V22 multi-env (producer-side, project-level) ───────┐
      │     V22-NON-APP-PREFIX     (server APP_* discipline)  │
      │     V22-ROOT-SERVER-DRIFT  (one-direction: root→srv)  │
      │     V22-VIPER-KEY-NO-ENV   (yaml key ↔ APP_VAR map)   │
      └────────────────────────────────────────────────────────┘
```

**Rule of thumb:**
- Per-file checks ("is this `.env` line OK?") → V01.
- Cross-file consistency ("do root and server agree on prefix?") → V22.
- Viper-specific binding semantics → V22 (Phase42).

## What's NOT a category

- **"Tier 1 / Tier 2 / Tier 3"** — that's the *invocation* axis (PostToolUse fast-path / PostToolUse general / Stop comprehensive), orthogonal to category. A single validator can run in multiple tiers (V05 runs in both 2 and 3).
- **Severity** — error/warning/info is per-rule, not per-validator. V05 has all three.
- **File patterns** — overlap is fine; multiple validators can match `**/*.go`. The router de-duplicates findings on identical `(rule, file, line, message)`.

## Disabling whole categories

Currently the disable list is per-V-ID:

```yaml
# .verifiers/config.yaml
validators:
  disabled: ["V13-ai-cheating-guard", "V14-complexity-guard"]
```

A future `groups:` section could allow:

```yaml
# proposed
groups:
  process: [V12, V13, V15, V16]
validators:
  disabled_groups: ["process"]
```

Not implemented yet — open to issue if useful.

## Phase50 changelog

This document was created during the Phase50 organizational pass.
Concrete code changes shipped in the same phase:

- **Removed** `V05-PROD-NO-RESOURCE-LIMITS` (duplicate of `V26-PROD-NO-RESOURCE-LIMITS`).
- **Removed** `V03-UNIMPLEMENTED-RPC` (consolidated into `V27-UNIMPLEMENTED-RPC`).
- **Removed** `V03-BREAKING` (consolidated into `V23-BREAKING-<RULE>` which preserves Buf's per-rule code).
- **Documented** the V05↔V26 healthcheck layering as intentional (V05 warning, V26 error).
- **Test count**: 1130 → 1127 (3 tests for the now-removed V03 handler-coverage; the V27 tests at `tests/test_connect_handler.py::TestUnimplementedRpc` cover the same behavior with stricter Connect-handler signature matching).

For full per-validator detail consult each `skills/V##-{name}/SKILL.md`.
