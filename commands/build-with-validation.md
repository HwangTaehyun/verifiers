---
name: build-with-validation
description: Builder/Validator 팀 워크플로우 — 구현과 검증을 서로 다른 에이전트로 분리 실행 (IndyDevDan 하이브리드 패턴)
---

## Build with Validation 워크플로우

이 커맨드는 IndyDevDan의 Builder/Validator 팀 패턴을 실행합니다.

### Flow

1. **Builder agent** 생성 → 구현 작업 수행 (모든 도구 접근 가능)
2. Builder 완료 시 → **Validator agent** 생성 → read-only 검증
3. Validator가 에러 발견 → Builder에게 수정 지시
4. Builder 수정 → Validator 재검증 (closed loop)
5. 전체 통과 시 완료

### 사용법

```
/build-with-validation <task description>
```

### 실행 순서

1. Builder agent를 생성하여 구현 작업을 위임합니다:
   - `agents/team/builder.md` 프로필 사용
   - 사용자가 요청한 작업을 구현

2. Builder가 완료를 알리면 Validator agent를 생성합니다:
   - `agents/team/validator.md` 프로필 사용 (read-only)
   - 전체 V01~V08 검증 수행

3. Validator가 에러를 발견하면:
   - 에러 리스트와 FIX 지시를 Builder에게 전달
   - Builder가 수정 후 다시 Validator 검증

4. 모든 검증 통과 시 사용자에게 완료 리포트

### Hooks와의 관계

- Hooks (Tier 1/2/3)는 **Builder가 작업 중에도 자동 실행** (매 Edit/Write마다)
- Validator는 **Builder 작업 완료 후** 포괄적 검증 (hooks보다 더 넓은 범위)
- 즉, hooks는 "실시간 경고", Validator는 "최종 QA 게이트"
