---
name: verify
description: 전체 검증 활성화 — 모든 V01~V08 validator를 PostToolUse에 등록하여 매 Edit/Write 시 해당되는 validator 자동 실행
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/router.py"
          timeout: 30
---

## 전체 검증이 활성화되었습니다.

모든 파일 수정 시 해당되는 validator가 자동으로 실행됩니다.
`router.py`가 수정된 파일의 경로를 보고 적합한 validator를 선택하여 실행합니다.

### 활성화된 검증 (V01~V08)
- **V01**: Env/Config — 3-Layer Separation, .env.example 완전성
- **V02**: GraphQL — genqlient stale, omitempty, 함수 참조
- **V03**: Proto — buf lint, stale 감지, handler 매핑
- **V04**: Hasura — migration 순서, up/down 쌍, 위험 DDL
- **V05**: Docker — 포트 충돌, 네트워크, healthcheck
- **V06**: Go — go vet, gofmt, go build
- **V07**: TypeScript — any 검출, ESLint, 하드코딩 색상
- **V08**: Security — 시크릿 하드코딩, CORS, PHI 로깅
