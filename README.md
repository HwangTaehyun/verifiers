# verifiers

> AI 에이전트 코딩 워크플로우를 위한 재사용 가능한 검증 시스템 (Claude Code hooks + skills + agents)

`verifiers`는 Claude Code 가 생성한 코드를 **세 단계(Tier 1/2/3)** 로 검증하는 모듈입니다.
보안 위반은 즉시 차단하고, 상황별 품질 점검은 skill 로 호출하며, 턴 종료 시점에는 19개의 등록 validator (V01~V19, V17 UI 미구현) 가 일괄 실행됩니다. 현재 **782 개의 pytest** 가 검증 로직을 보호합니다. 각 validator·hook 의 상세 동작은 [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md) 를 참조하세요.

| Tier | 진입점 | 트리거 / 시점 | 역할 |
| :--: | ------ | ------------- | ---- |
| **1** | `security_hook.py` | PostToolUse · `Edit \| Write \| MultiEdit` · <100ms | regex 기반 보안 즉시 차단 |
| **2** | `skills/verify-*` (20개) | Claude/사용자가 명시적 호출 (자동 hook 미등록) | 상황별 검증 — `verify-go`, `verify-ts`, `verify-docker`, ... |
| **3** | `stop_validator.py` | Stop · ≤120s | V01~V19 등록 validator 일괄 실행 (+ circuit breaker · FeedbackTracker) |

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
| Claude 가 turn 을 끝낼 때     | Tier 3 `stop_validator.py` 가 V01~V16 전체를 종합 실행          |
| Claude 가 적합하다고 판단할 때 | Tier 2 `skills/verify-*` 를 상황에 맞게 호출 (예: TS 변경 시 `verify-ts`) |

### 수동 실행 (CLI)

```bash
cd <PROJECT_DIR>
just --justfile <VERIFIERS_REPO>/justfile verify          # V01~V19 전체
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
| `/verify`                     | 현재 프로젝트에 V01~V19 종합 검증 즉시 실행          |
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

`hooks/validators/` 에 위치한 21개 모듈 중 19개가 `validators/__init__.py:get_all_validators()` 에 등록되어 V01~V19 검증을 수행합니다 (V17 UI 는 미구현, `hasura_graphql_enforcement.py` 는 skill 전용 — 자세한 사항은 카탈로그 §6 참조):

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
- **Circuit breaker**: `stop_validator.py` 는 `.verifier-block-count` 파일에 연속 차단 횟수를 기록하고, 3 회 연속 차단되면 통과시켜 무한 루프를 방지합니다.
- **Tier 분리**: 빠른 보안 차단 (Tier 1, <100ms) / 상황별 호출 (Tier 2) / 무거운 종합 검증 (Tier 3, ≤120s) 으로 비용·블로킹 정책을 분리.

## License

MIT
