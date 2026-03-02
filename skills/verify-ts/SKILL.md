---
name: verify-ts
description: TypeScript 작업 시 any 타입 검출, 하드코딩 색상, console.log, deprecated MUI, ESLint 검증
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/ts_quality.py"
          timeout: 15
---

## TypeScript 검증이 활성화되었습니다.

`*.ts`, `*.tsx` 수정 시 자동으로 검증됩니다.

### PostToolUse 검사 (빠른, <5초)
- **V07-NO-ANY**: explicit any 타입 사용 금지
- **V07-HARDCODED-COLOR**: theme.palette 대신 #hex 색상 사용
- **V07-NO-CONSOLE**: 프로덕션 코드에서 console.log/debug/info
- **V07-DEPRECATED-MUI**: makeStyles, @material-ui/ 등 MUI v4 패턴
- **V07-ESLINT-***: ESLint 단일 파일 (React hooks, a11y 등)

### Stop hook 검사 (느린, 포괄적 — Tier 3에서 자동)
- **V07-TSC-***: 전체 타입 체크 (tsc --noEmit)
- **V07-ESLINT-***: ESLint 전체 프로젝트
- **V07-CIRCULAR-IMPORT**: 순환 import 검출 (madge)
- **V07-UNUSED-CODE**: 미사용 코드/export/dependency (knip)
