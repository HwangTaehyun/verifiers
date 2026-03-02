---
name: verify-ts-test
description: TypeScript 소스 수정 시 관련 테스트만 자동 실행 (V10 타겟 테스트 러너, vitest/jest 자동 감지)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/ts_test_runner.py"
          timeout: 45
---
TypeScript 테스트 자동 실행 활성화. *.ts(x) 수정 시 관련 테스트만 실행.
- V10-TEST-FAIL: 테스트 실패
- V10-NO-TEST: 대응하는 테스트 파일 없음 (warning)
- V10-REPEATED-FAIL: 3회 연속 실패 → PRD/테스트 수정 필요 여부 확인
