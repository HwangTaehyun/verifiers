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

### Tier 3 SLA (parallel runner)

`lib/parallel_runner.py` 가 4-worker `ProcessPoolExecutor` 로 19 개 validator 를 병렬 실행합니다. `scripts/benchmark_stop.py` 의 합성 워크로드 (15 light + 4 heavy = real V06 / V07 / V19 / V14 비용 모사) 측정 결과:

| 모드            | 벽시계         | 비고                                                 |
| --------------- | -------------- | ---------------------------------------------------- |
| Sequential      | ~5.6 s         | `VERIFIERS_PARALLEL=0` 또는 fallback                 |
| Parallel (4w)   | ~2.3 s         | 기본값                                                |
| 이상적 lower bound | ~2.0 s     | `max(per-validator)` — 무한 worker 가정              |
| **Speedup**     | **~2.5 ×**     | 큰 V06 (golangci-lint) 의 길이가 wall-clock 결정      |

실측 명령:

```bash
uv run python scripts/benchmark_stop.py            # 사람용
uv run python scripts/benchmark_stop.py --json     # CI / 모니터링용
```

`per-validator timeout = 30 s` (기본). 한 validator 가 hang 되어도 나머지는 계속 실행되며, hang 된 항목은 `V##-TIMEOUT` sentinel finding 으로 표시됩니다 (silent false-approve 방지).

## Per-project configuration

`<project>/.verifiers/config.yaml` 한 파일이 verifier 의 모든 동작을 조정합니다.
파일이 없거나 어떤 키도 명시하지 않으면 기본값으로 작동하니 안전하게 시작할 수 있습니다.

```yaml
# .verifiers/config.yaml — 자주 쓰이는 짧은 예시
exclude:
  paths: ["vendor/**", "**/__generated__/**"]
  per_validator:
    V14: ["legacy/**"]      # 복잡도 검사만 legacy/ 제외

validators:
  disabled: [V04, V20]      # Hasura 안 쓰는 프로젝트

security:
  phi_check_enabled: false  # 의료 도메인 외엔 끄는 게 일반적

docker:
  reverse_proxy_networks: ["traefik"]   # nginx-proxy 가 아닐 때
```

지원되는 모든 키 (`thresholds.*`, `exclude.*`, `validators.*`, `security.*`, `docker.*`),
각 validator 가 어떤 키를 읽는지 매핑 표, router/stop_validator 의 적용 우선순위,
디버깅 레시피, hard-fail 케이스 등 — **자세한 내용은 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

> **Phase21 BREAKING CHANGE**: 기본 `docker.vhost_check_mode` 가 `"production"` 으로 바뀌었습니다 (이전엔 사실상 `"all"`).
> 이전의 엄격한 동작을 원하면 명시적으로 `"all"` 지정 필요. [상세](docs/CONFIGURATION.md#1-풀-스키마).

## Validator metrics — 어떤 검사가 의미 있게 작동했나

verifiers 가 매 hook 호출에서 그 결과를 JSONL 한 줄로 기록합니다 — 어떤 validator 가 얼마나 자주 발동했는지, 그래서 실제로 finding 을 얼마나 냈는지, 평균 실행 시간이 얼마인지 모두 남습니다. 자가 개선형 agent 의 skill bloat 문제처럼 ([Hermes Curator 논의](https://github.com/NousResearch/hermes-agent/issues/7816)), 이런 운영 데이터가 있어야 "이 validator 가 비용만큼 효과를 내고 있나?" 를 정직하게 판단할 수 있습니다.

### 어디에 저장되는가 (Phase33b+)

**프로젝트별 분리**: 각 프로젝트 루트의 `.verifiers/state/metrics/V##-{name}.jsonl` 에 누적됩니다. verifiers 를 여러 프로젝트에서 hook 으로 쓰더라도 cross-project 섞임이 없고, 프로젝트를 지우면 metric 도 함께 정리됩니다.

```
<project-root>/
  .verifiers/
    state/
      metrics/
        V01-env-config.jsonl
        V08-security.jsonl
        V14-complexity-guard.jsonl
        ...
```

각 파일은 자동으로 10MB 넘으면 `.1` 백업으로 회전 (1단 FIFO, 최대 약 20MB / validator).

### 보는 법

```bash
# 현재 프로젝트 (cwd 기준 자동 detect) 의 최근 30일
uv run --script scripts/validator_metrics.py

# 90일로 확장
uv run --script scripts/validator_metrics.py --days 90

# JSON 출력 (파이프라인용)
uv run --script scripts/validator_metrics.py --json

# 다른 프로젝트의 metric 디렉토리 명시
uv run --script scripts/validator_metrics.py --log-dir /path/to/other/project/.verifiers/state/metrics
```

출력 예 (실측):

```
Validator metrics — last 30 days

ID                         state    uses  finds  errs warns   mean(ms)  effect
--------------------------------------------------------------------------------
V08-security               active   2977    310   274    36      603.2    0.10
V14-complexity-guard       active   2725  59480  1284 58196     1082.2   21.83
V19-py-quality             active   2646      2     2     0       15.5    0.00
V20-hasura-graphql         active    127   2674  2628    46      145.7   21.06
V09-go-test-runner         quiet    2667      0     0     0       22.7    0.00
V10-ts-test-runner         quiet    2638      0     0     0        0.0    0.00

Quiet   (2): V09-go-test-runner, V10-ts-test-runner
  → quiet validators fired but emitted no findings — review for false-positive rules or perf/value gaps.
  → dormant validators never fired — likely benign (file_patterns didn't match in this project).
```

### Lifecycle states

| state | 의미 | 보통 의사결정 |
|---|---|---|
| **active** | 최근 30일 안에 호출 + finding emit | 그대로 유지 |
| **quiet** | 호출은 됐지만 30일 동안 finding 0 | rule 검토 (false positive? 너무 엄격?) 또는 비용 / 가치 gap |
| **dormant** | 14일 동안 호출조차 안 됨 | 보통 benign — 그 언어/툴이 프로젝트에 없어서. action 불필요 |

### Effectiveness — 진짜 의미있는 metric

`effect` 컬럼은 `findings_emitted / use_count`. 0 에 가까우면 매번 호출되는데 아무것도 못 잡고 있다는 뜻이고, 1 이 넘으면 호출당 평균 1+ 개 finding 을 발행한다는 뜻입니다 (V14 가 21.83 = 호출마다 평균 22 개).

> **호출되고 안 쓰였거나 별로 의미없게 쓰였을 수도 있다** — 이게 정확히 effectiveness 가 보여주는 지점입니다. uses 가 많은데 finds 가 0이면 (V09/V10/V19 처럼) Tier 2 가 매 Edit 마다 비용을 치르면서 가치 없음 — 후보로 검토.

CLI 가 출력 밑단에 quiet / dormant 자동 분류해 줍니다. 의사결정은 사용자 몫 — 자동 archive 는 일부러 안 합니다 ([Hermes Curator 가 246/346 skill 을 archive 한 사례](https://github.com/NousResearch/hermes-agent/issues/7816#issuecomment-4341335259)는 강력하지만, validator 레벨에서는 사람이 한 번 더 보는 게 안전합니다 — pinned 메타 + archive workflow 는 추후 phase 로 분리).

## Contributing & Changelog

- 새 validator 를 추가하거나 기존 룰을 바꾸려면: [CONTRIBUTING.md](CONTRIBUTING.md) 의 V-ID 할당 / mode dispatch 보일러플레이트 / 테스트 컨벤션 / PR 체크리스트 참고.
- 모든 의미 있는 변경은 [CHANGELOG.md](CHANGELOG.md) 에 기록됩니다 (Keep a Changelog 형식).

## License

MIT — see [LICENSE](LICENSE).
