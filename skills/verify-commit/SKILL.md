---
name: verify-commit
description: 세션 종료 시 커밋 규율 검증 — 구조/행동 변경 분리, 대규모 diff, 테스트 누락, 미커밋 변경사항 (Stop mode only)
---

## Commit Discipline 검증이 활성화되었습니다.

Kent Beck의 커밋 규율 원칙에 따라 세션 종료 시 자동으로 검증됩니다.

### Stop hook 검사 (세션 종료 시)
- **V12-MIXED-CHANGE**: 구조 변경(rename/move)과 행동 변경(기능 추가/수정)이 혼재 → 분리 커밋 권장
- **V12-LARGE-DIFF**: 15개+ 파일 수정 → atomic commit 분할 권장
- **V12-NO-TEST-IN-FEATURE**: 소스 코드 변경 있으나 테스트 파일 변경 없음 → 테스트 추가 권장
- **V12-UNSTAGED-CHANGES**: 미커밋 변경사항 존재 → 커밋 검토 권장
