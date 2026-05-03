---
name: env-vs-config-decision
description: 새 설정 값을 추가할 때 env (환경변수) / config 파일 (yaml) / 코드 (.go·.ts) 중 어디에 두어야 하는지 결정하는 가이드. 12-Factor Config + OWASP Secrets + Viper / K8s ConfigMap 의 합의 패턴을 한 표로 정리. V01-env-config / V22-multi-env 가 mechanical 검증을 한다면, 이 skill 은 decision criteria 를 박제한다.
---

# Env vs Config — Decision Skill

AI 에이전트 / 개발자가 새 설정 값을 추가할 때마다 **"이건 env 야 yaml 이야 코드야?"** 를 물어보는 자리. 4가지 룰만 외우면 끝난다.

> **관련 검증기**: 이 skill 은 분류 *기준* 만 정의한다. 분류 *결과* 의 mechanical 정합성 (예: yaml 의 secret literal, env.example 누락) 은 [`V01-env-config`](../V01-env-config/SKILL.md) 와 [`V22-multi-env`](../V22-multi-env/SKILL.md) 가 stop-hook 시점에 강제한다.

---

## 📜 The One Rule

```
새 값 X 를 어디에 둘지 결정하는 4단계 질문:

  ① X 가 시크릿인가?               → YES → env. 끝. (yaml 절대 ❌)
  ② X 가 환경마다 다른가?           → YES → env 우선 (yaml 에 default 두는 건 OK)
  ③ X 가 코드 동작 자체인가?        → YES → 코드 (.go / .ts)
  ④ 그 외 (운영 튜닝, 환경 동일)    → yaml (commit)
```

이게 전부. 추가 nuance 는 §Edge Cases 에서.

---

## 🧠 Why this rule?

이 분류를 가르는 기준은 **"누가 이 값을 결정하나?"** 와 **"유출되면 사고나?"** 두 축이다.

```
                              유출 = 사고?
                              ──────────
                              YES         NO
                          ┌─────────────────────┐
누가 결정?  Secret 매니저 │  ① Secret (env)       │  ─
            (rotation)    │                     │
                          ├─────────────────────┼──────────────────┤
            배포 시스템    │  ─                   │  ② Env-public    │
            (K8s/Docker)  │                     │     (env)        │
                          ├─────────────────────┼──────────────────┤
            코드 작성자    │  ─                   │  ④ Tuning (yaml) │
            (PR 리뷰)      │                     │  ③ Code (.go)    │
                          └─────────────────────┴──────────────────┘
```

각 셀의 정당성:

- **시크릿은 무조건 env**: yaml 은 git 에 commit 됨. commit 된 시크릿은 git history 영구. secret manager (K8s Secret / Vault / SOPS) 와 자연스럽게 연결되는 건 env var 뿐. 출처: [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html), OWASP, last updated 2024-09, retrieved 2026-05-04.

- **환경별 인프라 값은 deploy 가 주입**: K8s ConfigMap → env var 가 cloud-native 표준. 같은 image artifact 가 모든 환경에 흐를 수 있게 함 ([12-Factor V](https://12factor.net/build-release-run), Adam Wiggins, 2011, retrieved 2026-05-04).

- **운영 튜닝은 yaml 에 commit**: 타입(숫자/duration/리스트), 주석, 코드 리뷰가 의미 있는 값. yaml 이 가장 자연스러운 표현 매체.

- **코드 동작은 코드**: deploy 와 무관, 변경하려면 PR + 테스트. env 로 빼고 싶은 충동은 "정말 deploy 마다 바뀌나?" 자문하면 보통 NO.

---

## 🏷️ The 4 Tiers — 구체 예시

### Tier ① Secret (env, 시크릿 매니저 주입)

| 예시 | 왜 시크릿? |
|------|-----------|
| `database.password` / `APP_DATABASE_PASSWORD` | DB 접근 = 데이터 유출 |
| `jwt.access_token_secret` | JWT 위조 가능 |
| `jwt.refresh_token_secret` | 세션 탈취 |
| `hasura.admin_token` | admin = 모든 데이터 접근 |
| `email.sendgrid.api_key` | 이메일 spoofing |
| `oauth.google.client_secret` | OAuth 위조 |
| `sms.twilio.auth_token` | SMS 비용 + spoofing |
| `payment.lemonsqueezy_api_key` | 결제 (돈) |
| `stripe.secret_key` (`sk_live_*`) | 결제 (돈) |
| `storage.s3.secret_access_key` | S3 권한 |

**저장 위치**:
- Local Docker dev: `docker-compose.yaml` 의 `environment:` 블럭에 dev placeholder commit (`ax-finance_pass` 같은 dummy) — 진짜 시크릿 아니므로 OK
- Production: K8s Secret manifest (별도 repo 또는 Vault sync), 절대 image 에 baked-in ❌

### Tier ② Env-public (환경별 인프라 endpoint)

| 예시 | 환경별 다름 |
|------|-----------|
| `database.host` | `postgres` (Docker) / `prod-pg.internal` (K8s) |
| `database.port` | 5432 (보통 같음, 가끔 다름) |
| `database.user` | `ax-finance` (인증과 묶임) |
| `database.dbname` | `ax-finance` |
| `database.sslmode` | `disable` (dev) / `require` (prod) |
| `keycloak.auth_url` | `localhost:8180` / `auth.airsmed.io` |
| `keycloak.realm` | 같지만 분리 가능 |
| `keycloak.client_id` | publishable, env 별 다름 |
| `hasura.graphql_endpoint` | `hasura:8080` / `hasura.internal:8080` |
| `storage.s3.endpoint` | `minio:9000` / `s3.amazonaws.com` |
| `storage.s3.region` | 리전별 |
| `storage.s3.bucket_name` | env 별 |
| `oauth.google.client_id` | dev / prod 별 (publishable) |
| `sms.twilio.account_sid` | sandbox / live |
| `sms.twilio.from_number` | env 별 |
| `payment.lemonsqueezy_store_id` | env 별 |
| `email.sendgrid.from_email` | env 별 |
| `LOG_LEVEL` | `debug` (dev) / `info` (prod) |

**저장 위치**:
- yaml 에 default 둘 수 있음 (image-baked default)
- env 가 override (cloud-native 의 표준 패턴: K8s ConfigMap → env var)
- Viper 가 자동으로 합침 (env > yaml)

### Tier ③ Code (Go / TypeScript)

| 예시 | 왜 코드? |
|------|---------|
| 라우트 등록 (`cmd/server/main.go`) | 코드의 일부, 함수 호출 |
| Connect-RPC interceptor 체인 | middleware 순서는 코드 |
| status enum → 한국어 매핑 | 상수, type 안전 |
| region 분류 함수 (`internal/finance/region.go`) | 비즈니스 룰 |
| invoice number format | ADR-001 박제 + 코드 상수 |
| Stripe API version (`"2024-09-30.acacia"`) | 의존성 버전, 코드 |

**저장 위치**:
- `.go` / `.ts` 파일, type 안전성 활용
- 환경별로 안 바뀜 → env 로 뺄 이유 없음
- 변경 시 PR + 리뷰 + 테스트

### Tier ④ Tuning (yaml committed)

| 예시 | 왜 yaml? |
|------|---------|
| `connect_host: "0.0.0.0"` | 환경 동일, 단순 string |
| `connect_port: 7778` | 숫자, 환경 거의 동일 |
| `cors_origins: [...]` | 리스트 → yaml 자연스러움 |
| `http_timeout: 30s` | duration 타입 |
| `db_pool_size: 10` | 숫자 튜닝 |
| `retry_count: 3` | 정책 |
| `rate_limit: 1000/min` | 운영 정책 |
| `dev: true/false` | boolean (env override OK) |

**저장 위치**:
- `server/config/<name>.yaml` (committed)
- 환경별 미세 차이는 `<name>.docker.yaml` / `<name>.local.yaml`
- 변경 시 PR 리뷰 (커뮤니티 가시성)

---

## 🔀 Edge Cases — 헷갈리는 분기

### Case A — 환경별로 다른 리스트 (`cors_origins`)

```
딜레마:
  • 환경별 다름 → env 후보
  • 리스트 → yaml 후보 (env 는 string)

정답: 둘 다 가능. 프로젝트 컨벤션 정하기.

  옵션 A — yaml 환경별 파일:
     ax-finance.docker.yaml: cors_origins: [...]
     ax-finance.production.yaml: cors_origins: [...]
     → 코드 리뷰로 변경 추적 가능

  옵션 B — env 로 (쉼표 구분):
     APP_CORS_ORIGINS=a,b,c
     → 운영 시점 hot change 가능 (kubectl edit configmap)

  추천: 변경 빈도가 낮으면 yaml, 운영 hot change 자주면 env.
       이 프로젝트 (ax-finance) 는 yaml 권장 — 변경이 거의 없음.
```

### Case B — 모든 환경에서 같은 값 (예: `keycloak.realm: "ax-finance"`)

```
정답: yaml 에 두고, 미래 분기 가능성 있으면 env override 허용 (Viper 자동 처리).

  → 지금 같은 값이라고 코드 hardcode 하지 마라.
    "절대 안 변할" 보장은 어렵다. yaml 이 안전한 default.
```

### Case C — 환경별 인프라 값을 yaml 에 default 두기 (예: `database.host`)

```
정답: yaml 에 환경별 default + env override 허용.

  • dev: ax-finance.local.yaml 의 host=localhost (yaml 이 정함)
  • Docker dev: ax-finance.docker.yaml 의 host=postgres (yaml 이 정함)
  • Production: K8s ConfigMap 의 APP_DATABASE_HOST=prod-pg... (env 가 정함)

  → 같은 image artifact 가 모든 환경에 흐름 (12-Factor V).
  → yaml = image-baked default, env = deploy-time override.
```

### Case D — `${APP_*}` placeholder 의 함정

```
yaml:
   database:
     password: "${APP_DATABASE_PASSWORD}"   ← 자동 expand 안 됨!

  ⚠️ Viper 는 "${...}" 를 literal 문자열로 저장한다.
  ⚠️ 런타임 expansion 은 viper.AutomaticEnv() 가 같은 키를 env 에서 찾았을 때만.
  
  의도: yaml 에 placeholder 표시, 실값은 env (시크릿 매니저) 가 주입.
  결과: env 가 unset 이면 password = "${APP_DATABASE_PASSWORD}" literal —
        DB 인증 실패. 메시지 직관적이지 않음.
  
  대응: 시작 시점 검증 (validateConfig 함수) 에서 secret 키가 placeholder 형식이면 panic.
```

### Case E — Frontend (Vite) 는 다르다

```
[Backend (Go + Viper)]
   yaml + env 하이브리드 (Viper 가 자동 합침)

[Frontend (Vite)]
   yaml 없음 (Vite 는 yaml loader 표준 아님)
   → Tier ② → web/env/.env.[mode]
   → Tier ③④ → web/src/config/*.ts (TypeScript)

   ⚠️ VITE_* prefix 는 모두 client JS bundle 에 박힘.
      누구나 DevTools 로 읽을 수 있음. 시크릿 절대 ❌.
```

---

## 🚨 Anti-patterns (PR reject 사유)

### 1. ❌ yaml 에 시크릿 literal

`server/config/<name>.yaml` 의 `password` / `secret` / `api_key` / `token` 키 값이 `${VAR}` placeholder 가 아니라 8+ 글자 literal 로 박힌 경우.

- **왜 안 됨**: yaml 은 git 에 commit → secret 영구 노출 → revoke 후에도 history 에 남음.
- **올바른 형식**: `password: ${APP_DATABASE_PASSWORD}` (literal expansion 은 Viper `AutomaticEnv` 가 처리).
- **검증**: V01-SECRET-IN-CONFIG 가 stop hook 에서 차단.

### 2. ❌ VITE_* 로 시크릿 노출

`web/env/.env.*` 또는 코드의 `import.meta.env.VITE_*` 로 시크릿 키 (`sk_live_*` 같은 backend-only credential) 를 expose.

- **왜 안 됨**: `VITE_*` prefix 의 모든 값은 클라이언트 JS bundle 에 박힌다 — DevTools 로 누구나 읽을 수 있음.
- **올바른 분리**: publishable key (`pk_*`) 만 frontend 의 VITE_*, secret (`sk_*`) 은 backend env (`APP_STRIPE_SECRET_KEY`).
- **출처**: [Vite — Env Variables and Modes](https://vite.dev/guide/env-and-mode) 의 *"VITE_\* variables should NOT contain sensitive information"* 명시.

### 3. ❌ deploy 와 무관한 값을 env 로

UI 상수 (예: `MAX_INVOICE_LINES_PER_PAGE=50`) 를 env 로 빼는 경우.

- **왜 안 됨**: 환경별로 안 바뀐다. 코드의 일부 (Tier ③).
- **신호**: "정말 deploy 마다 바뀌나?" 자문하면 보통 NO → 코드로.

### 4. ❌ yaml 과 env 양쪽에 같은 키 무계획 중복

같은 키가 yaml 과 env 둘 다에 있는데 관계 (default vs override) 가 코멘트로 명시되지 않음.

- **왜 안 됨**: 어느 쪽이 진본인지 인지 부담. drift 위험. 코드 리뷰 시 어느 쪽을 봐야 할지 모름.
- **올바른 패턴 A**: yaml = image-baked default + env = deploy-time override (Viper precedence). yaml 에 코멘트 박기.
- **올바른 패턴 B**: 한 곳으로 통일 (yaml 만, 또는 env 만).

### 5. ❌ pflag 만 보고 env path 무시

config path 결정 시 `pflag.Lookup("config").Changed` 만 체크 → CLI flag 만 인식, `APP_CONFIG` env var 무시.

- **왜 안 됨**: docker-compose / K8s ConfigMap 으로 `APP_CONFIG=...` 주입해도 코드가 이를 무시 → yaml 안 읽힘 → silent default fallback.
- **올바른 패턴**: `viper.GetString("config")` 로 flag + env 둘 다 인식 (Viper 가 알아서 합침).
- **사례**: ax-finance 프로젝트가 2026-05-04 에 fix 한 실제 버그. database.host 가 silent 하게 default `"localhost"` 사용 중이었음.

---

## 🎯 Decision Cheat Sheet — 한 페이지 요약

```
[새 값 X 를 어디에?]

   ┌─ 시크릿? ────────────── YES → env (Tier ① — secret manager)
   │
   ├─ 환경별 다름? ────────── YES → env 우선 (Tier ② — yaml default OK)
   │
   ├─ 코드 동작? ──────────── YES → 코드 (Tier ③ — .go / .ts)
   │
   └─ 그 외 ──────────────────── → yaml (Tier ④ — committed)


[저장 매핑]

   Backend (Go + Viper):
     Tier ① → docker-compose env (dev) / K8s Secret (prod)
     Tier ② → docker-compose env (dev) / K8s ConfigMap (prod)
                + yaml 에 default 두는 건 OK (Viper 가 env override 허용)
     Tier ③ → cmd/, internal/ 의 .go 파일
     Tier ④ → server/config/<name>.yaml (committed)

   Frontend (Vite):
     Tier ① → 절대 ❌ (VITE_* 는 public)
     Tier ② → web/env/.env.[mode]
     Tier ③ → web/src/config/*.ts
     Tier ④ → web/src/config/*.ts (yaml 미지원)


[Viper 우선순위 — Backend]
   ① viper.Set()              (가장 강함, 거의 안 씀)
   ② flag (--port=8888)        ← Tier ④ override 가능
   ③ env var (APP_*)           ← Tier ① + ②
   ④ config file (yaml 1개)    ← Tier ② default + Tier ④
   ⑤ key/value store (etcd)    (이 프로젝트 미사용)
   ⑥ SetDefault()              ← 코드 default
```

---

## 🔗 Related Skills

- [V01-env-config](../V01-env-config/SKILL.md) — yaml 의 secret literal / env.example 누락 mechanical 검증 (이 skill 의 Tier ① 룰을 enforcement)
- [V22-multi-env](../V22-multi-env/SKILL.md) — `APP_*` prefix 컨벤션 / yaml 키와 env var 매핑 일관성 (이 skill 의 Tier ② 룰을 enforcement)
- [test-classical](../test-classical/SKILL.md) — 비-validator 가이드 skill 의 컨벤션 참고

---

## 📚 References

- [The Twelve-Factor App — III. Config](https://12factor.net/config) — Adam Wiggins, originally 2011, continuously updated, retrieved 2026-05-04. "Config = everything that varies between deploys" 의 정의 + env vars 가 표준.
- [The Twelve-Factor App — V. Build, Release, Run](https://12factor.net/build-release-run) — Adam Wiggins, originally 2011, retrieved 2026-05-04. Build artifact 와 config 분리 — 같은 image 가 모든 환경에 흐른다는 근거.
- [The Twelve-Factor App — X. Dev/Prod Parity](https://12factor.net/dev-prod-parity) — Adam Wiggins, originally 2011, retrieved 2026-05-04. backing service 종류 동일 + 환경별 endpoint 만 다름.
- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) — OWASP, last updated 2024-09, retrieved 2026-05-04. 시크릿은 secret manager 에서 동적 주입 권장 (env-only 도 차선책으로 인정).
- [Storing secrets in env vars considered harmful](https://blog.arcjet.com/storing-secrets-in-env-vars-considered-harmful/) — Arcjet, published 2024-08, retrieved 2026-05-04. env var 도 안전하지 않다는 modern critique — 시크릿 매니저로 격상 권장.
- [Vite — Env Variables and Modes](https://vite.dev/guide/env-and-mode) — Vite team, continuously updated, retrieved 2026-05-04. `VITE_*` 가 client bundle 에 박힌다는 보안 제약.
- [spf13/viper README — Working with Configuration](https://github.com/spf13/viper) — Steve Francia, continuously developed since 2014-04, retrieved 2026-05-04. `Set > flag > env > config > kv > defaults` 우선순위 (이 skill 의 Backend 매핑 근거).
- [Kubernetes — ConfigMap concepts](https://kubernetes.io/docs/concepts/configuration/configmap/) — Kubernetes docs, continuously updated, retrieved 2026-05-04. ConfigMap → env var 주입 (Tier ② 의 cloud-native 표준 패턴).
- [Kubernetes — Distribute Credentials Securely Using Secrets](https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/) — Kubernetes docs, continuously updated, retrieved 2026-05-04. Secret → env var 주입 (Tier ① 의 cloud-native 표준 패턴).
- [getsops/sops](https://github.com/getsops/sops) — Mozilla → CNCF, continuously developed since 2017-04, retrieved 2026-05-04. dev secret 을 git 에 (encrypted) commit 하는 모던 패턴 — 팀 ≥5 시점에 도입 검토.

### Project-internal references

- `ax-finance-project/docs/ADR/009-env-vs-config-strategy.md` — 본 4-Tier 분류를 ax-finance 프로젝트 구체 컨텍스트에 적용한 ADR (2026-05-03 작성).
- `ax-finance-project/server/internal/config/config.go:101-140` — Viper 를 4단 우선순위로 통합하는 ParseConfig 구현 (2026-05-04 fix 후 viper.GetString 으로 env 까지 인식).
- `ax-finance-project/server/CONFIG_GUIDE.md` — 프로젝트 자체 우선순위 가이드 (`flag > env > config > default`).
