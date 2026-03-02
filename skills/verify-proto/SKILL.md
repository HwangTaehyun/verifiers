---
name: verify-proto
description: Proto/Connect-RPC 작업 시 buf lint, stale 감지, handler 매핑, breaking change 검증
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/proto_connect.py"
          timeout: 30
---

## Proto/Connect-RPC 검증이 활성화되었습니다.

`proto/**/*.proto`, `buf.yaml`, `buf.gen.yaml` 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V03-BUF-LINT**: proto 네이밍/스타일 규칙 위반
- **V03-STALE-GEN**: proto 파일 변경 후 gen/ 코드 재생성 필요 (해시 기반)
- **V03-UNIMPLEMENTED-RPC**: rpc method에 대응하는 handler 구현 누락
- **V03-BREAKING**: main 브랜치 대비 breaking change 경고
