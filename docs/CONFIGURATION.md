# Per-project configuration

`<project>/.verifiers/config.yaml` 한 파일이 verifier 의 모든 동작을 조정합니다.
파일이 없거나 어떤 키도 명시하지 않으면 모든 항목이 기본값으로 적용되니 안전하게 시작할 수 있습니다.

> README 의 "Per-project configuration" 섹션은 이 문서의 짧은 요약입니다.
> 전체 schema, 우선순위, 디버깅 레시피는 여기 있습니다.

## 1. 풀 스키마

```yaml
# .verifiers/config.yaml — 모든 키 optional, 명시 안 한 값은 기본값 사용

thresholds:
  complexity:                       # V14 — 함수 복잡도 가드
    cyclomatic_warn: 10             # warning 임계 (기본 10)
    cyclomatic_error: 20            # error 임계 (기본 20)
    cognitive_warn: 15              # Sonar-style 인지 복잡도 (기본 15)
    cognitive_error: 30             # (기본 30)
    function_lines_warn: 80         # 함수 라인 수 warning (기본 80)
    function_lines_error: 150       # 함수 라인 수 error (기본 150)
    nesting_warn: 4                 # 중첩 깊이 (기본 4)
    params_warn: 5                  # 파라미터 개수 (기본 5)
  commit:                           # V12 — 커밋 규율
    large_diff_files: 15            # N 개 이상 변경 시 LARGE-DIFF warning (기본 15)
  test_runner:                      # V09 / V10 / V11 — 언어별 test runner 공유
    repeated_failure_count: 3       # 같은 테스트 N 회 연속 실패 시 REPEATED-FAIL (기본 3)

exclude:
  # 글로벌 — 매칭되는 파일은 ANY validator 가 검사하지 않음
  paths:
    - "vendor/**"
    - "node_modules/**"
    - "**/__generated__/**"

  # Per-validator — 매칭되는 파일은 해당 validator 만 skip, 다른 validator 는 정상 실행
  # 키는 V-ID prefix(V14) 또는 full id(V14-complexity-guard) 둘 다 허용.
  # 둘 다 적으면 둘 다 적용됩니다.
  per_validator:
    V14:                             # V14 (복잡도) 만 legacy/ 검사 제외
      - "legacy/**"
      - "scripts/**"
    V08-security:                    # 시크릿 스캔에서만 fixtures/ 제외
      - "test-fixtures/**"

validators:
  enabled: []                        # 비워두면 모든 validator 활성 (기본)
                                     # 채우면 strict allowlist — typo 시 hard-fail
  disabled:                          # 명시적 opt-out — V-ID prefix 또는 full id
    - V04                            # Hasura 안 쓰는 프로젝트는 V04 통째로 끄기
    - V20-hasura-graphql             # full-id 도 동일하게 동작

security:                            # V08 (Security validator) 튜닝
  phi_check_enabled: true            # false 로 바꾸면 PHI 검사 자체를 끔 (의료 도메인 외 권장)
  phi_fields: []                     # 비우면 기본값 사용 (patient_name, ssn, ...)
                                     # 채우면 그 리스트로 완전 교체 (덮어쓰기, 누적 X)
  required_gitignore: []             # 비우면 기본값 (.env, *.pem, *.key, .env.local, *.p12)
                                     # 채우면 사용자 리스트로 교체

docker:                              # V05 (Docker validator) 튜닝
  vhost_check_mode: "production"     # V05-VHOST-NO-NETWORK 발동 시점
                                     # "production" — prod 분류 compose 만 (기본, Phase21+)
                                     # "all"        — 모든 compose (Phase20 까지의 동작)
                                     # "off"        — 비활성화
  reverse_proxy_networks:            # VHOST 검사에서 인정되는 reverse-proxy 네트워크 이름
    - nginx-proxy                    # Traefik 사용 시 ["traefik"] 등으로 교체
                                     # 명시적 [] = "어떤 네트워크도 인정 X" (every VHOST trips)
  production_filename_patterns: []   # prod 로 분류할 compose 파일명 (fnmatch glob)
                                     # 비우면 default — 회사 컨벤션 (e.g. "*-prd.*") 으로 교체 가능
  dev_filename_patterns: []          # dev 로 분류할 compose 파일명
                                     # 비우면 default = ["*override*", "docker-compose.yaml", "docker-compose.yml"]
  production_stage_names: []         # USER 검사 대상 Dockerfile stage 이름
                                     # 비우면 default = ["prod","production","release","final","runtime",""]
  dev_stage_names: []                # V05-DEV-NO-BUILD-TARGET 가 인정하는 build.target
                                     # 비우면 default = ["dev"]

stop:                                # Tier 3 (Stop hook) 튜닝 — Phase28+
  run_pytest: "smart"                # V21-pytest 가 매 Stop 마다 pytest 돌릴지
                                     # "smart"  — 이번 turn 에 .py / pyproject.toml 변경 있을 때만 (기본)
                                     #            git diff --name-only HEAD 휴리스틱
                                     # "always" — 매 Stop 마다 무조건 (Phase27 이전 V19 동작)
                                     # "never"  — Stop 에서 pytest 안 돎 (CI 에 위임)
```

> **Phase21 BREAKING CHANGE**: 기본 `vhost_check_mode` 가 `"production"` 으로 바뀌었습니다 (이전엔 사실상 `"all"`).
> 로컬 dev 에서 false positive 가 사라지는 대신, 이전의 엄격한 동작을 원하면 명시적으로 `"all"` 지정 필요.

## 2. 각 validator 가 config 의 어떤 값을 읽는가

| Validator                          | Config 키                                                                                                                                                                                                              | 효과                                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **V14** Complexity Guard           | `thresholds.complexity.*` (cyclomatic / cognitive / function_lines / nesting_warn / params_warn)                                                                                                                      | 8 개 임계값을 모두 프로젝트별 override. 미지정 시 모듈 기본값.                                  |
| **V12** Commit Discipline          | `thresholds.commit.large_diff_files`                                                                                                                                                                                  | LARGE-DIFF warning 발동 파일 수.                                                              |
| **V09 / V10 / V11** Test Runners   | `thresholds.test_runner.repeated_failure_count`                                                                                                                                                                       | 언어별 (Go / TS / Python) 동일 키로 REPEATED-FAIL 임계 공유.                                    |
| **모든 validator** (router 단)     | `exclude.paths`                                                                                                                                                                                                       | 매칭 파일은 router 가 validator 호출 자체를 skip.                                              |
| **각 validator** (router 단)       | `exclude.per_validator[<id-or-prefix>]`                                                                                                                                                                               | 매칭 파일은 해당 validator 만 skip.                                                            |
| **모든 validator** (registry 단)   | `validators.enabled`, `validators.disabled`                                                                                                                                                                           | enabled (allowlist, 비어있으면 전체) → disabled (deny-list) 순으로 적용. 둘 다 typo 시 hard-fail. |
| **V08** Security                   | `security.phi_check_enabled`, `security.phi_fields`, `security.required_gitignore`                                                                                                                                    | PHI scanning on/off, PHI 필드 셋 교체, .gitignore 필수 항목 셋 교체.                            |
| **V05** Docker                     | `docker.vhost_check_mode`, `docker.reverse_proxy_networks`, `docker.production_filename_patterns`, `docker.dev_filename_patterns`, `docker.production_stage_names`, `docker.dev_stage_names`                          | VHOST 검사 발동 모드 (production / all / off), Traefik 등 다른 reverse proxy 인정, 회사 컨벤션 파일명/stage 이름 매핑. |
| **V21** Pytest Runner              | `stop.run_pytest`                                                                                                                                                                                                     | always / never / smart 모드. smart 는 `git diff --name-only HEAD` 로 .py 변경 감지 후만 pytest 실행. |

**아직 config 와 연결 안 된 항목** (의도적 유지):

- V08 시크릿 regex (AWS / GitHub / OpenAI 패턴) / V18 mock 변수 prefix — 보안·정책 셋이라 코드에 박혀 있음.
- V04 Hasura migration · V02 / V03 코드젠 — 검사 자체가 외부 도구 출력 파싱이라 임계 개념이 없음.

## 3. 빠른 예시

### 3.1 "Hasura 안 쓰고 legacy 폴더는 복잡도 검사 면제"

```yaml
# .verifiers/config.yaml
exclude:
  paths:
    - "vendor/**"
  per_validator:
    V14:
      - "legacy/**"

validators:
  disabled:
    - V04          # Hasura migration 검사 끄기
    - V20          # Hasura GraphQL 강제 끄기
```

이 한 파일로:

- 모든 validator 가 `vendor/**` 를 무시
- `legacy/**` 는 V14 (복잡도) 검사만 면제, 시크릿(V08) 등 다른 검사는 그대로
- V04 / V20 은 registry 에서 빠져 Tier 3 종합 검사도 안 돔

### 3.2 "의료 도메인 아닌 SaaS — PHI 검사 끄고 Traefik 사용"

```yaml
security:
  phi_check_enabled: false      # PHI scanning 끔 — V08 의 다른 검사(시크릿/CORS/.gitignore)는 유지

docker:
  reverse_proxy_networks:        # 우리는 Traefik 씀
    - traefik
```

### 3.3 "회사 컨벤션 — `*-prd.yaml` 이 prod, dist stage 가 production"

```yaml
docker:
  production_filename_patterns: ["*-prd.*"]
  production_stage_names: ["prod", "dist"]
  dev_stage_names: ["develop", "local"]
```

## 4. 동작 우선순위

router (Tier 2) 에서는 다음 순서로 필터링합니다:

```
Edit / Write / MultiEdit
  ↓
1. is_excluded(file, exclude.paths)                  → 글로벌 exclude (전체 skip)
  ↓ 통과
2. filter_enabled_validators(enabled)                 → 비어있으면 무필터 / 채워있으면 strict allowlist
                                                       (0 개 매칭 시 hard-fail VERIFIERS-CONFIG-EMPTY-ALLOWLIST)
  ↓ 통과
3. filter_disabled_validators(disabled)               → V-ID 단위 비활성 (allowlist 와 conflict 시 disabled 우선)
  ↓
4. is_excluded_for_validator(file, per_validator)     → 파일 × validator 단위 skip
  ↓
5. validator.should_run(file)                          → file_patterns 매칭 (e.g. "*.go" 만 보는 V06 등)
  ↓
6. content-hash cache (.verifiers/state/router-cache.json) → 동일 내용이면 skip
  ↓
7. 살아남은 validator 만 실제로 실행
```

stop_validator (Tier 3) 는 (1)·(2)·(3) 만 순차 적용 후 **모든 validator 를 일괄 실행** 하고, 결과 finding 들을 `_apply_exclude_filters` 가 (1) + per_validator 로 post-filter (Phase 17).

## 5. 설정이 안 먹는 것 같을 때 디버깅

```bash
# 1. config 파싱이 정상인지 확인
uv run python -c "from lib.config_loader import load_config; from pathlib import Path; print(load_config(Path('.')))"

# 2. silent except 가 삼킨 에러 확인
cat logs/_errors.jsonl | tail -10

# 3. 디버그 모드로 hook 직접 실행
VERIFIERS_DEBUG=1 just verify

# 4. 단일 validator 만 실행해 결과 비교
just verify-one V14
```

## 6. Hard-fail 케이스

다음은 Stop hook 이 silent-approve 대신 명시적으로 에러로 surface 합니다:

| Rule                                | 발생 조건                                                              | 권장 조치                                              |
| ----------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------ |
| `VERIFIERS-CONFIG-EMPTY-ALLOWLIST`  | `validators.enabled` 가 non-empty 인데 매칭 0 (typo / stale id)        | 오타 수정 또는 `enabled:` 키 통째로 제거.               |
| `V##-CRASHED`                       | validator 가 예외를 던짐                                                | `logs/_errors.jsonl` 의 traceback 확인.                 |
| `V##-TIMEOUT`                       | validator 가 30 s per-validator 예산 초과                                | `validators.disabled` 로 끄거나, validator 자체 최적화. |

## 7. 스키마 정의 위치

- 모든 dataclass: `lib/config_loader.py`
- 매칭 로직: `lib/exclusion.py`
- Content-hash 캐시: `lib/router_cache.py`
- 병렬 실행 + sentinel finding: `lib/parallel_runner.py`
