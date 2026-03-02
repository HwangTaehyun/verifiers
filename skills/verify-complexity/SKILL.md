---
name: verify-complexity
description: 함수 복잡도, 길이, 중첩, 파라미터 수 검증 (Uncle Bob + Neal Ford 원칙)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/complexity_guard.py"
          timeout: 15
---

## Complexity Guard가 활성화되었습니다.

`*.go`, `*.py`, `*.ts`, `*.tsx` 수정 시 자동으로 검증됩니다.

### PostToolUse 검사 (수정된 파일)
- **V14-HIGH-COMPLEXITY**: 함수 cyclomatic complexity > 10 warning, > 20 error
- **V14-LONG-FUNCTION**: 함수 길이 > 50줄 warning, > 100줄 error
- **V14-DEEP-NESTING**: 중첩 깊이 > 4 warning
- **V14-TOO-MANY-PARAMS**: 함수 파라미터 > 5개 warning

### 언어별 분석
- **Python**: AST 기반 정밀 분석 (cyclomatic complexity, nesting depth)
- **Go**: 정규식 기반 휴리스틱 분석
- **TypeScript**: 정규식 기반 휴리스틱 분석
