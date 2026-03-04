---
name: docker-cicd-deploy
description: "GitHub Actions CI/CD — Git tag 기반 Docker 이미지 빌드 → GHCR push → SSH EC2 배포 파이프라인 가이드"
---

## GitHub Actions Docker CI/CD Deploy Skill

Git tag push 시 GitHub Actions에서 Docker production 이미지를 빌드하여 GHCR에 push하고,
SSH로 EC2에서 pull하여 배포하는 파이프라인 가이드입니다.

---

### Architecture

```
git tag vX.Y.Z → git push origin vX.Y.Z
         │
   GitHub Actions (.github/workflows/deploy.yml)
         │
   ┌─────▼──────────────────────────────────┐
   │ Job 1: build-and-push                  │
   │  - QEMU + Buildx (ARM64 cross-compile) │
   │  - Login to ghcr.io                    │
   │  - Build images (target: prod)         │
   │  - Push with tags: vX.Y.Z + latest     │
   │  - GHA cache for layer reuse           │
   └────────────────────┬──────────────────┘
                        │
   ┌────────────────────▼──────────────────┐
   │ Job 2: deploy (needs: build-and-push) │
   │  - SSH via bastion (ProxyJump)        │
   │  - git pull (docker-compose files)    │
   │  - docker login ghcr.io              │
   │  - TAG=vX.Y.Z deploy-prod.sh         │
   │  - docker compose up -d --pull always │
   └───────────────────────────────────────┘
```

---

### Workflow 파일 구조

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    tags: ['v*']

env:
  REGISTRY: ghcr.io/<owner>/<repo>

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3          # ARM64 에뮬레이션
      - uses: docker/setup-buildx-action@v3         # Buildx 빌더
      - uses: docker/login-action@v3                # GHCR 로그인
      - uses: docker/build-push-action@v6           # 이미지 빌드+푸시
        with:
          platforms: linux/arm64                     # EC2 Graviton
          target: prod                              # 멀티스테이지 타겟
          cache-from: type=gha                      # GitHub Actions 캐시
          cache-to: type=gha,mode=max

  deploy:
    needs: build-and-push
    steps:
      - uses: appleboy/ssh-action@v1                # SSH (bastion 지원)
        with:
          proxy_host: ${{ secrets.EC2_SSH_HOST }}    # Bastion public IP
          host: ${{ secrets.EC2_DEPLOY_HOST }}       # Target private IP
```

---

### Required GitHub Secrets

| Secret | 용도 | 예시 |
|--------|------|------|
| `GHCR_TOKEN` | GHCR pull용 PAT (`read:packages`, `write:packages`) | `ghp_xxxx` |
| `EC2_SSH_HOST` | Bastion 공인 IP | `98.95.218.36` |
| `EC2_SSH_USER` | SSH 유저명 | `ec2-user` |
| `EC2_SSH_KEY` | SSH 프라이빗 키 (ed25519) | `-----BEGIN OPENSSH...` |
| `EC2_DEPLOY_HOST` | 타겟 EC2 사설 IP | `10.0.10.145` |
| `APP_GITHUB_CLIENT_ID` | Vite build arg (OAuth) | `Ov23li...` |

**PAT 생성**: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
- Repository access: `oh-my-agentic-score-cloud`
- Permissions: `Read and write` on Packages

---

### deploy-prod.sh 패턴

```bash
#!/usr/bin/env bash
# TAG=v0.5.0 ./scripts/deploy-prod.sh
set -euo pipefail
export TAG="${TAG:-latest}"

# Pull pre-built images from GHCR and restart
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --pull always
```

**핵심**: `--pull always` = GHCR에서 항상 최신 이미지 pull 후 컨테이너 재생성.

---

### Docker Compose Image Naming

```yaml
# docker-compose.yaml
services:
  my-service:
    image: ${REGISTRY:-ghcr.io/<owner>/<repo>}/my-service:${TAG:-latest}
    build:
      context: .
      dockerfile: Dockerfile
      target: prod
```

- `REGISTRY` + `TAG` 환경변수로 이미지 소스 제어
- `build:` 섹션 유지 = 로컬 `--build`도 여전히 가능 (개발용)
- CI/CD는 `--pull always`로 GHCR 이미지 사용

---

### Build Args (Vite 프론트엔드)

Vite는 `import.meta.env.*`를 **빌드 타임**에 인라인합니다.
Dockerfile에서 ARG → ENV로 전달해야 합니다:

```dockerfile
# Dockerfile
ARG VITE_API_URL=http://localhost:7778
ARG VITE_GITHUB_CLIENT_ID=

ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_GITHUB_CLIENT_ID=${VITE_GITHUB_CLIENT_ID}

RUN bun run build  # 이 시점에 ENV 값이 번들에 인라인됨
```

GitHub Actions에서:
```yaml
build-args: |
  VITE_API_URL=https://api.example.com
  VITE_GITHUB_CLIENT_ID=${{ secrets.APP_GITHUB_CLIENT_ID }}
```

---

### 롤백

```bash
# 특정 버전으로 롤백
TAG=v0.4.0 ./scripts/deploy-prod.sh

# 또는 직접
export TAG=v0.4.0
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --pull always
```

GHCR에 태그별 이미지가 보존되므로 언제든 이전 버전으로 롤백 가능.

---

### Checklist (새 프로젝트 적용 시)

1. [ ] Dockerfile에 `dev` / `build` / `prod` 멀티스테이지 구성
2. [ ] docker-compose.yaml에 `image: ${REGISTRY}/name:${TAG}` 패턴
3. [ ] docker-compose.production.yaml에 build args (VITE_* 등)
4. [ ] `.github/workflows/deploy.yml` 생성
5. [ ] GitHub Secrets 설정 (GHCR_TOKEN, EC2_SSH_*, APP_* build args)
6. [ ] deploy-prod.sh에서 `--build` → `--pull always` 변경
7. [ ] 첫 배포: `git tag v0.1.0 && git push origin v0.1.0`
8. [ ] GHCR 패키지 visibility 확인 (private repo = private package)
