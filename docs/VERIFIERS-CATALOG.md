# Verifiers — Hook · Validator 종합 카탈로그

> 본 문서는 verifiers 의 **세 단계(Tier 1/2/3) hook 시스템** 과 **49 개 등록 validator (V01~V58, V17/V24/V55 미사용) + 20+ Tier 2 skill** 의 동작·목적을 한 곳에 정리합니다. 모든 단정 (validator id, file pattern, 검사 내용, 모드 차이) 은 `hooks/`, `skills/` 소스에서 직접 추출한 것입니다.

---

## 0. 핵심 아키텍처 (한 눈에)

| Tier | 발동 이벤트 | 진입점 | matcher / 시점 | 핵심 역할 |
| :--: | ----------- | ------ | -------------- | --------- |
| **1** | `PostToolUse` | `hooks/security_hook.py` | `Edit \| Write \| MultiEdit` · timeout 10s · <100ms | regex 기반 시크릿(V08) 즉시 차단 — 가장 가벼운 첫 줄 방어 |
| **2** | (hook 미등록) | `hooks/router.py` + `skills/verify-*` (20개) | Claude 가 자율 판단해 skill 호출 / 사용자가 `/verify` 실행 / `just verify-one V##` | file_path 매칭 validator 만 골라 실행 — 상황별 비용 통제 |
| **3** | `Stop` | `hooks/stop_validator.py` | turn 종료 시도 시 단일 발동 · timeout 120s | 등록된 49개 validator (V01~V58, V17/V24/V55 미사용) 일괄 실행 · Phase 63 PASS-state 캐시 · circuit breaker x3 · FeedbackTracker |

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
         · get_all_validators() 49개 일괄 실행 (mode="stop", Phase 62 lru_cache)
         · Phase 63 PASS-state 캐시 lookup → 입력 변경 없는 cacheable validator skip
         · ThreadPoolExecutor(max_workers=min(8, len(validators))) 로 병렬 실행 (Phase 36)
         · errors 있음 → block + reason → Claude 다시 수정
         · zero-finding 인 cacheable validator → record_pass (5-min TTL)
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
| 1  | `verify`               | 전체 검증 활성화 — 49 개 active validator 중 file_path 매칭만 실행 | router.py 통해 file_path 매칭하는 모든 validator 실행                                                          | V01~V58 (V17/V24/V55 미사용) |
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
| 동작                | `get_all_validators()` 가 등록한 49 개 validator 의 `run(ctx, file_path=None, mode="stop")` 호출 (Phase 32 에서 `validate()` retire). Phase 62 lru_cache 로 49 개 모듈 import 가 한 번만 발생. |
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

### 등록된 validator 상세 (`hooks/validators/__init__.py:get_all_validators()` 순서)

> 형식: **V-ID** · 모듈 · file_patterns → 검사 항목 · stop vs post_tool_use 차이 · 목적
>
> **범위 안내**: 아래 상세 카탈로그는 Phase 22 (V01~V21) 시점에 작성된 핵심 validator 들입니다. Phase 53–58 에서 추가된 V22~V58 (28 개 추가) 의 자세한 동작은 다음 소스를 직접 참조하세요:
>
> - **목록 + 그룹 분류**: `lib/config_loader.py` 의 `BUILTIN_GROUPS` (7 카테고리) + 본 문서 §10 TL;DR.
> - **각 validator 동작**: `hooks/validators/<module>.py` 의 모듈 docstring + `validate_*` 메서드 docstring.
> - **추가된 phase 의 의도**: `CHANGELOG.md` 의 Phase 53/54/55/56/57/58 항목 (각 V-ID 의 ship-blocker 근거 + 검사 룰 요약).
> - **테스트로 본 행동 명세**: `tests/test_<module>.py` (각 V-ID 마다 30+ 단위 테스트로 동작 박제).
>
> Phase 60–63 의 변경은 validator 신설이 아니라 인프라 개선 (workflow_loader 추출, parallel_runner 캐시 + timeout 오버라이드, tier_cache PASS-state) — §8 참조.

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
- **모드**: post_tool_use 만 (stop 은 V21 이 전체 `pytest` 수행).
- **목적**: TDD 사이클의 빨간/초록 즉시 피드백.

#### V19 · `py_quality.py` · `**/*.py`, `**/pyproject.toml`, `**/ruff.toml`
- **검사**: post_tool_use — `V19-RUFF-CHECK` (단일 파일 `ruff check --output-format text --no-fix`, `(.+?):(\d+):(\d+): (\S+) (.+)` → `V19-RUFF-{CODE}` rule id 보존), `V19-RUFF-FORMAT` (`ruff format --check`). stop — `V19-RUFF-ALL` (프로젝트 전체 `ruff check .`, 최대 20 개 finding 후 요약).
- **목적**: ruff lint/format. Phase28 에서 pytest 분리되어 V21 로 이동 — parallel runner 가 lint 와 test 실행을 독립 단위로 본다.

#### V20 · `hasura_graphql_enforcement.py` · `**/*.go`, `**/hasura/**`
- **검사**: Hasura 가 프로젝트에 감지될 때만 동작 (감지 안 되면 `_detect_hasura()` early-exit, 비용 0).
  - `V20-RAW-SQL-FORBIDDEN` — Go 파일에서 `db.Query(...)`, `.QueryRow(...)`, `.ExecContext(...)`, `.PrepareContext(...)`, 또는 `SELECT/INSERT/UPDATE/DELETE` 리터럴 검출. raw SQL 대신 GraphQL 사용 강제.
  - `V20-SQL-IMPORT` — `database/sql` 또는 `github.com/jmoiron/sqlx` 등 raw-SQL 라이브러리 import 검출.
  - `V20-MISSING-GRAPHQL` — Service struct 가 GraphQL client 필드 (gqlClient / graphqlClient / hasura* 등) 없이 정의됐을 때 경고.
- **모드**: post_tool_use (편집된 단일 Go 파일) + stop (프로젝트 전체 Go 파일).
- **목적**: Hasura 도입 후에도 코드가 raw SQL 로 우회하면 schema/permission 보호가 무력화됨. Phase3 에서 V15→V20 prefix 재할당으로 dependency_guard 와 모듈 1:1 매핑 회복. **Hasura 없는 프로젝트에는 영향 0** — 비용도 false positive 도 없음.

#### V21 · `py_pytest.py` · `**/*.py`, `**/pyproject.toml` · stop 전용
- **검사**: stop 모드만 동작. `V21-TEST-FAIL` (`pytest -x -q --tb=no`, `(\d+) failed`, `FAILED\s+(\S+)`, 최대 5 개 테스트 이름 포함).
- **모드 게이팅**: `stop.run_pytest` config 키 (Phase28+) — `"smart"` (기본; `git diff --name-only HEAD` 결과에 `.py` 또는 `pyproject.toml` 있을 때만), `"always"` (legacy 동작), `"never"` (CI 에 위임). git 이 없거나 실패하면 fail-open (pytest 돎).
- **목적**: V19 와 분리해서 ruff (수백 ms) 와 pytest (수 초) 를 독립 워커 슬롯으로 처리. smart 모드는 매크다운/yaml-only turn 에서 pytest 비용 0.

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
   │       ├─ Phase 63 tier_cache.lookup_recent_pass → 입력 변경 없으면 skip
   │       ├─ 49 개 (cacheable 미스 + ineligible) validator 를 ThreadPoolExecutor(8w) 로 병렬 호출 (mode=stop)
   │       ├─ Phase 63 record_pass — zero-finding cacheable validator 만 5-min TTL 로 박제
   │       ├─ FeedbackTracker.record_all → 반복 위반 검출
   │       ├─ stop_hook_active=true && block?
   │       │    └─ .verifiers/state/verifier-block-count++  (>= 3 이면 강제 approve)
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

## 6. 주의사항 / 구현 이력

본 카탈로그는 Phase 1 ~ Phase 22 까지 누적된 변경을 반영합니다. 초기 조사에서 flagged 된 후 나중에 해결된 항목도 이력으로 남겨둡니다.

### 6.1 해결됨 (resolved in later phases)

1. ~~**`hasura_graphql_enforcement.py` 미등록**~~ → **Phase 3 에서 해결**: `__init__.py` 에 등록 + V15→V20 prefix 재할당. Hasura 미감지 프로젝트는 early-exit 으로 비용 0.

2. ~~**V15 가 두 모듈을 공유**~~ → **Phase 3 에서 해결**: hasura_graphql_enforcement 의 모든 rule prefix 가 V20-* 로 이동. V-ID ↔ 모듈 1:1 매핑이 `_assert_registry_invariants` (Phase 3) 로 import 시점에 강제됨.

3. ~~**`hasura_graphql_enforcement.py` 가 깨진 코드 (실제로 한 번도 작동 안함)**~~ → **Phase 3 에서 재구현**: `Finding(code=…, file_path=…, line_number=…, details=…)` 같은 존재하지 않는 필드 사용 + `ctx.changed_files` 같은 없는 속성 참조를 모두 정리.

### 6.2 의도적 미해결 (intentional gaps)

4. **V17 (UI) 미구현** — `verify-ui` skill 은 외부 MCP 호출 (Chrome DevTools, Pencil/Figma) 에 의존하기 때문에 Python validator 로 적합하지 않음. `__init__.py` 의 `# TODO: not yet implemented` 주석은 의도적.

5. **`run_single.py` 는 hook 흐름에 없음** — `just verify-one V<NN>` 의 디버그 진입점이며 CI 나 자동 hook 에서는 호출되지 않음. NAME_MAP 은 `__init__.py:get_all_validators()` 와 lockstep 으로 관리.

6. **Tier 1 의 시크릿 패턴 셋이 Tier 3 V08 의 셋보다 작음** — Tier 1 (security_hook.py) 은 7 개 정규식, V08 (security.py) 은 더 풍부한 패턴 + CORS + PHI + .gitignore. Tier 1 은 의도적으로 가벼운 첫 줄 방어.

---

## 7. Configuration system (Phase 6, 7, 15, 17, 19, 21)

`<project>/.verifiers/config.yaml` 가 21+ 개 knob 으로 검증 동작을 조정합니다. 자세한 schema 는 [`/docs/CONFIGURATION.md`](CONFIGURATION.md) — 여기서는 카탈로그 관점만 정리.

### 7.1 Knob 카테고리

| 카테고리                     | 영향 받는 validator              | Phase | 핵심 키 |
| ---------------------------- | -------------------------------- | ----- | ------- |
| `thresholds.complexity.*`    | V14                              | 7     | cyclomatic / cognitive / function_lines / nesting / params |
| `thresholds.commit.*`        | V12                              | 11    | large_diff_files |
| `thresholds.test_runner.*`   | V09 / V10 / V11                  | 11    | repeated_failure_count |
| `exclude.paths` (글로벌)      | 모든 validator (router post-filter) | 7     | gitignore-style globs |
| `exclude.per_validator`      | 특정 validator 만               | 15    | `{V-id-or-prefix: [globs]}` |
| `validators.enabled`         | 모든 validator                   | 16    | strict allowlist (typo 시 hard-fail, Phase 22) |
| `validators.disabled`        | 모든 validator                   | 7     | deny-list (allowlist 와 함께면 disabled 우선) |
| `security.*`                 | V08                              | 19    | phi_check_enabled / phi_fields / required_gitignore |
| `docker.*`                   | V05                              | 21    | vhost_check_mode (BREAKING) / reverse_proxy_networks / filename / stage names |

### 7.2 적용 우선순위 (router 단)

```
Edit/Write file
  ↓
1. global exclude.paths        — 매칭 → router 자체 종료
  ↓ (통과)
2. validators.enabled         — non-empty + 0 매칭 → CONFIG-EMPTY-ALLOWLIST 에러
  ↓ (통과)
3. validators.disabled        — V-ID 매칭 validator 제거
  ↓
4. exclude.per_validator      — 파일×validator 단위 skip
  ↓
5. validator.should_run(file) — file_patterns 매칭 안 하는 validator 제거
  ↓
6. content-hash cache         — 직전과 동일 내용 → router 종료
  ↓
7. 살아남은 validator 들 실제 실행
```

stop_validator (Tier 3) 는 (1)·(2)·(3) 만 순차 적용 후 모든 validator 를 일괄 실행하고, 결과 finding 들을 `_apply_exclude_filters` 가 `(1) + per_validator` 로 post-filter (Phase 17).

---

## 8. Tier 3 parallelism + 다층 캐시 + sentinel findings (Phase 12 / 36 / 61 / 62 / 63)

`hooks/stop_validator.py` 는 49 개 validator 를 `lib/parallel_runner.run_all` 을 통해 **`ThreadPoolExecutor(max_workers=min(8, len(validators)))`** 로 병렬 실행합니다.

**Phase 36 — ProcessPoolExecutor → ThreadPoolExecutor**: 모든 heavy validator 가 `subprocess.run` 으로 child process 를 띄우고 (golangci-lint, ruff, eslint, tsc, pytest 등) 그동안 GIL 을 놓기 때문에, thread 가 process 와 동등한 병렬화를 제공합니다. spawn cost (~200 ms / Stop) + ProjectContext pickling 비용을 제거. `pickle.PicklingError` fallback 분기도 함께 retire.

**Phase 36 — adaptive workers**: `max_workers=min(DEFAULT_MAX_WORKERS=8, len(validators))`. 활성 validator 가 5 개 뿐인 프로젝트에서 8 개 thread 를 spawn 하지 않음.

### 8.1 캐시 stack (입력 변경 없으면 일을 안 함)

세 단계의 캐시가 stack 으로 쌓여 매 Stop 의 비용을 점진적으로 절감합니다:

| Phase | 위치 | 키 | TTL | Storage |
| ----- | ---- | -- | --- | ------- |
| **63** Tier 3 PASS-state 캐시 | `lib/tier_cache.py` | `validator.file_patterns` 매칭 파일들의 `sha256(path:size:mtime_ns)` | 5 분 (configurable) | `<cwd>/.verifiers/state/tier-cache/V##.json` |
| **61** Subprocess 결과 캐시 | `lib/subprocess_cache.py` | sha256 (input files + tool version + cmd args + config files) | 7 일 FIFO (max 32 entries) | `<cwd>/.verifiers/state/subprocess-cache/<label>.json` |
| **61** V07 native 캐시 | `eslint --cache`, `tsc --incremental` | tool 자체 관리 (eslint: file-level content hash, tsc: tsbuildinfo) | tool 자체 관리 + lockfile gate | `<cwd>/.verifiers/cache/eslint/`, `<cwd>/.verifiers/cache/tsc.tsbuildinfo` |

**중요**: PASS-state 캐시는 **zero-finding** 일 때만 기록. finding 이 있으면 다음 Stop 에서 다시 surface 되어 사용자가 고치기 전까진 캐시되지 않음. Sentinel finding (`V##-CRASHED`, `V##-TIMEOUT`) 도 캐시 제외.

**hard-exclusion 목록** (`TIER_CACHE_INELIGIBLE` in `lib/tier_cache.py`): V06/V09/V10/V11/V12/V21/V37 — test runner + git-state-dependent. 파일 입력만으로 결과가 결정되지 않으므로 영구 제외.

### 8.2 Phase 62 — per-validator timeout 오버라이드

`.verifiers/config.yaml` 의 `timeouts.per_validator[V##]` 로 default 30 s 를 덮어쓸 수 있습니다. `lib/parallel_runner._resolve_timeout` 이 ctx.config.timeouts 에서 V## prefix 매핑을 조회하고 min-1s 로 클램프.

```yaml
timeouts:
  default: 30
  per_validator:
    V21: 180   # pytest 는 3 분까지
    V19: 5     # ruff 는 hang 의심 시 즉시 cut
    V06: 240   # go-quality (Stage 2 = golangci + go test 병렬) 4 분
```

### 8.3 안전망 — Sentinel findings

| Rule                           | 발생 조건                                            | severity  |
| ------------------------------ | ---------------------------------------------------- | --------- |
| `V##-CRASHED`                  | validator 가 던진 예외                                | warning   |
| `V##-TIMEOUT`                  | per-validator timeout (config 또는 default 30 s) 초과 | warning   |
| `VERIFIERS-CONFIG-EMPTY-ALLOWLIST` | `validators.enabled` 가 0 개 매칭 (typo, Phase 22) | error     |

핵심 invariant: **silent false-approve 금지**. 어떤 형태의 실패든 finding 으로 surface 되어 사용자가 "검사가 통과했다" 라고 착각할 수 없게 함. Phase 36 에서 sentinel 에 `kind="sentinel"` 마킹 → `_apply_exclude_filters` 가 sentinel 은 절대 silence 하지 않음 (`exclude.paths: ["**"]` 같은 설정으로도 우회 불가).

### 8.4 Opt-out

| Env var                       | 효과                                                   |
| ----------------------------- | ------------------------------------------------------ |
| `VERIFIERS_PARALLEL=0`        | 병렬 실행 비활성, sequential fallback                   |
| `VERIFIERS_NO_TIER_CACHE=1`   | Phase 63 PASS-state 캐시 우회 — 모든 validator 매번 실행 |
| `VERIFIERS_NO_CACHE=1`        | V07 eslint/tsc + V03 buf subprocess 캐시 우회          |
| `VERIFIERS_DEBUG=1`           | hook 디버그 로그 활성                                   |

ThreadPoolExecutor 로 전환된 후 자동 fallback (pickle / pool-setup 에러) 분기는 retire — thread 는 spawn 도 pickle 도 없음. validator 안에서 던진 예외는 `_run_one_validator` 의 inner sentinel 이 받아서 `V##-CRASHED` 로 변환.

---

## 9. Tier 2 auto-gateway + content cache (Phase 13)

이전 카탈로그가 작성된 시점엔 **Tier 2 router 가 hook 자동등록 안 됨** 이라고 적혀 있었습니다 (`/verify` 명령으로만 동작). **Phase 13 에서 변경됨**:

- `scripts/merge_settings.py` 가 PostToolUse 에 **3 개 hook 등록**:
  1. Tier 1: `security_hook.py` (< 100 ms, 시크릿 즉시 차단)
  2. **Tier 2: `router.py`** (≤ 60 s, 상황별 validator 자동 디스패치) ← Phase 13 추가
  3. Tier 3 hook: `Stop` 이벤트 시 `stop_validator.py` (≤ 120 s)

### 9.1 Tier 2 의 두 prefilter

매 Edit/Write 마다 router 가 전체 발동되면 비싸므로:

1. **확장자 prefilter**: 어떤 active validator 도 `should_run(file)` True 가 안 나오면 즉시 종료 (`.md` / lockfile / 무관한 yaml 등).
2. **Content-hash 캐시**: 파일의 sha256 이 직전 router run 과 같으면 skip. `<cwd>/.verifiers/state/router-cache.json` (1000 entry FIFO).

이 두 가드 덕분에 PostToolUse 매번 발동의 cost 가 의미있게 낮아짐.

---

## 10. 정리 (TL;DR — Phase 63 기준)

- **세 Tier 분업**:
  - **Tier 1** (`security_hook.py`, < 100 ms, regex only) — 시크릿 누설 즉시 차단. 항상 자동.
  - **Tier 2** (`router.py` + 20+ 개 skill, ≤ 60 s, content-hash 캐시 + 확장자 prefilter) — 상황별 validator 자동. **Phase 13 부터 hook 자동등록**.
  - **Tier 3** (`stop_validator.py`, ≤ 120 s, 49 개 validator, ThreadPoolExecutor max_workers=8 + Phase 63 PASS-state 캐시) — turn 종료 게이트. circuit breaker (3 회 연속 block 시 통과) + FeedbackTracker + sentinel findings.
- **3 hook 등록 위치**: `scripts/merge_settings.py` 가 글로벌/프로젝트 양쪽의 `settings.json` 에 Tier 1+2+3 모두 등록.
- **검사 분류** (49 active validators, 7 BUILTIN_GROUPS — `disabled_groups` 로 그룹 단위 disable):
  - **code-quality** (9): V06 Go, V07 TS, V14 복잡도, V19 Python ruff, V34 Go err 래핑, V35 ctx 전파, V36 HTTP 하드닝, V38 golangci 엄격, V39 컨텍스트 로거
  - **test-execution** (5): V09 Go test, V10 TS test, V11 Python test, V21 pytest, V37 race + coverage
  - **env-config** (2): V01 env 시크릿, V22 multi-env 일관성
  - **docker** (6): V05, V25, V26, V44, V45, V58
  - **api-rpc-data** (12): V02, V03, V04, V20, V23, V27, V46, V47, V48, V49, V50, V56
  - **security** (7): V08, V18, V40, V41, V42, V43, V57
  - **process** (8): V12, V13, V15, V16, V51, V52, V53, V54
  - 미사용 V-ID: V17 (UI 미구현), V24 (결번), V55 (사용자 결정으로 컷)
- **다층 캐시 (Phase 61–63)**:
  - **Phase 63** Tier 3 PASS-state 캐시 (`lib/tier_cache.py`) — 입력 변경 없으면 validator skip (5-min TTL). V06/V09/V10/V11/V12/V21/V37 영구 제외.
  - **Phase 61** Subprocess 결과 캐시 (`lib/subprocess_cache.py`) — V03 buf-lint 등 (7-day FIFO).
  - **Phase 61** Native 캐시 — V07 eslint `--cache` + tsc `--incremental` (TS 5.0+).
  - Escape: `VERIFIERS_NO_TIER_CACHE=1`, `VERIFIERS_NO_CACHE=1`.
- **Phase 62 4-pack 최적화**: adaptive workers + per-validator timeouts (`timeouts.per_validator`) + pre-compiled `file_patterns` (`@functools.lru_cache(maxsize=128)`) + lazy validator import cache (`get_all_validators` lru_cache).
- **Configuration**: 25+ knob via `.verifiers/config.yaml` (thresholds / exclude / validators / security / docker / stop / timeouts / tier_cache / groups). Hard-fail on `validators.enabled` typo. 자세히는 [`/docs/CONFIGURATION.md`](CONFIGURATION.md).
- **테스트 안전망**: 1,482 tests, Tier 3 dogfood CI, PEP 723 inline-deps drift gate, classical-school test 강제 (CONTRIBUTING).
