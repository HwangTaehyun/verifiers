---
name: verify-py-test
description: Python 소스 수정 시 관련 pytest 자동 실행 (V11 타겟 테스트 러너)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/py_test_runner.py"
          timeout: 45
---
Python 테스트 자동 실행 활성화. *.py 수정 시 관련 pytest만 실행.
- V11-TEST-FAIL: 테스트 실패
- V11-NO-TEST: 대응하는 test_*.py 없음 (warning)
- V11-REPEATED-FAIL: 3회 연속 실패 → PRD/테스트 수정 필요 여부 확인
