---
name: verify-docker
description: Docker 작업 시 docker-compose/Dockerfile 포트 충돌, 네트워크, VIRTUAL_HOST, healthcheck, 환경변수 참조 검증
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/docker_compose.py"
          timeout: 15
---

## Docker 검증이 활성화되었습니다.

`docker-compose*.yaml`, `Dockerfile*` 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V05-PORT-CONFLICT**: 서비스 간 호스트 포트 중복
- **V05-VHOST-NO-NETWORK**: VIRTUAL_HOST 설정이 nginx-proxy 네트워크 누락
- **V05-UNDEFINED-NETWORK**: top-level에 미정의된 네트워크 참조
- **V05-MISSING-HEALTHCHECK**: depends_on condition: service_healthy인데 healthcheck 없음
- **V05-MISSING-ENV-VAR**: ${VAR} 참조가 .env에 미정의 (default 없음)
