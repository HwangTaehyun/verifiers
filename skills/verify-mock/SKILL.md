---
name: verify-mock
description: Frontend hook 파일에서 mock/hardcoded data 사용을 검출하여 실제 API 연동을 강제합니다. use*Data.ts 작성/수정 시 자동 검증.
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/mock_data_guard.py"
          timeout: 15
---

## Mock Data Guard 검증이 활성화되었습니다.

`hooks/use*Data.ts` 파일 수정 시 자동으로 검증됩니다.

### PostToolUse 검사 (빠른, <5초)
- **V18-MOCK-VARIABLE**: `MOCK_*`, `mock*`, `FAKE_*` 등 mock 변수명 사용 금지
- **V18-MOCK-DATA**: `setState([{rank:1, ...}])` 식 하드코딩 데이터 직접 주입 금지
- **V18-FAKE-DELAY**: `new Promise(r => setTimeout(...))` 식 가짜 네트워크 지연 금지
- **V18-TODO-API**: `// TODO: Replace with actual API call` 식 미완성 주석 금지
- **V18-NO-API-IMPORT**: `use*Data.ts` 파일에 API client import 누락 검출

### Stop hook 검사 (느린, 포괄적)
- 위 모든 검사를 `web/src/hooks/` 디렉토리 전체에 대해 실행

### 왜 필요한가?
Mock data로 프론트엔드를 개발하면 API 연동을 잊고 배포하는 사고가 발생합니다.
이 검증기는 hook 파일에서 mock data 패턴을 즉시 감지하여 실제 Connect-RPC API 호출로 대체하도록 강제합니다.
