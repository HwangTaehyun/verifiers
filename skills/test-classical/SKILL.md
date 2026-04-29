---
name: test-classical
description: AI 에이전트가 테스트 코드를 작성·수정할 때 따라야 할 Classical (Chicago/고전파) 스타일 규칙. 내부 모킹·호출 순서 검증 대신 외부 IO 경계만 모킹하고 결과로 검증한다. 출처: Atipico1/ai-testing-rules.
---

# Classical-school Testing Rules

Codex / Claude Code / Cursor 같은 에이전트가 테스트 코드를 만들 때 자동으로 적용되어야 하는 규칙입니다.
근거는 [Atipico1/ai-testing-rules](https://github.com/Atipico1/ai-testing-rules) 의 AGENTS.md (작성자: 김태성, 2025-2026 AI 테스팅 논문 20여 편 + OpenAI Codex 521개 Rust 테스트 감사 결과). 이 스킬은 그 규칙을 verifiers 프로젝트의 컨벤션에 맞춰 정리한 버전입니다.

## Core Philosophy

1. **동작을 테스트하라, 구현이 아니라.** 순수 리팩토링이 테스트를 깨면 안 된다.
2. **시스템 경계에서만 모킹하라.** 안쪽은 모두 진짜.
3. **Classist (Chicago) TDD 가 기본.** Mockist (London) 는 AI 가 쓰는 코드베이스에서 빠르게 썩는다.
4. **의미 있는 적은 테스트 > 누수가 많은 많은 테스트.**

> Hide the implementation from the test. Hide the test from the implementation. Only behavior connects them.

## Mocking Rules

**모킹해도 되는 (이것만):**
- Database / ORM
- 3rd-party HTTP API
- 파일시스템, 시계, 무작위, 네트워크
- 프로세스 경계를 넘는 모든 것 (subprocess, shell)

**절대 모킹하지 말 것:**
- 본인 도메인 객체 (Value Objects, DTOs, Entities)
- 순수 함수, 유틸리티
- 같은 코드베이스 안의 협력자 (같은 모듈/같은 서비스의 호출)
- 테스트 대상 자체 (이걸 모킹하고 싶다면 단위 경계 자체가 잘못된 것)

선호도:
- HTTP-level fake (`wiremock`, `msw`, `nock`) > trait/interface mock
- 실제 임시 파일시스템 (`tmp_path`, `tempfile`) > 모킹된 `fs`
- 본 프로젝트는 **subprocess.run 모킹 OK** (외부 명령은 시스템 경계)
- 본 프로젝트는 **BaseValidator.run / 본인 함수 모킹 ❌** (내부 협력자)

## Assertion Rules

- **반환값**과 **관찰 가능한 상태(observable state)** 를 검증.
- `toHaveBeenCalledWith(...)` / `verify(...)` / spy 호출 횟수를 **주된 검증으로 쓰지 마라**.
- 객체 전체 비교를 우선 (`expect(result).toEqual(expected)`) — 필드별 비교는 보조.
- LLM 텍스트, 타임스탬프, 순서 무관 set 등 비결정적 출력에는 snapshot 금지.

## Naming Rules

테스트 이름은 **관찰 가능한 동작**을 적는다. 메서드명·내부 호출명은 절대 적지 마라.

```
# ❌ 구현 냄새
def test_findUnique_called_once(): ...
def test_calls_upsert_then_emits_event(): ...
def test_should_work(): ...

# ✅ 동작
def test_returns_cached_result_when_fetched_within_ttl(): ...
def test_rejects_login_when_password_is_expired(): ...
def test_charges_full_price_for_non_vip_users(): ...
```

템플릿: `test_<subject>_<expected_behavior>_when_<condition>`

## Structure Rules

| 레이어 | 목적 | 예산 |
|--------|------|------|
| Unit | 순수 로직, 엔티티, util | 많이, in-memory, 밀리초 |
| Integration | 모듈 + 진짜 DB/큐 | 적당히, 도메인당 |
| E2E | 핵심 사용자 여정 | 적게, 여정당 1 |
| Regression | 과거 incident 당 1 | 버그 발생 시 |

- E2E 는 핵심 여정당 하나. 도메인당 통합 테스트 한 줌.
- 단위 테스트는 **로직이 자명하지 않을 때만**. getter, DI 배선, 프레임워크 글루에는 단위 테스트 X.
- Unit spec 은 소스 옆에. Integration/E2E 는 별도 트리.
- 비싼 라이브 테스트는 env flag (`LIVE_TEST=true`, `RUN_EXPENSIVE=1`) 뒤로.

## Domain Entity Rules

다음 중 하나라도 사실이면 도메인 엔티티를 추출하라:
- 비즈니스 로직이 같은 데이터 위 2+ 서비스에 흩어져 있다.
- 서비스가 평범한 DB row 위에서 산술·상태 전환을 한다.
- 사실은 순수한 로직을 테스트하기 위해 DB를 띄워야 한다.

```python
# Before — 로직이 서비스에, ORM 에 묶임
user.hunger = user.hunger - EAT * 2
user.energy = user.energy + SLEEP * 2
db.user.update(user)

# After — 로직은 엔티티에, 서비스는 영속화만
user.eat()
user.sleep()
user_repo.save(user)
```

`User.eat()` 은 in-memory 단위 테스트. 밀리초, 모킹 없음, 드리프트 없음.

## Property-Based Testing

큰 입력 공간에 명확한 invariant 가 있는 코드 (parser, encoder, sorter, validator, state machine) 는 example 테스트 외에 property-based 테스트를 쓴다. 라이브러리:
- Python: `hypothesis`
- TypeScript: `fast-check`
- Rust: `proptest`

룰: **같은 함수에 4번째 example 테스트를 쓰고 있다면 property 로 전환하라.**

## Flaky Test Rules

1. flaky 테스트를 **절대 commit 하지 마라**. 들어왔다면 24시간 안에 격리.
2. 격리 = issue 링크 + owner + deadline 으로 skip. owner 없으면 삭제.
3. **근본 원인을 고쳐라** — retry 루프, `sleep()`, timeout 늘리기로 막지 말 것.
4. 흔한 root cause: 공유 전역 상태, 실제 시계, 테스트 순서, seed 안 한 randomness, 네트워크. 증상 말고 원인 수정.

## Migration Rules (기존 Mockist 코드베이스)

스포츠 삼아 기존 테스트를 다시 쓰지 말고, 점진적으로 적용:

1. **새 테스트** (오늘부터 작성하는 것) 는 이 룰을 완전히 따른다.
2. **수정 중인 파일**: 테스트를 편집할 일이 있을 때 경계 부분만 변환.
3. **최악의 위반자 우선**: `assert_called_with` / `mock.call_count` 가 가장 많은 상위 3-5 파일을 도메인 단위로 재작성.
4. 가장 위험한 도메인 하나에 먼저 진짜 DB (Testcontainers / docker-compose) 도입. 패턴 검증 후 확장.
5. 비결정적 출력의 snapshot 테스트는 삭제. 구조적 assertion 으로 대체하거나 그냥 삭제.

## Workflow Rules

- 명세에서 **실패하는 테스트를 먼저** 작성하고, 그것에 대해 구현한다.
- 코드를 먼저 만들고 에이전트에게 "이 파일 테스트 짜줘" 를 시키지 마라 — 현재 구현에 잠긴 coverage theater 가 나온다.
- **테스트 1개당 동작 1개**. 한 동작을 묘사하기 위해 `expect()` 3개가 필요한 건 OK; 3개의 동작을 테스트한다면 3개의 테스트로 분할.

## PR Red Flags — Reject or Rework

- 진짜 assertion 보다 `mock.*` 호출이 더 많음.
- `assert_called_with` / `verify()` 가 유일한 검증.
- import 가 `_internal/` 또는 private module 경로에 손을 뻗음.
- LLM, timestamp, network 출력의 snapshot.
- linked issue 와 owner 없는 `it.skip` / `pytest.skip`.
- 테스트가 함수 이름을 따라 매번 이름이 바뀜 (leakage).
- 한 public 함수만 가진 파일의 테스트 파일이 원본보다 김.
- 경계 모킹이나 진짜 DB 대신 `unittest.mock` 의 풀 mock 클래스가 새로 추가됨.

## When NOT to Write a Test

- 로직 없는 평범한 CRUD → E2E 하나로 충분.
- 프레임워크 배선 (DI, routing, modules) → 프레임워크가 자체 테스트.
- Config / 상수 → 타입 시스템 또는 schema validator 가 보장.
- 폐기될 throwaway 스크립트 → production 데이터 건드리지 않는 한.
- 곧 삭제할 코드.

**테스트가 보호하는 동작을 한 문장으로 적을 수 없다면, 그 테스트는 쓰지 마라.**

## verifiers 프로젝트에서의 적용

이 프로젝트의 자체 테스트가 따르는 패턴 (참고 모범 사례):

- ✅ `tests/test_router_cache.py` — 진짜 임시 파일시스템에 진짜 JSON, 결과(딕셔너리 내용)로 검증. 호출 횟수 검증 0.
- ✅ `tests/test_config_loader.py` — 실제 yaml 파일 작성·로드 round-trip. 오류 케이스도 진짜 파일로.
- ✅ `tests/test_exclusion.py` — 순수 함수 + 진짜 임시 디렉토리.
- ✅ `tests/test_parallel_runner.py` — `mock.patch` 없이 module-level dataclass test double 사용 (`_PassValidator`, `_CrashValidator`). subprocess pool 도 진짜로 띄움.
- ⚠️ `tests/test_stop_validator.py` — `mock.patch("hooks.validators.base.BaseValidator.run", ...)` 로 내부 메서드를 모킹 (London 스타일). 마이그레이션 후보. 대안: 위 `_PassValidator` / `_CrashValidator` 같은 진짜 validator 인스턴스로 교체.

새 validator·신규 모듈을 추가할 때는:
1. **외부 명령은 모킹 OK** (`subprocess.run`) — `tests/test_py_quality.py` 의 패턴 참고.
2. **내부 클래스/함수는 모킹 X** — `tests/test_router_cache.py` 의 진짜 I/O 패턴 참고.
3. 테스트 이름은 동작으로 (`test_collapses_identical_finding`, `test_records_new_entry`).
4. 필요하면 module-level dataclass test double 작성 (`_PassValidator` 같은 것).

PR 체크리스트 (CONTRIBUTING.md 의 체크리스트와 함께 적용):
- [ ] 새 테스트는 외부 경계 (subprocess, FS, HTTP) 만 모킹
- [ ] assertion 은 반환값·상태 기반, `assert_called_*` 가 주 검증이 아님
- [ ] 테스트 이름이 동작을 묘사 (구현 이름 X)
- [ ] 한 테스트 = 한 동작
- [ ] flaky 테스트가 아니거나, 격리된 채 issue 링크 있음

## 참고 자료

- 원전: [Atipico1/ai-testing-rules](https://github.com/Atipico1/ai-testing-rules) — 김태성, 2025-2026 AI 테스팅 논문 + OpenAI Codex 521개 Rust 테스트 감사
- 본 스킬은 위 AGENTS.md 를 verifiers 컨벤션에 맞춰 정리한 버전. 표현이 충돌하면 원전이 우선합니다.
