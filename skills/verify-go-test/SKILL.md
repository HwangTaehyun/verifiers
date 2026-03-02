---
name: verify-go-test
description: Go 소스 수정 시 해당 패키지 테스트만 자동 실행 (V09 타겟 테스트 러너)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/go_test_runner.py"
          timeout: 45
---
Go 테스트 자동 실행 활성화. *.go 수정 시 해당 패키지 테스트만 실행.
- V09-TEST-FAIL: 테스트 실패
- V09-NO-TEST: 대응하는 _test.go 없음 (warning)
- V09-REPEATED-FAIL: 3회 연속 실패 → PRD/테스트 수정 필요 여부 확인
