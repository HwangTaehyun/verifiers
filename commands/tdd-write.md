---
name: tdd-write
description: TDD Red Phase — PRD/설계로부터 테스트 코드 생성 (구현은 하지 않음)
---

## TDD Write (Red Phase — Create)

### Red Phase란?
TDD의 Red-Green-Refactor 사이클 중 첫 번째 단계.
테스트를 먼저 작성하고, 아직 구현이 없으므로 테스트가 **실패(빨간색)**하는 것을 확인한다.
이 실패가 "기능이 아직 없다"는 올바른 이유로 발생해야 한다 (오타나 문법 에러가 아님).

- **RED** (테스트 작성 → 실패 확인) ← 이 커맨드가 하는 일
- **GREEN** (최소한의 구현 코드 작성 → 테스트 통과)
- **REFACTOR** (코드 정리, 테스트는 계속 통과)

### 사용법
/tdd-write <PRD or specification>

### Flow
1. tdd-writer agent로 테스트 코드 생성 (Create 모드)
2. 테스트 실행 → 모두 FAIL 확인 (Red — 기능 미구현으로 인한 실패)
3. 생성된 테스트 파일 목록 + 예상 구현 사항 리포트
4. 구현은 사용자가 진행 (또는 /tdd로 전체 사이클)
