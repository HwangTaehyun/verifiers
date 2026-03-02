---
name: verify-env
description: Env/Config 작업 시 3-Layer Separation, .env.example 완전성, config 키 일관성, VITE_* 동기화 검증
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/env_config.py"
          timeout: 15
---

## Env/Config 검증이 활성화되었습니다.

`.env*`, `config/*.yaml`, `docker-compose*.yaml` 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V01-SECRET-IN-CONFIG**: config 파일에 시크릿 하드코딩 검출 (3-Layer 위반)
- **V01-ENV-MISSING**: docker-compose/Go 코드에서 참조하는 변수가 .env.example에 없음
- **V01-CONFIG-KEY-MISSING**: config 변형 간 누락된 키 경고
- **V01-VITE-ENV-MISSING**: import.meta.env.VITE_* 변수가 web/env/에 미정의
