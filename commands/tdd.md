---
name: tdd
description: TDD 전체 워크플로우 실행 — superpowers TDD skill + tdd-writer + builder/validator 오케스트레이션
---

## TDD 워크플로우

superpowers:test-driven-development skill의 Iron Law를 따르는 전체 R-G-R 사이클.

### 사용법
/tdd <PRD or specification or feature description>

### Flow
1. **RED**: tdd-writer agent로 테스트 코드 생성 (context isolation)
   - PRD/spec에서 testable behaviors 추출
   - Go: _test.go (testify + table-driven)
   - TS: .test.ts(x) (vitest/jest + RTL)
   - Python: test_*.py (pytest)
   - 테스트 실행 → 모두 FAIL 확인 (Iron Law)

2. **GREEN**: builder agent로 최소 구현
   - 테스트 파일만 참조하여 구현
   - V09/V10/V11 test runner가 PostToolUse로 실시간 검증
   - 목표: 모든 테스트 통과, 추가 기능 없음

3. **REFACTOR**: 코드 정리
   - V06/V07 quality checks
   - validator agent 최종 검증
   - 테스트 여전히 green 확인

### 요구사항 변경 시 (Re-enter RED)
개발 중 PRD 변경이나 요구사항 수정이 필요한 경우:
1. GREEN 단계 중단
2. `/tdd-update`로 테스트 코드 수정 (tdd-writer Update 모드)
3. 수정된 테스트 FAIL 확인
4. GREEN 단계 재진입 (수정된 테스트 통과하도록 구현)

이것은 TDD의 자연스러운 흐름 — R-G-R 사이클의 어느 시점에서든 RED로 돌아갈 수 있다.

### 기존 Skill 활용
- TDD 프로세스 가이드: `superpowers:test-driven-development`
- 브레인스토밍: `superpowers:brainstorming`
- 코드 리뷰: `superpowers:requesting-code-review`
