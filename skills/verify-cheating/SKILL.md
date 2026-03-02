---
name: verify-cheating
description: AI 에이전트의 테스트 삭제/비활성화/약화 치팅 행위 감지 (Kent Beck 원칙)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/ai_cheating_guard.py"
          timeout: 10
---

## AI Cheating Guard가 활성화되었습니다.

테스트 파일(`*_test.go`, `test_*.py`, `*.test.ts` 등) 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V13-TEST-DELETED**: 테스트 함수/메서드 삭제 감지 (error) — 테스트를 삭제하여 "통과"시키는 행위 차단
- **V13-TEST-DISABLED**: skip/disable 어노테이션 추가 (warning) — `t.Skip()`, `@pytest.mark.skip`, `xit()` 등
- **V13-ASSERTION-REMOVED**: assert/expect 문 개수 감소 (warning) — 검증 로직 약화 감지
- **V13-TEST-WEAKENED**: 엄격한 assertion이 느슨한 것으로 교체 (warning) — `toEqual` → `toBeTruthy` 등
