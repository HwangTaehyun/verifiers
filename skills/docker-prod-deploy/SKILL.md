---
name: docker-prod-deploy
description: Docker production/dev 배포 검증 — Dockerfile 베스트 프랙티스, production compose 보안, dev override 핫리로드 패턴
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/docker_prod_deploy.py"
          timeout: 15
---

## Docker Production Deployment 검증이 활성화되었습니다.

`Dockerfile*`, `*.Dockerfile`, `docker-compose*.yaml`, `.dockerignore` 수정 시 자동 검증됩니다.

### Dockerfile 베스트 프랙티스
- **V17-DOCKERFILE-NO-USER**: Production 스테이지가 root로 실행 (USER 디렉티브 누락)
- **V17-DOCKERFILE-NO-EXPOSE**: EXPOSE 디렉티브 누락
- **V17-DOCKERFILE-COPY-ALL**: `COPY . .` 사용하는데 .dockerignore 없음 (시크릿 유출 위험)
- **V17-DOCKERFILE-NO-MULTISTAGE**: 멀티 스테이지 빌드 미사용 (dev/builder/prod 분리 필요)

### Production Compose
- **V17-PROD-PORT-EXPOSED**: 프로덕션에서 호스트 포트 노출 (Traefik 라우팅 권장)
- **V17-PROD-DEV-MODE**: 프로덕션 설정에 dev 모드 활성화 (APP_DEV=true 등)
- **V17-PROD-WILDCARD-CORS**: 프로덕션에서 CORS `*` 사용
- **V17-PROD-NO-TRAEFIK-LABELS**: Traefik 라우팅 라벨 누락
- **V17-PROD-NO-RESOURCE-LIMITS**: 리소스 제한(CPU/메모리) 미설정

### Dev Override Compose
- **V17-DEV-NO-VOLUME-MOUNT**: dev override에서 소스 볼륨 마운트 없음 (핫리로드 불가)
- **V17-DEV-NO-BUILD-TARGET**: dev override에서 build target이 'dev'가 아님
