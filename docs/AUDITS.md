# Audit History — Verifiers Project

> **Purpose**: 이 프로젝트는 여러 차례의 audit (점검·감사) 를 거치면서 verifier
> 집합이 진화해왔다. 이 문서는 각 audit 가 **무슨 기준으로 점검했고**, **무엇을
> 발견했고**, **어떤 phase 로 결론났는지** 시간순으로 정리한 단일 진실 소스다.
>
> 새로 합류하는 컨트리뷰터가 "왜 이 verifier 가 만들어졌나" / "왜 이 rule 은
> 삭제됐나" 같은 archaeological 질문을 코드 grep + git blame 없이 한 곳에서
> 답할 수 있게 하는 게 목표.

## 용어 — "audit" 이란?

**Audit = 점검·감사**. 회계감사처럼, **정해진 기준에 비추어 항목별로 따져보는
일**. 결과는 항상 "현 상태와 기준 사이의 차이점 보고서 + 조치 항목 리스트".

이 프로젝트에서 audit 의 input 은:
- 실제 코드베이스 상태 (verifiers/ 자기 자신 또는 외부 target project)
- 비교 기준 (best-practice docs, 12-Factor, OpenSSF Scorecard, CIS Benchmark, etc.)

Output 은:
- gap matrix (현재 어디에 있고, 어디에 있어야 하는지)
- proposed verifier rules (gap 을 자동 점검할 수 있게)
- 우선순위 (impact-per-effort)

## Audit 연표

```
Phase 27 audit  (2026-04-29)  → V22-V27 추가 (target-project 커버리지)
Phase 50 audit  (2026-04-30)  → V## 들 사이 rule-level 중복 정리
Phase 51 audit  (2026-04-30)  → V## 들 사이 algorithm-level 중복 정리
Phase 53 audit  (2026-04-30)  → ai-project-template best-practice gap (V34-V50)
```

---

## Phase 27 audit — Target-Project 커버리지

**날짜**: 2026-04-29 (v0.4.0 마일스톤)
**기준**: `/Users/taehyun/airs/Innovation/ai-project-template/` 의 기술스택 (env / Viper config / docker-compose / proto / genqlient / Hasura) 을 기존 V01-V21 verifier 가 충분히 커버하나?

### 발견된 gap (6개)

| ID | Gap | 결과 |
|---|---|---|
| A | env / Viper 의 multi-environment 일관성 (APP_ prefix, root↔server drift, Viper key↔env mapping) | **V22 multi-env** 신설 |
| B | buf governance (lock drift, breaking change per-rule, protovalidate hint) | **V23 buf-governance** 신설 |
| C | Hasura permission audit | **V24 cut** — V20 Hasura GraphQL Enforcement 가 충분 커버한다고 판단되어 사용자 결정으로 제외 |
| D | Go multi-binary discipline (graceful shutdown, tools.go, .air.toml mapping) | **V25 go-multibinary** 신설 |
| E | Docker compose production hardening (resource limits, healthcheck, secret bind-mount, localhost vhost) | **V26 docker-prod** 신설 |
| F | Connect-RPC handler completeness (handler↔proto, interceptors, connect.NewError) | **V27 connect-handler** 신설 |

### 결론
- 25개 active verifier 로 확장 (V01-V27 minus V17 deferred + V24 cut)
- v0.4.0 release tag 로 마일스톤
- Skill 폴더 패턴 (`SKILL.md` 표준 구조: Rules / Why / Design / How-it-checks / Could-be-better / References / Examples) 정착

---

## Phase 50 audit — Rule-level 중복 정리

**날짜**: 2026-04-30
**기준**: 25개 V## 사이에 **같은 의미의 rule 이 두 곳에서 emit** 되고 있나?

### 발견된 중복 (3건)

1. **`V05-PROD-NO-RESOURCE-LIMITS` ↔ `V26-PROD-NO-RESOURCE-LIMITS`**
   - 둘 다 prod-classified compose 에서 `deploy.resources.limits` 부재 점검
   - V05: severity=info (stale), V26: severity=warning (canonical)
   - **결정**: V26 owns it. V05 의 method `_check_prod_resource_limits` 삭제.

2. **`V03-UNIMPLEMENTED-RPC` ↔ `V27-UNIMPLEMENTED-RPC`**
   - V03: 느슨한 regex `func \([^)]+\) (\w+)\(` (모든 Go 프로젝트 적용)
   - V27: 엄격한 Connect signature `*connect.Request[T]` 매칭
   - V27 이 stricter superset; Connect 프로젝트에서는 둘 다 emit (noise)
   - **결정**: V03-UNIMPLEMENTED-RPC 삭제. V27 owns it.
   - 트레이드오프: non-Connect 프로젝트는 이 check 가 사라짐 → 그런 프로젝트는 V27 disable + IDE/buf-lint 로 커버.

3. **`V03-BREAKING` ↔ `V23-BREAKING-<RULE>`**
   - V03: coarse single rule (모든 buf-breaking finding 을 한 rule 로 emit)
   - V23: per-rule 코드 보존 (`V23-BREAKING-FIELD_NO_DELETE` 등 selective disable 가능)
   - **결정**: V03-BREAKING 삭제. V23 owns it. 같은 worktree-aware logic 이라 regression 없음.

### 의도적으로 공존한 케이스 (1건)
**`V05-MISSING-HEALTHCHECK` ↔ `V26-PROD-NO-HEALTHCHECK`** — 둘 다 유지.
- V05: warning, all-files (early permissive nudge)
- V26: error, prod-only (strict gate)
- 두 SKILL.md 에 layering 의도 명시.

### 결과물
- `docs/VERIFIERS-CATEGORIES.md` 신설 — 25개 verifier 를 7 카테고리로 정리
- 3개 rule 삭제, 3개 method 삭제, 3개 test 클래스 삭제 (test 카운트 1130 → 1127)
- Commit `5218f49`

---

## Phase 51 audit — Algorithm-level 중복 정리

**날짜**: 2026-04-30
**기준**: V## 들이 **같은 알고리즘 코드를 복사-붙여넣기로 중복 보유**하고 있나? lib/ 으로 추출 가능?

### 검토한 6쌍

| 쌍 | 중복 줄 수 | 판정 |
|---|---|---|
| V02 ↔ V03 codegen staleness (hash + mtime) | 28줄 동일 | ✅ **추출** |
| V05 ↔ V26 compose loader | 14줄 | ⏸ **defer** — V26 이 이미 local `_walk_compose` 갖고 있음. 3번째 consumer 나타나면 그때 |
| V23 ↔ V27 proto walker | 5줄 idiom | ❌ **premature** |
| V01 ↔ V22 env file parsing | 8줄 | ❌ **marginal** |
| V25/V27/V08 Go pattern matching | domain-specific 별개 | ❌ **공통점 없음** |
| Git common-dir resolution | 단일 호출처 | ❌ **추출 불필요** |

### 결과물
- `lib/codegen_staleness.py` 신설 (V02 + V03 공유)
- 8개 unit test (skip cases / hash gate / mtime gate / both gates trip)
- V02 -10 lines, V03 -15 lines, lib +135 lines (대부분 docstring)
- Test 카운트 1127 → 1135
- Commit `7a66ae6`

### Phase 52 — 카테고리를 운영 가능하게 (audit 후속작업)
Phase 50 의 categorization 문서가 *문서로만* 존재 → **`disabled_groups: ["process"]`** 같은 config 로 실제 disable 가능하게 만듦. `BUILTIN_GROUPS` dict + `expand_disabled_groups()` 헬퍼. 17개 새 test 추가 (1135 → 1152). Commit `9e1c973`.

---

## Phase 53 audit — `ai-project-template` Best-Practice Gap

**날짜**: 2026-04-30
**기준**: `ai-project-template` 가 의료/금융 프로젝트로서 **best practice 를 충족하나?** primary source (12-Factor, OpenSSF Scorecard, CIS Docker Benchmark, Postgres docs, K8s probe docs) 와 비교.

### 점검한 3개 surface

| Surface | Gap 수 | 발견된 핵심 issue |
|---|---|---|
| **Go runtime discipline** | 6 | bare `return err` (cmd/normalize-cmf 에서 7군데), mid-flow `context.Background()` (minio_pdf_renderer), HTTP server timeout 0 (slowloris 위험), `-race` 없는 CI, `wrapcheck` 없는 lint config, global zerolog 미사용 |
| **CI/CD + container security** | 6 | 8개 third-party action 모두 floating tag (SHA pin 0개), `permissions:` block 0개, image scanning 0개, base image digest 0개, Dockerfile HEALTHCHECK 0개, Dependabot config 0개 |
| **DB / Hasura / observability** | 5 | `ALTER TYPE ADD VALUE` rollback 누락, 5개 FK column 인덱스 누락 (production death-trap), Hasura select-only 의도 미문서화, OTel SDK 0% (interceptor 자리만 있음), 단일 `/health` (k8s liveness/readiness 분리 안 됨) |

### 제안된 17개 verifier (V34-V50)

| ID | 이름 | Severity | Tier |
|---|---|---|---|
| V34 | go-error-wrapping | warning | 2/3 |
| V35 | go-context-propagation | error | 2/3 |
| V36 | go-http-server-hardening ★ | error | 2/3 |
| V37 | go-test-race-coverage | error | 3 |
| V38 | golangci-strictness | error | 2/3 |
| V39 | go-context-scoped-logger | warning | 2/3 |
| V40 | actions-sha-pin ★ | error | 2/3 |
| V41 | actions-permissions-block | warning | 2/3 |
| V42 | dependabot-config | warning | 3 |
| V43 | ci-image-scanning ★ | error | 2/3 |
| V44 | dockerfile-base-digest-pin | warning | 2/3 |
| V45 | dockerfile-healthcheck | warning | 2/3 |
| V46 | migration-enum-rollback | warning | 2/3 |
| V47 | fk-index-discipline ★ | error | 2/3 |
| V48 | hasura-permission-rationale | info | 3 |
| V49 | otel-instrumentation | warning | 3 |
| V50 | health-endpoint-split ★ | error | 3 |

★ = 의료/금융 ship-blocker tier

### 우선순위 (impact-per-effort)

```
Sprint 1 (즉시 ship 권장):
  V40 SHA-pin actions          ← 8개 third-party action 봉인 (supply chain)
  V47 FK index discipline      ← 5개 컬럼 인덱스 누락 (production death-trap)
  V50 /livez vs /readyz        ← single /health 분리 (k8s outage 방지)
  V36 HTTP server timeouts     ← slowloris 봉쇄

Sprint 2:
  V37 -race in CI              ← 알려진 concurrent test 가 CI gate 뒤에 숨어있음
  V41 permissions: block       ← least-privilege GITHUB_TOKEN
  V43 image scanning           ← CVE 감사 trail (regulatory)

Sprint 3:
  V42 Dependabot               ← CVE 자동 PR 흐름
  V49 OTel SDK                 ← production 가시성

Long tail:
  V34, V35, V38, V39, V44, V45, V46, V48
```

### 결과물 (Phase 53)
- 17개 `skills/V##-{name}/SKILL.md` 디자인 spec ship (이번 phase)
- Python 구현은 별도 phase 들에서 (Sprint 별로)
- `BUILTIN_GROUPS` 업데이트는 implementation 시점에 (지금은 design 만 lock)

---

## Audit 의 작동 메커니즘 — 어떻게 진행했나

각 audit 는 다음 패턴을 따랐다:

```
                        ┌──────────────────────────────────────┐
                        │   1. 비교 기준 정의                  │
                        │      (12-Factor, CIS, OpenSSF, ...)  │
                        └──────────────────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────────────┐
                        │   2. 병렬 research agent spawn       │
                        │      (3-4명, 각자 다른 surface)       │
                        └──────────────────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────────────┐
                        │   3. 각 agent 가:                    │
                        │      - 실제 파일 읽기 (file:line)    │
                        │      - 기준과 비교                   │
                        │      - gap + citation 보고           │
                        └──────────────────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────────────┐
                        │   4. 결과 synthesis (lead)           │
                        │      - 우선순위 부여                 │
                        │      - verifier proposal 으로 변환   │
                        │      - SKILL.md / 코드 / docs 작성   │
                        └──────────────────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────────────────┐
                        │   5. 결정 + ship                     │
                        │      - 사용자 컨펌 (큰 변경)          │
                        │      - test + ruff + commit          │
                        │      - audit history 에 기록 (이 문서)│
                        └──────────────────────────────────────┘
```

### Agent 활용 — 왜 병렬 research

각 audit 는 **수십 개 파일을 읽고 best practice docs 를 cite** 해야 함. 단일
세션에서 sequential 로 진행하면:
- Context window 소진 (300+ files read)
- citation 출처 추적 어려움 (날짜 검증 등)

병렬 agent (Anthropic Claude `Agent` tool) 패턴:
- 3-4명 spawn, 각자 다른 surface 점검
- 각 agent 는 600-1200 단어 보고서 + file:line citations
- Lead 가 수신 후 synthesis (cross-cutting 우선순위)

이 방식이 single-shot LLM 의 hallucination + scope-creep 문제를 줄임.

---

## 다음 audit 후보

언제 다음 audit 가 필요할까:

1. **새 기술 스택 도입 시** — target project 가 Kafka, Redis, Vault 등 새 컴포넌트 추가 → 그 영역 verifier 커버리지 audit
2. **새 best-practice 발견 시** — OWASP / CIS / SLSA 등이 framework 업데이트 → 해당 영역 재점검
3. **incident 발생 후** — production incident 의 root cause 가 verifier 로 catch 가능했는지 → "이 incident 가 verifier 부재 때문에 났는가?" audit
4. **6개월 정기** — verifier 시스템도 corrosion. 정기적으로 1) 사용 안 되는 V## 식별 (per-project metrics 활용), 2) false-positive 비율 점검, 3) 최신 best-practice 반영 점검

각 audit 는 시작 시 이 문서에 placeholder section 을 추가하고 끝날 때 결과를
back-fill 하는 식으로 운영.

---

## 참고

- 카테고리 맵 (verifier 분류): `docs/VERIFIERS-CATEGORIES.md`
- 사용자 가이드: `docs/CONFIGURATION.md`
- 변경 이력: `CHANGELOG.md`
- 각 verifier 의 spec: `skills/V##-{name}/SKILL.md`
- 코드: `hooks/validators/*.py`, `lib/*.py`
