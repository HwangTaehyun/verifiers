---
name: verify-graphql
description: GraphQL/genqlient 작업 시 stale 감지, omitempty 누락, 함수 참조 검증으로 genqlient 관련 런타임 버그 방지
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/graphql_gen.py"
          timeout: 15
---

## GraphQL 검증이 활성화되었습니다.

`*.graphql`, `genqlient.yaml`, `genqlient.go` 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V02-YAML-MISSING-FIELD**: genqlient.yaml 필수 필드 누락
- **V02-STALE-GEN**: query/schema 변경 후 genqlient.go 재생성 필요 (해시 기반)
- **V02-OMITEMPTY**: *uuid.UUID 필드에 omitempty 태그 누락 (null UUID "0000..." 버그)
- **V02-MISSING-FUNCTION**: repository에서 호출하는 genqlient 함수가 generated 코드에 없음
