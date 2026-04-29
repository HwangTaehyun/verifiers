# verifiers

> AI 에이전트 코딩 워크플로우를 위한 재사용 가능한 검증 시스템 (Claude Code hooks + skills + agents)

`verifiers`는 Claude Code 가 생성한 코드를 **세 단계(Tier 1/2/3)** 로 검증하는 모듈입니다.
보안 위반은 즉시 차단하고, 상황별 품질 점검은 skill 로 호출하며, 턴 종료 시점에는 19개의 등록 validator (V01~V20, V17 UI 미구현) 가 일괄 실행됩니다. 현재 **782 개의 pytest** 가 검증 로직을 보호합니다. 각 validator·hook 의 상세 동작은 [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md) 를 참조하세요.

| Tier | 진입점 | 트리거 / 시점 | 역할 |
| :--: | ------ | ------------- | ---- |
| **1** | `security_hook.py` | PostToolUse · `Edit \| Write \| MultiEdit` · <100ms | regex 기반 보안 즉시 차단 |
| **2** | `skills/verify-*` (20개) | Claude/사용자가 명시적 호출 (자동 hook 미등록) | 상황별 검증 — `verify-go`, `verify-ts`, `verify-docker`, ... |
| **3** | `stop_validator.py` | Stop · ≤120s | V01~V20 등록 validator 일괄 실행 (+ circuit breaker · FeedbackTracker) |

## Requirements

- macOS / Linux
- [Claude Code](https://docs.claude.com/claude-code) 설치
- [`uv`](https://docs.astral.sh/uv/) (Python 의존성 격리 실행)
- [`just`](https://github.com/casey/just) (설치 레시피 실행)
- Python ≥ 3.11

## Installation

verifiers 를 클론한 뒤, **글로벌** 또는 **프로젝트별** 중 원하는 모드로 설치하세요. 두 모드는 공존 가능합니다.

```bash
git clone https://github.com/HwangTaehyun/verifiers.git
cd verifiers
just setup            # uv 가 의존성 설치 (.venv 생성)
```

> 이 README 의 모든 예시에서 `<VERIFIERS_REPO>` 는 위에서 클론한 `verifiers` 저장소의 절대 경로를 의미합니다 (예: `~/code/verifiers`). `<PROJECT_DIR>` 는 verifier 를 적용할 다른 프로젝트의 절대 경로입니다.

### 1) 글로벌 설치 — 모든 프로젝트에 적용

`~/.claude/` 아래에 hooks · skills · agents · commands 를 심볼릭 링크로 설치하고, `~/.claude/settings.json` 에 Tier 1/3 hook 을 등록합니다.

```bash
cd <VERIFIERS_REPO>
just install
```

설치되는 항목:

| 항목                    | 위치                                              |
| ----------------------- | ------------------------------------------------- |
| 베이스 심볼릭 링크      | `~/.claude/verifiers`                             |
| Tier 2 skills (20개)    | `~/.claude/skills/verify*`                        |
| Agents (5개)            | `~/.claude/agents/{stack-verifier,ui-verifier,tdd-writer}.md`, `~/.claude/agents/team/{builder,validator}.md` |
| Slash commands (5개)    | `~/.claude/commands/{verify,build-with-validation,tdd,tdd-write,tdd-update}.md` |
| Tier 1 + Tier 3 hooks   | `~/.claude/settings.json` 에 머지                 |

삭제:
```bash
just uninstall
```
> `merge_settings.py` 는 `verifiers/` 문자열로 식별되는 hook 만 제거하므로 사용자 커스텀 hook 은 보존됩니다.

### 2) 프로젝트별 설치 — 특정 프로젝트에만 적용

```bash
cd <VERIFIERS_REPO>
just install-project <PROJECT_DIR>
```

`<PROJECT_DIR>/.claude/` 아래에 동일한 구조로 설치되고, 그 프로젝트의 `settings.json` 에만 hook 이 등록됩니다.

삭제:
```bash
just uninstall-project <PROJECT_DIR>
```

### 3) 설치 검증

```bash
ls -l ~/.claude/skills | grep verify             # 20개 verify-* 심볼릭 링크 확인
grep -i verifiers ~/.claude/settings.json        # hook 등록 확인
```

설치 후 **Claude Code 를 재시작**하면 hook 이 활성화됩니다.

## Usage

### Claude Code 안에서 자동 실행

| 시점                    | 무엇이 실행되나                                                |
| ----------------------- | -------------------------------------------------------------- |
| Edit / Write / MultiEdit 직후 | Tier 1 `security_hook.py` 가 보안 위반 패턴을 즉시 차단         |
| Claude 가 turn 을 끝낼 때     | Tier 3 `stop_validator.py` 가 V01~V20 전체를 종합 실행          |
| Claude 가 적합하다고 판단할 때 | Tier 2 `skills/verify-*` 를 상황에 맞게 호출 (예: TS 변경 시 `verify-ts`) |

### 수동 실행 (CLI)

```bash
cd <PROJECT_DIR>
just --justfile <VERIFIERS_REPO>/justfile verify          # V01~V20 전체
just --justfile <VERIFIERS_REPO>/justfile verify-one V03  # 특정 validator 만
```

설치 없이 hook 을 직접 호출:

```bash
echo '{"cwd": "'"$(pwd)"'"}' | \
  uv run --script <VERIFIERS_REPO>/hooks/stop_validator.py
```

### Slash commands (글로벌 설치 후)

| 명령                          | 용도                                                 |
| ----------------------------- | ---------------------------------------------------- |
| `/verify`                     | 현재 프로젝트에 V01~V20 종합 검증 즉시 실행          |
| `/build-with-validation`      | Builder ↔ Validator 패턴으로 구현/검증 분리 실행      |
| `/tdd`, `/tdd-write`, `/tdd-update` | TDD Red 단계 (테스트 먼저 작성) 워크플로우      |

### Agents

| Agent             | 역할                                            |
| ----------------- | ----------------------------------------------- |
| `stack-verifier`  | 스택별 (Go/TS/Python/...) 종합 검증 실행         |
| `ui-verifier`     | UI/UX 변경 시각 검증                            |
| `tdd-writer`      | 명세 → pytest 테스트 코드 자동 생성              |
| `team/builder`    | 구현 담당 (모든 도구 접근)                      |
| `team/validator`  | 검증 담당 (read-only, 코드 수정 불가)           |

## Validators

`hooks/validators/` 에 위치한 21개 모듈 중 19개가 `validators/__init__.py:get_all_validators()` 에 등록되어 V01~V20 검증을 수행합니다 (V17 UI 는 미구현, `hasura_graphql_enforcement.py` 는 skill 전용 — 자세한 사항은 카탈로그 §6 참조):

- 보안: `security.py` (V08), `dependency_guard.py` (V15), `linter_config_guard.py` (V16)
- 품질: `complexity_guard.py` (V14), `mock_data_guard.py` (V18), `ai_cheating_guard.py` (V13), `commit_discipline.py` (V12)
- Python: `py_quality.py` (V19), `py_test_runner.py` (V11)
- TypeScript: `ts_quality.py` (V07), `ts_test_runner.py` (V10)
- Go: `go_quality.py` (V06), `go_test_runner.py` (V09)
- 인프라: `docker_compose.py` (V05), `env_config.py` (V01)
- API/스키마: `graphql_gen.py` (V02), `proto_connect.py` (V03), `hasura_migration.py` (V04), `hasura_graphql_enforcement.py` (skill 전용)

각 validator 는 `tests/test_*.py` 에 1:1 대응하는 단위 테스트를 갖습니다 (총 782 tests).

> 📖 **상세 카탈로그**: 각 validator 가 어느 hook 에서 무엇을 검사하고 왜 필요한지 — file pattern, 정규식, 외부 명령, post_tool_use ↔ stop 모드 차이까지 포함한 풀 스펙은 [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md) 를 참조하세요. 20개 Tier 2 skill 의 V-ID 매핑 표와 실행 흐름 시퀀스 다이어그램도 함께 수록되어 있습니다.

## Development

```bash
cd <VERIFIERS_REPO>
just test               # pytest 전체 실행
just lint               # ruff check
just format             # ruff format
just logs               # logs/*.jsonl tail
just clean-logs         # 로그 + 캐시 초기화
```

추가 레시피는 `just --list` 또는 `justfile` 을 참조하세요.

## Architecture Notes

- **심볼릭 링크 우선**: 설치 시 코드를 복사하지 않고 링크합니다. `git pull` 한 번으로 모든 설치 지점이 갱신됩니다.
- **Marker 기반 안전 제거**: `unmerge_settings.py` 가 `verifiers/` 문자열 marker 로 자기 hook 만 식별 제거 → 사용자 커스텀 hook 보호.
- **Circuit breaker**: `stop_validator.py` 는 `<cwd>/.verifiers/state/verifier-block-count` 에 연속 차단 횟수를 기록하고, 3 회 연속 차단되면 통과시켜 무한 루프를 방지합니다.
- **Tier 분리**: 빠른 보안 차단 (Tier 1, <100ms) / 상황별 호출 (Tier 2) / 무거운 종합 검증 (Tier 3, ≤120s) 으로 비용·블로킹 정책을 분리.

## Per-project configuration

`<project>/.verifiers/config.yaml` 한 파일이 verifier 의 모든 동작을 조정합니다. 파일이 없으면 모든 키가 기본값으로 적용되니 안전하게 시작할 수 있습니다.

### 풀 스키마

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
  disabled:                          # 명시적 opt-out — V-ID prefix 또는 full id
    - V04                            # Hasura 안 쓰는 프로젝트는 V04 통째로 끄기
    - V20-hasura-graphql             # full-id 도 동일하게 동작
```

### 각 validator 가 config 의 어떤 값을 읽는가

| Validator                       | Config 키                                                | 효과                                                 |
| ------------------------------- | ------------------------------------------------------- | ---------------------------------------------------- |
| **V14** Complexity Guard        | `thresholds.complexity.*` (cyclomatic / cognitive / function_lines / nesting_warn / params_warn) | 8개 임계값을 모두 프로젝트별 override. 미지정 시 모듈 기본값. |
| **V12** Commit Discipline       | `thresholds.commit.large_diff_files`                    | LARGE-DIFF warning 발동 파일 수.                      |
| **V09 / V10 / V11** Test Runners | `thresholds.test_runner.repeated_failure_count`         | 언어별 (Go / TS / Python) 동일 키로 REPEATED-FAIL 임계 공유. |
| **모든 validator** (router 단)  | `exclude.paths`                                          | 매칭 파일은 router 가 validator 호출 자체를 skip.       |
| **각 validator** (router 단)    | `exclude.per_validator[<id-or-prefix>]`                 | 매칭 파일은 해당 validator 만 skip.                     |
| **모든 validator** (registry 단) | `validators.disabled`                                    | 매칭 V-ID 의 validator 가 registry 에서 제외됨 — Tier 2/3 모두 적용. |

**아직 config 와 연결 안 된 항목** (의도적 유지):
- V08 시크릿 regex / V08 PHI 필드 셋 / V18 mock 변수 prefix — 보안·정책 셋이라 코드에 박혀있음.
- V05 Docker · V04 Hasura · V02/V03 코드젠 — 검사 자체가 외부 도구 출력 파싱이라 임계 개념이 없음.

### 빠른 예시 — "Hasura 안 쓰고 legacy 폴더는 복잡도 검사 면제"

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

### 동작 우선순위

router (Tier 2) 에서는 다음 순서로 필터링:

```
1. is_excluded(file, exclude.paths)              → 글로벌 exclude (전체 skip)
2. filter_disabled_validators(disabled)           → V-ID 단위 비활성
3. is_excluded_for_validator(file, per_validator) → 파일×validator 단위 skip
4. validator.should_run(file)                     → file_patterns 매칭
5. content-hash cache (.verifiers/state/router-cache.json) → 동일 내용 skip
6. 살아남은 validator 만 실제로 실행
```

stop_validator (Tier 3) 는 (1)·(2) 만 적용 — 프로젝트 전체 스캔이라 per-file 결정이 의미 없습니다.

### 설정이 안 먹는 것 같을 때 디버깅

```bash
# 1. config 파싱이 정상인지 확인
uv run python -c "from lib.config_loader import load_config; from pathlib import Path; print(load_config(Path('.')))"

# 2. silent except 가 삼킨 에러 확인
cat logs/_errors.jsonl | tail -10

# 3. 디버그 모드로 hook 직접 실행
VERIFIERS_DEBUG=1 just verify
```

스키마 전체 정의는 `lib/config_loader.py` 의 dataclass, 매칭 로직은 `lib/exclusion.py`, 캐시는 `lib/router_cache.py` 참조.

## Contributing & Changelog

- 새 validator 를 추가하거나 기존 룰을 바꾸려면: [CONTRIBUTING.md](CONTRIBUTING.md) 의 V-ID 할당 / mode dispatch 보일러플레이트 / 테스트 컨벤션 / PR 체크리스트 참고.
- 모든 의미 있는 변경은 [CHANGELOG.md](CHANGELOG.md) 에 기록됩니다 (Keep a Changelog 형식).

## License

MIT — see [LICENSE](LICENSE).
