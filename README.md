# verifiers

> AI 에이전트 코딩 워크플로우를 위한 재사용 가능한 검증 시스템 (Claude Code hooks + skills + agents)

`verifiers`는 Claude Code 가 생성한 코드를 **세 단계(Tier 1/2/3)** 로 검증하는 모듈입니다.
보안 위반은 즉시 차단하고, 상황별 품질 점검은 skill 로 호출하며, 턴 종료 시점에는 60개의 등록 validator (V01~V80, V17/V24/V55/V59/V63/V67-V70/V73-V75/V78-V79 미사용·예약) 가 일괄 실행됩니다. 현재 **1,698 개의 pytest** 가 검증 로직을 보호합니다. 각 validator·hook 의 상세 동작은 [`docs/VERIFIERS-CATALOG.md`](docs/VERIFIERS-CATALOG.md) 를 참조하세요.

| Tier | 진입점 | 트리거 / 시점 | 역할 |
| :--: | ------ | ------------- | ---- |
| **1** | `security_hook.py` | PostToolUse · `Edit \| Write \| MultiEdit` · <100ms | regex 기반 보안 즉시 차단 |
| **2** | `router.py` + `skills/verify-*` | PostToolUse 자동 + 사용자/Claude 호출 | 파일 패턴 매칭 validator 만 디스패치 (content-hash 캐시 포함) |
| **3** | `stop_validator.py` | Stop · ≤120s | V01~V80 등록 validator 일괄 실행 (Phase 63 PASS-state 캐시 + circuit breaker · FeedbackTracker) |

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
| Write-time skills (2개) | `~/.claude/skills/{write-business-function,env-vs-config-decision}` |
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
| Edit / Write / MultiEdit 직후 | Tier 1 `security_hook.py` 가 보안 위반 패턴을 즉시 차단 + Tier 2 `router.py` 가 파일 패턴 매칭 validator 만 디스패치 |
| Claude 가 turn 을 끝낼 때     | Tier 3 `stop_validator.py` 가 V01~V80 전체를 종합 실행 (Phase 63 PASS-state 캐시로 입력 변경 없는 항목 skip) |
| Claude 가 적합하다고 판단할 때 | `skills/verify-*` 를 상황에 맞게 호출 (예: TS 변경 시 `verify-ts`) |
| Claude 가 비즈니스 로직 함수 **작성** 직전 | `skills/write-business-function` 가 자동 활성 — input validation → context validation → throw 패턴 + 언어별 docstring 강제 (write-time 가이드, post-hoc 검사 X) |

### 수동 실행 (CLI)

```bash
cd <PROJECT_DIR>
just --justfile <VERIFIERS_REPO>/justfile verify          # V01~V80 전체
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
| `/verify`                     | 현재 프로젝트에 V01~V80 종합 검증 즉시 실행          |
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

`hooks/validators/` 에 위치한 60개 validator 가 `validators/__init__.py:get_all_validators()` 에 등록되어 V01~V80 검증을 수행합니다 (V17 / V24 / V55 는 미사용 — V17 UI 미구현, V24 결번, V55 사용자 결정으로 컷; 나머지 V## 결번은 Phase 74+ 예약). 7 개 카테고리 (`BUILTIN_GROUPS`) 로 그룹 단위 disable 지원:

- **code-quality** (19): V06 Go, V07 TS, V14 복잡도, V19 Python ruff, V34 Go err 래핑, V35 ctx 전파, V36 HTTP 하드닝, V38 golangci 엄격, V39 컨텍스트 로거, V60 Go layer imports, V62 Go typed env, V64 TS layer imports, V65 TS any-budget ratchet, V66 TS no-direct-fetch, V71 React hooks plugin enforced, V72 React Suspense+EB pairing, V76 RHF↔Zod schema sync, V77 RHF defaultValues type-match, V80 Go circular deps (Tarjan SCC)
- **test-execution** (5): V09 Go test, V10 TS test, V11 Python test, V21 pytest, V37 race + coverage
- **env-config** (2): V01 env 시크릿, V22 multi-env 일관성
- **docker** (6): V05 docker-compose, V25 multi-binary, V26 prod 하드닝, V44 base digest, V45 healthcheck, V58 reproducible build
- **api-rpc-data** (12): V02 graphql-gen, V03 proto/Connect, V04 Hasura migration, V20 Hasura GraphQL, V23 buf governance, V27 connect handler, V46 enum rollback, V47 FK 인덱스, V48 Hasura 권한 의도, V49 OTel, V50 livez/readyz, V56 /metrics
- **security** (8): V08 시크릿/CORS/PHI/.gitignore, V18 mock data, V40 Action SHA pin, V41 workflow 권한, V42 Dependabot, V43 이미지 스캐닝, V57 SBOM, V61 Go SQL parameterization (OWASP A03)
- **process** (8): V12 commit, V13 AI cheating, V15 의존 방향, V16 linter 설정, V51 ADR, V52 README 배지, V53 community files, V54 commitlint

각 validator 는 `tests/test_*.py` 에 1:1 대응하는 단위 테스트를 갖습니다 (총 1,698 tests, 60 validators × 평균 28 tests).

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

### Tier 3 SLA (parallel runner + 다층 캐시 + 단일 walk 인덱스)

`lib/parallel_runner.py` 가 `ThreadPoolExecutor(max_workers=min(8, len(validators)))` 로 60 개 validator 를 병렬 실행합니다. Phase 36 에서 ProcessPoolExecutor → ThreadPoolExecutor 로 전환 (subprocess 호출이 GIL 을 놓아서 thread 가 process 와 동등 — spawn cost + pickling 제거).

#### 실측 — ax-finance-project (102,975 entries / web/node_modules 91,753)

| 시나리오 | Phase 64 까지 | Phase 65 적용 후 | Phase 71 (현재) | 누적 절감 |
|---|---:|---:|---:|---:|
| COLD (전체 wipe) | 96,902 ms | 6,182 ms | **15,267 ms** ¹ | −84% |
| **WARM** | 79,531 ms | 6,186 ms | **3,461 ms** | **−96%** |
| EDIT 1 .go file | 79,364 ms | 5,791 ms | **3,513 ms** | −96% |
| 캐시 모두 비활성 | 97,429 ms | 6,288 ms | **5,678 ms** | −94% |

¹ Phase 65 의 6.2 s COLD 측정은 V07 의 `tsbuildinfo` / `.eslintcache` 가 부분 보존된 상태였습니다. Phase 71 측정은 모든 native cache + Go `$GOCACHE/test/` 까지 wipe 한 진짜 cold — 이게 사용자가 처음 verifier 적용한 첫 Stop hook 의 실제 비용입니다. 그 이후의 모든 Stop hook 은 WARM 시나리오 (3.5 s).

새 wall floor 는 V07 (`tsc --noEmit` + eslint + madge + knip 합 약 2 s) + V06 (`go build` + cached `go test`) — subprocess 자체가 floor 며, 두 validator 가 ThreadPool 의 longest pole.

#### Phase 66-71: subprocess 자체 캐시 + 코드 정리 (cold 첫 실행 후의 매일 Stop 비용 5s → 3.5s)

Phase 65 가 walk 비용을 제거한 후 V07 의 subprocess 자체가 wall floor 가 됐습니다. Phase 66-71 는 그 subprocess 비용을 cProfile 로 분해해서 캐시 가능한 모든 비용을 잡았습니다:

| Phase | 핵심 변경 | 효과 |
|-------|----------|------|
| **66** | V06/V09 의 `-count=1` 제거 → Go 자체 test cache 복원 | warm test 5s → 0.7s |
| **67** | V07 ESLint fix: `bun run` script wrapper 우회 + v9-only `--no-warn-ignored` 제거 + cache-location 을 디렉토리 → 파일 | V07 가 비로소 정상 동작 (이전엔 0.17s 에 fail-fast → 0 finding, 지금 104 findings) |
| **68** | V07 madge subprocess cache (Phase 61 패턴 확장) | madge 1.5s → 0.7s |
| **69** | V20 / V34 / V35 / V39 (Go regex scanner 4개) per-file 캐시 | 각 800ms → 100ms (CPU 2.4s 절감) |
| **70** | V07 knip cache + `detect_tool_version` lru_cache + eager file_index build | V07 isolated 3.6s → 2.0s (−44%) |
| **71** | T1: 6 validator 가 더 file_index 공유 (V16/V08/V01/V27/V54). T2: `ProjectContext.compose_docs` cached_property. T3: `Finding`/`BaseValidator` → `lib/validators_core.py` (layering invariant 회복). T4: V08 PerFileCache. L4: `circuit_breaker` 모듈 분리. + 신규 V23-TS-NOCHECK 룰 | wall 3.5s 유지, 코드 일관성 + V08 cache 도달, 13/14 walk-heavy validator 가 file_index 공유 |

#### Phase 65: 단일 walk 프로젝트 인덱스 (`lib/file_index.py`)

**문제**: V05 / V14 / V15 / V38 / V44 / V45 / V58 가 각자 `Path.glob("**/...")` 으로 프로젝트 트리를 walk. 21k+ 파일 monorepo 에서 단일 walk 가 ~1.3 s. 6 개가 ThreadPool(8w) 에서 동시 실행되면:

1. **GIL 직렬화** — `Path.glob` 의 hot loop 가 pure-Python 이라 GIL 을 놓지 않음. 6 thread 가 GIL 한 개를 놓고 경합 → 한 번에 한 thread 만 진행.
2. **macOS APFS IO 직렬화** — 6 thread 의 동시 `stat` 호출이 커널에서 큐잉.
3. 결과: 단일 walk 1.3 s × 6 → 각 thread 가 16 s 측정. 실제 검증 작업은 1-5 s 인데 walk 가 16 s 로 묻어버림.

**해결**: ProjectContext 에 `file_index` cached_property 추가. Stop hook 시작 시 ONE 번 `os.walk` 로 전체 트리 인덱싱:

```
[Stop hook]
   │
   ├─→ ctx.file_index 첫 접근 시 ProjectFileIndex.build() 호출:
   │     ├─ os.walk(root, followlinks=False)
   │     ├─ dirnames[:] = [...] in-place 변형 → 디렉토리 단위 prune
   │     │     • DEFAULT_PRUNE_NAMES (.git, node_modules, vendor, __pycache__, .venv, ...)
   │     │     • exclude.paths 매칭 (vendor/**, web/build/**, **/__generated__/**, ...)
   │     ├─ 통과한 파일마다 (path, size, mtime_ns) 기록
   │     └─ {by_ext, by_name} dict 인덱스 빌드
   │
   ├─→ 모든 validator 가 같은 인덱스 query (0.1-1 ms 씩):
   │     ctx.file_index.find_by_pattern("Dockerfile*", "*.Dockerfile")
   │     ctx.file_index.find_by_pattern("*.go", "*.py", "*.ts", "*.tsx")
   │     ctx.file_index.find_by_pattern(".golangci.yaml", ".golangci.yml")
   │     ctx.file_index.find_by_pattern("docker-compose*.yaml")
   │
   └─→ Phase 63 cache hash 도 같은 인덱스 사용:
         ctx.file_index.hash_for_patterns(v.file_patterns)
```

**핵심**: `dirnames[:] = [...]` 는 `os.walk` 만의 인터페이스 — `Path.glob` 에는 없음. 디렉토리 진입 직전에 list 를 변형하면 그 서브트리는 walk 에서 통째로 제외됨. node_modules 91k 진입을 0 회로 만든다는 뜻.

**측정**: 102,975 entries → 4,244 files indexed (96% prune). Build 288 ms (한 번), find_by_pattern 0.1-1 ms (39 회). 이전엔 7 × ~1,600 ms walk = 11 s 가 6.2 s 로.

#### Phase 61–64: 다층 캐시 (입력 변경 없으면 skip)

매 Stop 마다 49 개 validator 를 모두 돌리는 것조차 낭비라는 인식 하에, 4 단계 캐시 stack:

1. **Tier 3 PASS-state 캐시** (Phase 63, `lib/tier_cache.py`) — validator 가 zero-finding 으로 통과한 입력 해시를 `.verifiers/state/tier-cache/V##.json` 에 5 분 TTL 로 기록. 다음 Stop 에서 입력 (path × size × mtime sha256, Phase 65 부터는 file_index 가 계산) 이 같으면 validator 자체를 skip. **제외 목록**: V06/V09/V10/V11/V12/V21/V37 (test runner + git-state 의존).
2. **V14/V15 per-file 캐시** (Phase 64.4, `lib/per_file_cache.py`) — Phase 63 가 한 validator 의 통째 skip 이라면, Phase 64.4 는 한 validator 안에서 변경 안 된 파일의 findings 재사용. (validator_id, file_path, mtime_ns, config_fingerprint) 키 → cached findings list. V14 21s → 669ms.
3. **V07/V03 native + subprocess 캐시** (Phase 61) — eslint `--cache`, tsc `--incremental`, V03 buf-lint 결과를 `.verifiers/state/subprocess-cache/<label>.json` 에 7-day TTL FIFO 로 캐싱.
4. **Per-validator timeout 오버라이드** (Phase 62) — `.verifiers/config.yaml` 의 `timeouts.per_validator` 로 V21 (pytest) 는 180 s, V19 (ruff) 는 5 s 등 차등 timeout.

#### 캐시 + 인덱스 escape hatch

| Env var                       | 효과                                                                   |
| ----------------------------- | ---------------------------------------------------------------------- |
| `VERIFIERS_PARALLEL=0`        | Tier 2/3 병렬 실행 비활성, sequential fallback                          |
| `VERIFIERS_NO_CACHE=1`        | V07 eslint/tsc + V03 buf 의 subprocess 캐시 비활성                      |
| `VERIFIERS_NO_TIER_CACHE=1`   | Phase 63 PASS-state 캐시 비활성 — 모든 validator 매 Stop 마다 강제 실행 |
| `VERIFIERS_NO_PER_FILE_CACHE=1` | Phase 64.4 / 69 / 71 per-file 캐시 비활성 (V14/V15/V20/V34/V35/V39/V08) |
| `VERIFIERS_DEBUG=1`           | hook 디버그 로그 활성                                                   |

> `file_index` 는 escape hatch 없음 — 캐시가 아니라 walk 통합이라 비활성화 시 30 초+ 의 GIL/IO 경합으로 회귀. 대신 `tests/test_file_index.py` 가 동작 invariant 를 박제.

`per-validator timeout = 30 s` (기본, `.verifiers/config.yaml` 의 `timeouts.per_validator` 로 V## 별 override). 한 validator 가 hang 되어도 나머지는 계속 실행되며, hang 된 항목은 `V##-TIMEOUT` sentinel finding 으로 표시됩니다 (silent false-approve 방지).

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
