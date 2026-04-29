# Verifiers — Hook · Validator 종합 카탈로그

> 본 문서는 verifiers 의 **세 단계(Tier 1/2/3) hook 시스템** 과 **19 개 등록 validator + 20 개 Tier 2 skill** 의 동작·목적을 한 곳에 정리합니다. 모든 단정 (validator id, file pattern, 검사 내용, 모드 차이) 은 `hooks/`, `skills/` 소스에서 직접 추출한 것입니다.

---

## 0. 핵심 아키텍처 (한 눈에)

| Tier | 발동 이벤트 | 진입점 | matcher / 시점 | 핵심 역할 |
| :--: | ----------- | ------ | -------------- | --------- |
| **1** | `PostToolUse` | `hooks/security_hook.py` | `Edit \| Write \| MultiEdit` · timeout 10s · <100ms | regex 기반 시크릿(V08) 즉시 차단 — 가장 가벼운 첫 줄 방어 |
| **2** | (hook 미등록) | `hooks/router.py` + `skills/verify-*` (20개) | Claude 가 자율 판단해 skill 호출 / 사용자가 `/verify` 실행 / `just verify-one V##` | file_path 매칭 validator 만 골라 실행 — 상황별 비용 통제 |
| **3** | `Stop` | `hooks/stop_validator.py` | turn 종료 시도 시 단일 발동 · timeout 120s | 등록된 19개 validator (V01~V19) 일괄 실행 · circuit breaker x3 · FeedbackTracker |

```
PostToolUse (Edit/Write/MultiEdit)
   └─> Tier 1: security_hook.py
         · 시크릿 regex 매칭 시 decision:"block" + reason
         · 매칭 없으면 {} 반환

(Tier 2 는 settings.json 의 hook 으로 자동 등록되지 않음)
   └─> Claude/사용자가 호출했을 때만:
         · /verify 슬래시 명령 → hooks/router.py
         · Claude 가 verify-* skill 자율 호출
         · just verify-one V## (디버그)

Stop 이벤트 (Claude turn 종료 시도)
   └─> Tier 3: stop_validator.py
         · get_all_validators() 19개 일괄 실행 (mode="stop")
         · errors 있음 → block + reason → Claude 다시 수정
         · stop_hook_active && block 누적 ≥ 3 → 강제 approve (deadlock 방지)
         · FeedbackTracker 가 같은 rule+file 반복 시 메시지 추가
```

**우선 읽을 것**:
- Tier 1 은 `~/.claude/settings.json` 의 `hooks.PostToolUse[matcher: Edit|Write|MultiEdit]` 항목, Tier 3 은 `hooks.Stop` 항목 — 둘 다 `scripts/merge_settings.py` 가 자동 등록.
- Tier 1 (timeout 10s) ↔ Tier 3 (timeout 120s) — 비용 분리.
- Tier 2 는 settings.json 에 hook 으로 직접 묶이지 않음 — Claude 가 skill 시스템으로 호출하거나 사용자가 `/verify` 명령으로 `hooks/router.py` 직접 실행.

---

## 1. Tier 1 — `hooks/security_hook.py` (PostToolUse 보안 즉시 차단)

| 항목                | 값                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------ |
| 등록 hook event     | `PostToolUse`                                                                        |
| matcher             | `Edit\|Write\|MultiEdit`                                                             |
| 의존성              | 없음 (regex only, `requires-python = ">=3.11"`, `dependencies = []`)                  |
| 성능 예산           | < 100 ms (모든 Edit/Write 마다 실행되므로 강제됨)                                       |
| 출력                | `{}`(통과) 또는 `{"decision":"block","reason":...,"additionalContext":...}`           |
| 등록 위치           | `scripts/merge_settings.py:31-41` — `TIER1_HOOK` 정의                                 |

### 검사 항목 (security_hook.py:23-31, 38-70)

수정한 파일 1 개를 읽어 라인 단위 정규식 매칭. 주석 (`//`, `#`, `*`, `/*`, `<!--`) 라인은 제외.

| 패턴                                            | 의미                          |
| ----------------------------------------------- | ----------------------------- |
| `AKIA[A-Z0-9]{16}`                              | AWS Access Key                |
| `ghp_[a-zA-Z0-9]{36}`                           | GitHub Personal Access Token  |
| `gho_[a-zA-Z0-9]{36}`                           | GitHub OAuth Token            |
| `sk-[a-zA-Z0-9]{20,}`                           | OpenAI / Anthropic API Key    |
| `sk_live_[a-zA-Z0-9]{20,}`                      | Stripe Live Key               |
| `xoxb-[a-zA-Z0-9\-]+`                           | Slack Bot Token               |
| `password\s*[:=]\s*["\'][^"\'$\{]{8,}["\']`     | 하드코딩 비밀번호             |

**제외 경로** (security_hook.py:35): `.env`, `.env.production`, `.env.development`, `_test.go`, `test_`, `fixtures/`, `testdata/`, `mock`, `__tests__` — 의도된 시크릿(테스트 fixture) 또는 .env 의 정상 시크릿은 패스.

### 발견 시 출력 형태 (security_hook.py:98-123)

- `decision: "block"` — Edit/Write 결과가 즉시 차단되고 Claude 에게 **`reason`** (짧은 정리) 와 **`additionalContext`** (위치·수정 가이드 풀 스펙) 가 전달됨.
- 단일 finding 의 rule id 는 `V08-HARDCODED-SECRET`.

### 목적

1. **하드코딩 시크릿이 git 히스토리에 들어가는 것 자체를 차단** — 한 번 commit 되면 force-push 로도 완전 제거 어려움.
2. **턴 종료 전(Tier 3) 검사로는 늦음** — 시크릿이 포함된 파일이 다른 도구·로깅·테스트로 흘러가기 전에 끊어야 함.
3. **모든 Edit/Write 호출에 빠짐없이 거는 가장 가벼운 첫 줄 방어**. 무거운 검사(AST, 외부 명령) 는 의도적으로 배제.

---

## 2. Tier 2 — `hooks/router.py` + `skills/verify-*` (상황별 호출)

| 항목                | 값                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------ |
| 등록 hook event     | **없음** — `settings.json` 의 hook 으로 자동 등록되지 않음                              |
| 호출 경로 1         | Claude 가 적합한 `verify-*` skill 을 자율 판단·호출                                    |
| 호출 경로 2         | `/verify` slash command → `hooks/router.py` 로 라우팅                                 |
| 호출 경로 3         | 사용자가 `just verify-one V<NN>` 또는 `hooks/run_single.py`                           |
| router 동작         | `Edit/Write/MultiEdit` 의 file_path 를 받아 `validator.should_run(file_path)` 매칭     |
|                     | → 매칭된 모든 validator 의 `validate(ctx, file_path, mode="post_tool_use")` 일괄 실행  |
|                     | → 한 validator 가 raise 해도 다른 validator 는 계속 실행 (router.py:53-59 의 try/except) |

### Tier 2 Skill 카탈로그 (20 개)

| #  | Skill                  | 트리거 / 적용 파일                                          | 무엇을 검증                                                                                                  | 연결 V-ID    |
| -- | ---------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------ |
| 1  | `verify`               | 전체 검증 활성화 — V01~V19 중 매칭 validator 자동 실행        | router.py 통해 file_path 매칭하는 모든 validator 실행                                                          | V01~V19      |
| 2  | `verify-cheating`      | `*_test.*` 수정 시                                          | 테스트 함수 삭제, `t.Skip*`/`@pytest.mark.skip`/`it.skip` 추가, `assert` 카운트 감소, `assertEqual→assertTrue` 약화 | V13          |
| 3  | `verify-commit`        | Stop mode 전용 — 세션 종료 시                                 | `git status` 미커밋, `git diff --name-status` 로 구조/행동 혼합, 15+ 파일 수정, 소스 변경 vs 테스트 변경 비대칭, Conventional Commits 정규식 | V12          |
| 4  | `verify-complexity`    | `*.go/py/ts(x)` 수정 시                                      | cyclomatic > 10/20, cognitive > 15/30, function > 80/150 line, nesting > 4, params > 5 (Python 은 AST, Go/TS 휴리스틱) | V14          |
| 5  | `verify-deps`          | `*.go/py/ts(x)` 수정 시                                      | Clean Architecture 레이어 위반 (domain<repo<service<handler<cmd), 순환 import (madge 보조), `.verifiers/layers.yaml` 커스텀 룰 | V15          |
| 6  | `verify-docker`        | `docker-compose*.yaml`, `Dockerfile*` 수정 시                 | 호스트 포트 중복, VIRTUAL_HOST 대비 nginx-proxy 네트워크 누락, undefined network 참조, depends_on:service_healthy 대비 healthcheck 누락, `${VAR}` 정의 누락 | V05          |
| 7  | `verify-env`           | `.env*`, `config/*.yaml`, `docker-compose*` 수정 시           | config YAML 의 시크릿 하드코딩(3-Layer 위반), `os.Getenv("APP_*")` / `${VAR}` 가 `.env.example` 에 미정의, config 변종 키 불일치, `import.meta.env.VITE_*` 미정의 | V01          |
| 8  | `verify-go`            | `*.go`, `go.mod`, `go.sum`                                   | PostToolUse: `go vet`, `gofmt -l`, `go build`. Stop: `golangci-lint run --out-format json`, `go test -race`     | V06          |
| 9  | `verify-go-test`       | `*.go` 수정 시                                                | 변경 파일이 속한 패키지만 `go test -json` 실행, 3 회 연속 실패 시 PRD/테스트 점검 권유                            | V09          |
| 10 | `verify-graphql`       | `*.graphql`, `genqlient.yaml`, `genqlient.go`                | yaml 필수 필드, hash/mtime 기반 stale 감지, `*uuid.UUID` 필드의 `,omitempty` 누락, repo 호출 함수 vs gen 코드 매칭 | V02          |
| 11 | `verify-hasura`        | `hasura/migrations/**/*.sql`, `hasura/metadata/**/*.yaml`   | timestamp 오름차순 / 중복, `up.sql`/`down.sql` 페어, 위험 DDL (`DROP TABLE` w/o `IF EXISTS`, `TRUNCATE`, `ALTER TYPE`), metadata orphan | V04          |
| 12 | `verify-hasura-graphql`| Hasura 감지 시 Go 서비스 파일                                 | `database/sql` import 금지, `.Query*`/`.Exec*` 호출 금지, `gqlClient` 필드 누락 — exempt: migration, `_test.go`, `mocks/`, `setup/` | V15 (별도 모듈 — §6 주의사항 참조) |
| 13 | `verify-input`         | 함수 작성 시 (수동 가이드)                                    | SQL injection / XSS / Path traversal / SSRF / 입력 검증 / 인가·rate limit·멱등성 체크리스트 — **자동 검사 없음** | (수동)        |
| 14 | `verify-linter`        | Stop mode 전용                                                | `.golangci.yml`/`ruff.toml`/`eslint.config.js` 부재, `errcheck`/`E722`/`no-empty` 비활성화, `unused`/`F401`/`no-unused-vars` 비활성화, `gosec`/Bandit `S*`/`no-eval` 비활성화 | V16          |
| 15 | `verify-mock`          | `**/hooks/use*Data.ts(x)`                                   | `MOCK_*`/`fake*`/`DUMMY_*` 변수, `setState([{rank:...}])` 하드코드, `setTimeout` 가짜 지연, `// TODO: Replace with actual API`, API client import 누락 | V18          |
| 16 | `verify-proto`         | `proto/**/*.proto`, `buf.yaml`, `buf.gen.yaml`, `gen/**/*.go` | `buf lint`, hash/mtime stale, 모든 rpc method 의 handler 구현 존재, `buf breaking` (main 브랜치 비교)            | V03          |
| 17 | `verify-py-test`       | `*.py` 수정 시                                                | 관련 `pytest` 자동 실행, `test_*.py` 부재 경고, 3 회 연속 실패 시 PRD 검토                                       | V11          |
| 18 | `verify-ts`            | `*.ts(x)`, `package.json`, `tsconfig.json`                  | PostToolUse: `:any`/`as any`, 하드코드 색상, `console.log/debug/info`, MUI v4 `makeStyles`/`@material-ui/`, ESLint 단일 파일. Stop: `tsc --noEmit`, ESLint 전체, `madge --circular --json`, `knip` | V07          |
| 19 | `verify-ts-test`       | `*.ts(x)` 수정 시                                             | vitest/jest/bun 자동 감지 후 변경 파일 관련 테스트만 실행, 3 회 연속 실패 시 PRD 검토                            | V10          |
| 20 | `verify-ui`            | UI 컴포넌트 수정 시                                           | Chrome DevTools MCP 스크린샷 + Pencil/Figma 스펙 시각 비교 — 레이아웃·색상·타이포·접근성 (WCAG AA)                | V17 (미등록) |

### 목적

1. **상황별 비용 통제**: Edit/Write 마다 모든 validator 를 돌리면 비용 폭발. Tier 2 는 "이 파일에 의미 있는" validator 만 실행.
2. **Skill = 사람이 읽는 가이드 + 실행 매뉴얼**: 각 SKILL.md 가 Claude 에게 "언제, 무엇을, 어떻게" 검증할지 알려주는 **자연어 라우팅 메타데이터** 역할.
3. **사용자 수동 실행 경로 보장**: `/verify`, `just verify-one VNN` 으로 CI 없이도 즉석 검증.

---

## 3. Tier 3 — `hooks/stop_validator.py` (Stop 종합 검증)

| 항목                | 값                                                                                                   |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| 등록 hook event     | `Stop`                                                                                                |
| matcher             | 없음 (Stop 은 단일 이벤트)                                                                            |
| 타임아웃            | 120 s                                                                                                 |
| 의존성              | `pyyaml>=6.0`                                                                                         |
| 동작                | `get_all_validators()` 가 등록한 19 개 validator 의 `validate(ctx, file_path=None, mode="stop")` 호출 |
| 출력                | `{"decision":"approve"}` 또는 `{"decision":"block","reason":...,"additionalContext":...}`             |
| 등록 위치           | `scripts/merge_settings.py:42-50` — `TIER3_HOOK`                                                      |

### Stop 단계 고유 보호 장치

#### (a) Circuit Breaker — 무한 루프 차단 (stop_validator.py:30, 74-118)

- `stop_hook_active=true` 가 들어오면 이미 직전 turn 이 block 으로 끝났다는 뜻.
- 프로젝트 루트의 `.verifier-block-count` 파일에 연속 block 횟수를 기록.
- **`_MAX_CONSECUTIVE_BLOCKS = 3`** 도달 시 강제로 `decision: "approve"` 로 통과시키고 `additionalContext` 에 "남아있는 에러 N 개 — `just verify` 로 직접 확인하라" 안내.
- 이 장치가 없으면 Claude 가 같은 에러로 무한히 재시도하다 세션이 끝까지 못 끝나는 deadlock 발생.

#### (b) FeedbackTracker — 반복 위반 추적 (stop_validator.py:60-72, lib/feedback_tracker.py)

- 모든 finding 을 `record_all` 로 기록.
- 같은 rule + 같은 file 이 N 회 반복되면 `format_feedback_message()` 가 "이 패턴이 반복된다 — 근본 원인 점검" 메시지를 추가.
- `tracker.save_session()` 으로 세션 간 전수 분석 가능.

### 등록된 19 개 validator (validators/__init__.py:33-53 순서대로)

> 형식: **V-ID** · 모듈 · file_patterns → 검사 항목 · stop vs post_tool_use 차이 · 목적

#### V08 · `security.py` · 모든 파일 (file_patterns 비어있음)
- **검사**: `V08-HARDCODED-SECRET` (Tier 1 과 동일 패턴 + 폭넓은 추가 셋), `V08-CORS-WILDCARD` (`AllowAllOrigins: true`, `Access-Control-Allow-Origin: *`, `cors.Config{AllowOrigins:["*"]}`), `V08-PHI-LOGGING` (Go `log.Info().Str("email",..)`, JS `console.log(email)` — HIPAA), `V08-NO-GITIGNORE` / `V08-GITIGNORE-MISSING` (`.env`, `*.pem`, `*.key`, `.env.local`, `*.p12` 누락).
- **모드**: post_tool_use = 파일 1 개 시크릿/CORS/PHI. stop = 프로젝트 전체 Go/TS 스캔 + .gitignore 검증.
- **목적**: 자격증명·CSRF·PHI(HIPAA) 누출 차단. Tier 1 은 가장 흔한 7 개 패턴만 빠르게, Tier 3 의 V08 은 더 풍부한 보안 점검.

#### V13 · `ai_cheating_guard.py` · `*_test.go`, `test_*.py`, `*.test.ts(x)`, `*.spec.*`, `*__tests__*`
- **검사**: `V13-TEST-DELETED` (Edit 의 old vs new 에서 `func Test`/`def test_`/`it()` 카운트 감소), `V13-TEST-DISABLED` (`t.Skip`, `@pytest.mark.skip`, `it.skip`, `xit` 추가), `V13-ASSERTION-REMOVED` (`assert.*`/`require.*`/`expect()` 카운트 감소), `V13-TEST-WEAKENED` (`assertEqual→assertTrue`, `toEqual→toBeTruthy`), `V13-MOCK-EVERYTHING` (테스트당 `jest.mock()` > 5), `V13-TRIVIAL-TEST` (`assert True`, `expect(true).toBe(true)`).
- **모드**: post_tool_use 의 `check_edit()` 가 old_string ↔ new_string 카운트 비교; stop 은 파일 전체 trivial / over-mock 스캔.
- **목적**: Kent Beck 의 경험 — AI 가 통과 못하는 테스트를 삭제·skip 으로 우회. 테스트 무결성을 prompt 가 아니라 **메커니즘** 으로 강제.

#### V01 · `env_config.py` · `.env*`, `config/*.yaml`, `*.go/ts(x)`, `docker-compose*`
- **검사**: `V01-SECRET-IN-CONFIG` (config YAML 의 8+ 자 hardcoded password/secret/api_key/token, `${VAR}` 형식은 패스), `V01-ENV-MISSING` (`docker-compose` 의 `${VAR}` 또는 Go 의 `os.Getenv("APP_*")` 가 `.env.example` 에 없음), `V01-CONFIG-KEY-MISSING` (docker/local/production yaml 변종 간 키 집합 차이), `V01-VITE-ENV-MISSING` (`import.meta.env.VITE_*` 가 `web/env/` 에 미정의).
- **모드**: 파일시스템 기반이라 모드 차이 없음.
- **목적**: 3-Layer Separation (config / env / secret 분리) 강제. 배포 직전에야 발견되는 환경 변수 누락 사고 차단.

#### V02 · `graphql_gen.py` · `**/graph/queries/**/*.graphql`, `**/graph/schemas/*.graphql`, `genqlient.yaml`, `**/gqlclient/*.go`
- **검사**: `V02-YAML-MISSING-FIELD` (genqlient.yaml 의 `schema/operations/generated/package` 부재), `V02-STALE-GEN` (input graphql hash 가 캐시와 다름 + genqlient.go mtime 비교), `V02-OMITEMPTY` (생성된 코드의 `*uuid.UUID` 필드에 `,omitempty` 누락 → null UUID JSON 마샬링 버그), `V02-MISSING-FUNCTION` (repository 가 호출하는 `gqlclient.X()` 가 generated 에 없음).
- **모드**: post_tool_use = yaml + stale; stop = 추가로 함수 참조 검증.
- **목적**: GraphQL 스키마 변경 후 코드젠 누락으로 런타임 에러 발생하는 빈번한 버그 패턴 차단.

#### V03 · `proto_connect.py` · `**/proto/**/*.proto`, `buf.yaml`, `buf.gen.yaml`, `**/gen/**/*.go`
- **검사**: `V03-BUF-LINT` (`buf lint` 의 `file:line:col:rule` 파싱), `V03-STALE-GEN` (proto hash + gen mtime), `V03-UNIMPLEMENTED-RPC` (proto 의 `rpc method` 마다 `internal/*.go` 에 handler 함수 매칭), `V03-BREAKING` (`buf breaking` vs main 브랜치 — git worktree 환경에서도 동작하도록 `git rev-parse --git-common-dir` 사용).
- **모드**: post_tool_use = lint + stale; stop = 추가로 handler 매핑 + breaking change.
- **목적**: protobuf 컨트랙트가 코드젠/구현/소비자와 어긋나면 Connect-RPC 런타임 에러. main 비교로 클라이언트 호환성도 보호.

#### V04 · `hasura_migration.py` · `**/hasura/migrations/**/*.sql`, `**/hasura/metadata/**/*.yaml`
- **검사**: `V04-TIMESTAMP-ORDER` (마이그레이션 디렉토리 timestamp 오름차순), `V04-DUPLICATE-TIMESTAMP`, `V04-MISSING-FILE` (`up.sql` ↔ `down.sql` 페어), `V04-DANGEROUS-DDL` (`DROP TABLE` w/o `IF EXISTS`, `DROP COLUMN`, `TRUNCATE`, `ALTER TYPE` — `-- INTENTIONAL:` 주석으로 우회 가능), `V04-METADATA-ORPHAN` (metadata 의 테이블이 어느 마이그레이션에도 `CREATE TABLE` 없음).
- **모드**: post_tool_use = 단일 파일 timestamp/ddl/페어; stop = 전체 metadata vs 전체 마이그레이션 정합성.
- **목적**: 데이터 손실 차단(rollback 가능성 보장) + Hasura metadata 와 SQL 마이그레이션 불일치 방지.

#### V05 · `docker_compose.py` · `docker-compose*.yaml/yml`, `Dockerfile*`, `*.Dockerfile`
- **검사**: 호스트 포트 충돌, VIRTUAL_HOST 설정 시 nginx-proxy 네트워크 누락, undefined 네트워크 참조, `depends_on: { x: { condition: service_healthy }}` 인데 healthcheck 없음, `${VAR}` 가 `.env*` 에 없음. Dockerfile: 멀티스테이지 빌드, non-root USER, EXPOSE, `.dockerignore` 누락 (`COPY . .` 위험), `:latest` 태그. **프로덕션 모드** (`*.production`/`.prod` 파일명): exposed 포트 금지, dev 플래그 금지, 와일드카드 CORS 금지, Traefik 라벨, 리소스 limit. **Dev override**: 핫리로드 볼륨, `build.target='dev'`.
- **모드**: post_tool_use 만 (Dockerfile 변경 없으면 stop 의미 적음). 파일명으로 dev/prod 컨텍스트 추론.
- **목적**: 인프라 설정 실수 (포트 충돌, healthcheck 빠진 의존성, 시크릿 leakage via `COPY . .`) + 프로덕션 안전 정책.

#### V06 · `go_quality.py` · `**/*.go`, `go.mod`, `go.sum`
- **검사**: post_tool_use 의 `V06-GO-VET` (`go vet ./...`, stderr 정규식 `(.+\.go):(\d+):\d+: (.+)`), `V06-GOFMT` (`gofmt -l file.go` exit≠0), `V06-BUILD-FAIL` (`go build ./...`). stop 의 `V06-LINT-*` (`golangci-lint run --out-format json` → Issues[].FromLinter/Text/Pos.Line), `V06-TEST-FAIL` (`go test -race -count=1 ./...` 또는 Makefile `test:` 타겟; `--- FAIL: (\S+)` 정규식).
- **모드**: post_tool_use = 빠른 vet/fmt/build (각 < 수초); stop = golangci-lint + race-test (≤ 180 s).
- **목적**: 컴파일·포맷·의심 패턴은 즉시, 무거운 lint·race-test 는 turn 종료 시 한 번.

#### V07 · `ts_quality.py` · `**/*.ts(x)`, `package.json`, `tsconfig.json`
- **검사**: post_tool_use — `V07-NO-ANY` (`:\s*any\b|as\s+any\b|<any>`), `V07-HARDCODED-COLOR` (style 속성 = `#hex|rgb|rgba|hsl`, theme 권유), `V07-NO-CONSOLE` (`console.log/debug/info` — 테스트/스토리북 제외), `V07-DEPRECATED-MUI` (`makeStyles`, `withStyles`, `@material-ui/`), `V07-ESLINT-*` (`bun run eslint --format json` 단일 파일). stop — `V07-TSC-*` (`bun run tsc --noEmit --pretty`, `(.+)\((\d+),\d+\): error (TS\d+): (.+)`), `V07-CIRCULAR-IMPORT` (`bunx madge --circular --json src/`), `V07-UNUSED-CODE` (`bunx knip`).
- **목적**: 타입 안전성, 테마 일관성, 순환 import 방지, 미사용 코드 정리.

#### V09 · `go_test_runner.py` · `**/*.go`, `go.mod`
- **검사**: 변경 파일이 속한 패키지만 `go test -json` 실행. 라인별 JSON 의 `Action: "fail"` 추출. `V09-NO-TEST` (해당 파일에 대응되는 `_test.go` 부재 — 경고). `V09-REPEATED-FAIL` (failure tracker 가 3 회 연속 같은 테스트 fail 감지 시 — PRD/test 자체가 잘못됐을 수 있음).
- **모드**: post_tool_use 만 (stop 은 V06 의 `go test -race ./...` 가 더 포괄적).
- **목적**: 패키지 단위 빠른 피드백 + flaky / 잘못된 테스트 명세 감지.

#### V10 · `ts_test_runner.py` · `**/*.ts(x)`
- **검사**: vitest > jest > bun test 우선순위로 자동 감지(config 파일 + package.json scripts). 변경 파일에 대응되는 테스트만 실행. `V10-TEST-FAIL` / `V10-NO-TEST` / `V10-REPEATED-FAIL` (V09 와 동일 구조).
- **목적**: TS/React 변경의 즉시 회귀 감지. V07 의 무거운 stop-mode tsc 와 분리.

#### V11 · `py_test_runner.py` · `**/*.py`
- **검사**: 변경 파일에 대응되는 `pytest` 만 실행. `V11-TEST-FAIL` / `V11-NO-TEST` / `V11-REPEATED-FAIL`. 실패 트래커는 V09/V10/V11 공유.
- **모드**: post_tool_use 만 (stop 은 V19 가 전체 `pytest` 수행).
- **목적**: TDD 사이클의 빨간/초록 즉시 피드백.

#### V19 · `py_quality.py` · `**/*.py`, `**/pyproject.toml`, `**/ruff.toml`
- **검사**: post_tool_use — `V19-RUFF-CHECK` (단일 파일 `ruff check --output-format text --no-fix`, `(.+?):(\d+):(\d+): (\S+) (.+)` → `V19-RUFF-{CODE}` rule id 보존), `V19-RUFF-FORMAT` (`ruff format --check`). stop — `V19-RUFF-ALL` (프로젝트 전체 `ruff check .`, 최대 20 개 finding 후 요약), `V19-TEST-FAIL` (`pytest -x -q --tb=no`, `(\d+) failed`, `FAILED\s+(\S+)`).
- **목적**: ruff lint/format + 전체 pytest gate. V11 의 파일 단위와 V19 의 프로젝트 단위 분리.

#### V12 · `commit_discipline.py` · stop mode 전용 (file_patterns 비어있음)
- **검사**: `V12-UNSTAGED-CHANGES` (`git status --porcelain`), `V12-LARGE-DIFF` (15+ 파일), `V12-MIXED-CHANGE` (`git diff --name-status HEAD` 의 R(rename) vs M(modify) 혼재), `V12-NO-TEST-IN-FEATURE` (`_is_source_file` 와 `_is_test_file` 패턴으로 분류, 소스만 변경되고 테스트 미변경), `V12-COMMIT-MSG-FORMAT` (`^(feat|fix|refactor|docs|test|chore|style|perf|ci|build|revert)(\(.+\))?!?:\s+.+`).
- **목적**: Kent Beck "atomic commit / structural ↔ behavioral 분리" 원칙. 리뷰어가 큰 PR 으로 익사하는 것 방지.

#### V14 · `complexity_guard.py` · `**/*.go/py/ts(x)`
- **검사**: `V14-HIGH-COMPLEXITY` (cyclomatic; warn 10 / error 20; Python 은 정확한 AST, Go/TS 는 if/elif/case/for/while/except/with/&&/||/ternary 휴리스틱), `V14-COGNITIVE-COMPLEXITY` (Sonar 스타일, 중첩에 가중치; warn 15 / error 30), `V14-LONG-FUNCTION` (warn 80 / error 150 라인; Python `end_lineno`, Go/TS 중괄호 카운트), `V14-DEEP-NESTING` (warn 4), `V14-TOO-MANY-PARAMS` (warn 5; Python 은 `self/cls` 제외).
- **모드**: post_tool_use = 파일 1 개; stop = `_scan_all_files()` 로 vendor/node_modules/generated 제외하고 전체.
- **목적**: 함수 가독성·테스트 용이성. 큰 함수가 굳어버리기 전에 잘라내라는 신호.

#### V15 · `dependency_guard.py` · `**/*.go/py/ts(x)`
- **검사**: `V15-WRONG-DEPENDENCY` — Clean Architecture 위반. 기본 레이어:
  - Go: `domain(0)` < `repository(1)` < `service(2)` < `handler(3)` < `cmd(4)`
  - TS: `types(0)` < `utils(1)` < `hooks(2)` < `components(3)` < `pages(4)`
  - Python: `models(0)` < `repositories(1)` < `services(2)` < `views(3)`
  - 내부 < 외부 위반 시 error. `.verifiers/layers.yaml` 로 커스텀 정의.
- 추가: `V15-CIRCULAR-IMPORT` (간단 cycle 검출), `V15-LAYER-SKIP` (한 레이어 건너뛰는 의존 경고).
- **모드**: post_tool_use = 단일 파일 import 분석 (Go 정규식, Python AST, TS 정규식 + npm 패키지 제외); stop = 프로젝트 전체.
- **목적**: 의존성은 안쪽으로만 — Uncle Bob.

#### V16 · `linter_config_guard.py` · stop mode 전용
- **검사**: 언어 자동 감지 후 (Go ↔ `.golangci.yml`, Python ↔ `ruff.toml` / `pyproject.toml [tool.ruff]`, TS ↔ `eslint.config.js` / `.eslintrc.*`) 부재 시 `V16-NO-LINTER-CONFIG`. 활성화된 설정에서 핵심 룰이 disabled 되어 있으면 `V16-MISSING-ERROR-RULES` (`errcheck` / `E722` / `no-empty`), `V16-MISSING-UNUSED-RULES` (`unused` / `F401` / `no-unused-vars`), `V16-MISSING-SECURITY-RULES` (`gosec` / Bandit `S*` / `no-eval`).
- **목적**: "린터를 켰다고 해서 안전하지 않다" — 정작 중요한 보안·에러 처리 룰이 꺼져 있는 경우 차단.

#### V18 · `mock_data_guard.py` · `**/hooks/use*Data.ts(x)`, `**/hooks/use*.ts(x)`
- **검사**: `V18-MOCK-VARIABLE` (`MOCK_*`, `mock*`, `FAKE_*`, `DUMMY_*`, `STUB_*`), `V18-MOCK-DATA` (`setState([{rank|score|username|count|value|id}:` 형태의 인라인 하드코드), `V18-FAKE-DELAY` (`new Promise(...setTimeout)` / `// Simulate network`), `V18-TODO-API` (`// TODO: Replace|Connect|Wire with actual|real API`), `V18-NO-API-IMPORT` (`use*Data.ts` 인데 `from (...api/|@connectrpc/|...gen/|...client|...service)` 일치 없음).
- **목적**: PR 들어올 때 "데모용 mock 그대로" 가 production 배포되는 사고 방지. Frontend hook 만 강제 (전체 코드베이스에서 mock 자체를 금지하는 건 아님).

### Tier 3 출력 정책 (base.py:142-193)

| 상황                                  | mode=`stop`                                     | mode=`post_tool_use`                            |
| ------------------------------------- | ----------------------------------------------- | ----------------------------------------------- |
| findings 없음                         | `{"decision":"approve"}`                        | `{}`                                            |
| warnings 만                            | `{"decision":"approve","additionalContext":...}`| `{"additionalContext":...}` (비차단)             |
| errors 있음                           | `{"decision":"block","reason":...,"additionalContext":...}` (turn 종료 차단) | `{"decision":"block","reason":...,"additionalContext":...}` (Edit/Write 차단) |

`reason` 은 짧고 행동 지향적 ("Fix these errors NOW") + 최대 N 개의 `[V##-RULE] file:line — fix` 라인. `additionalContext` 는 finding 마다 풀 스펙 + `FIX:` 가이드.

---

## 4. Hook 등록 메커니즘 — `scripts/merge_settings.py`

```
merge_settings.py
   ├── default 경로: ~/.claude/settings.json (글로벌)
   ├── --settings-path <path> 로 프로젝트 settings.json 지정 가능
   ├── MARKER = "verifiers/" (cmd 문자열에 포함되면 우리 hook 으로 식별)
   ├── 머지 절차:
   │     1) 기존 hooks.PostToolUse[Edit|Write|MultiEdit] 항목 중 MARKER 매칭 제거
   │     2) 기존 hooks.Stop 항목 중 MARKER 매칭 제거
   │     3) TIER1_HOOK 새로 추가 (security_hook.py, timeout=10)
   │     4) TIER3_HOOK 새로 추가 (stop_validator.py, timeout=120)
   └── unmerge_settings.py 가 같은 MARKER 로 자기 hook 만 제거 → 사용자 커스텀 hook 보존
```

`just install` / `just install-project <DIR>` 가 이 스크립트를 호출. 이 때 hooks 코드 자체는 복사되지 않고 `~/.claude/verifiers` 가 저장소를 가리키는 심볼릭 링크 — git pull 한 번으로 모든 설치 지점이 갱신.

---

## 5. 실행 흐름 시퀀스 (한 번의 Edit ~ turn 종료)

```
사용자 prompt
   │
   v
Claude: Edit /path/to/foo.go
   │
   ├──> [Tier 1] security_hook.py     (timeout 10s)
   │       └─ 시크릿 regex 매칭?
   │            ├─ 있음 → block + reason → Claude 가 즉시 수정
   │            └─ 없음 → {} (통과)
   │
   v
Claude: 다음 도구 호출 또는 답변 작성
   │
   v
(필요 시 사용자 또는 Claude 가 /verify 또는 verify-* skill 호출)
   │
   └──> [Tier 2] router.py / 개별 skill
           └─ should_run(file_path) 매칭 validator 실행 (mode=post_tool_use)
               └─ findings → block 또는 additionalContext
   │
   v
Claude: turn 종료 시도 (Stop 이벤트)
   │
   ├──> [Tier 3] stop_validator.py    (timeout 120s)
   │       ├─ 19 개 validator 모두 mode=stop 으로 호출
   │       ├─ FeedbackTracker.record_all → 반복 위반 검출
   │       ├─ stop_hook_active=true && block?
   │       │    └─ .verifier-block-count++  (>= 3 이면 강제 approve)
   │       └─ findings + circuit-breaker → approve 또는 block
   │
   ├─ block: turn 안 끝나고 Claude 가 수정 → 다시 Stop 이벤트 → 카운터 +1
   └─ approve: turn 종료 → 사용자에게 응답 노출
```

**우선 읽을 것**:
- Tier 1 의 timeout 10s 와 Tier 3 의 timeout 120s — 비용 분리.
- circuit breaker 가 없으면 Claude 가 수정 못하는 epsilon 에러로 무한 block.
- Tier 2 는 hook 으로 자동 발동 X — 사람/Claude 가 의식적으로 호출하는 채널.

---

## 6. 주의사항 / 미정합

조사 중 발견된 비자명 사항입니다.

1. **`hasura_graphql_enforcement.py` 는 `get_all_validators()` 에 등록되어 있지 않음** — `validators/__init__.py` 를 grep 해도 import 가 없습니다. `verify-hasura-graphql` skill 은 이 모듈을 참조하지만 Tier 3 의 자동 일괄 실행에서는 빠져 있어서 **stop hook 으로는 검사되지 않음**. `/verify` 를 명시적으로 호출하거나 skill 을 직접 부를 때만 동작. 의도된 것인지 확인 필요.

2. **V17 (UI) 미구현** — `verify-ui` skill 은 존재하지만 validator 모듈 없음. 시각 비교(Chrome DevTools + Pencil/Figma) 는 외부 MCP 호출에 의존.

3. **V15 가 두 모듈을 공유** — `dependency_guard.py` 와 `hasura_graphql_enforcement.py` 가 같은 V-ID 접두어를 사용. rule id 까지 보면 구분되지만 (V15-WRONG-DEPENDENCY vs V15-RAW-SQL-FORBIDDEN), V-ID 만으로 모듈 1:1 매핑이 깨지는 유일 케이스.

4. **`docker_prod_deploy.py`** 는 코드베이스에 없고 `__init__.py:18` 에 `# TODO: not yet implemented` 주석으로 남아있음 (V17 자리).

5. **`run_single.py`** 는 등록 흐름에 없음 — `just verify-one V<NN>` 으로 단일 validator 만 실행하는 디버그 진입점. CI 나 hook 에서는 호출되지 않음.

6. **Tier 1 의 시크릿 패턴 셋이 Tier 3 V08 의 셋보다 작음** — Tier 1 은 7 개 정규식, V08 (`security.py`) 은 더 풍부한 패턴 + CORS + PHI + .gitignore. Tier 1 은 의도적으로 가벼운 첫 줄 방어.

---

## 7. 정리 (TL;DR)

- **세 Tier 의 분업이 핵심**:
  - Tier 1 (`security_hook.py`, < 100 ms, regex only) — 시크릿 누설 즉시 차단.
  - Tier 2 (`hooks/router.py` + 20 개 skill) — Claude 또는 사용자가 상황 판단해 호출하는 상세 검증.
  - Tier 3 (`stop_validator.py`, ≤ 120 s, 19 개 validator) — turn 종료 게이트. 회로차단기와 반복 위반 트래커로 deadlock 회피.
- **register 위치**: `scripts/merge_settings.py` 가 글로벌/프로젝트 양쪽의 `settings.json` 에 Tier 1+3 만 등록. Tier 2 는 hook 자동발동 X (skill 시스템으로만).
- **검사 분류**:
  - 보안: V01 (config 시크릿), V08 (전체 시크릿/CORS/PHI/.gitignore) — Tier 1 도 같은 라인.
  - 코드 품질: V06 (Go), V07 (TS), V19 (Python), V14 (복잡도), V15 (의존 방향), V16 (린터 설정).
  - 테스트 무결성: V09/V10/V11 (언어별 테스트 러너), V13 (AI 치팅 방지).
  - 인프라/스키마: V05 (Docker), V04 (Hasura migration), V02 (genqlient), V03 (proto/Connect).
  - 운영 위생: V12 (commit), V18 (mock data), (V17 UI - 미구현).
