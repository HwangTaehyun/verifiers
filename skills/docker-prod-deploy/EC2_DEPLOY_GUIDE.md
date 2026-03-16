# EC2 Docker Compose Production Deployment Guide

> OMAS Cloud 프로젝트의 EC2 인스턴스 Docker Compose 프로덕션 배포 가이드

## Architecture

```
Internet → ALB (443, ACM TLS)
              │
              ▼
         EC2 Instance
              │
    ┌─────────┴─────────┐
    │                    │
Port 80              Port 81
ai-assistant         OMAS Cloud
(traefik)            (omas-traefik)
    │                    │
    ▼                    ├── omas-web (Nginx+SPA)
 anima-*                 ├── omas-server (Go Connect-RPC)
 containers              ├── omas-hasura (GraphQL Engine)
                         └── omas-postgres (PostgreSQL 18)
```

**Key Design Decisions:**
- **Port 81**: OMAS uses port 81 (not 80) because ai-assistant's Traefik already occupies port 80
- **Separate networks**: `omas_traefik` / `omas_network` (NOT sharing ai-assistant's `traefik` network)
- **Explicit project names**: `name: omas` / `name: omas-web` / `name: omas-proxy` to avoid Docker Compose project name collisions (both projects have a `server/` directory)
- **External networks**: All compose files declare networks as `external: true` — deploy script creates them

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Docker | v29+ with Compose v5+ |
| Git | SSH key with GitHub access |
| Disk | ~2GB for images |
| ALB | Routes `oh-my-agentic-score.com` → port 81 |
| DNS | Route53 → ALB for DOMAIN and API_DOMAIN |

## Directory Structure (EC2)

```
~/oh-my-agentic-score-cloud/
├── .env                          # Production secrets (NOT in git)
├── docker-compose.yaml           # omas-traefik (port 81)
├── scripts/deploy-prod.sh        # One-click deploy script
├── server/
│   ├── .env -> ../.env           # Symlink to root .env
│   ├── docker-compose.yaml       # postgres + hasura + omas-server
│   ├── docker-compose.production.yaml  # Production overrides
│   └── docker/omas-server.Dockerfile   # Multi-stage Go build
└── web/
    ├── .env -> ../.env           # Symlink to root .env
    ├── Dockerfile                # Multi-stage React build
    ├── docker-compose.yaml       # omas-web (Nginx)
    └── docker-compose.production.yaml  # Production overrides
```

## Initial Setup (First Time)

### 1. Clone Repository

```bash
ssh anima
cd ~
git clone git@github.com:HwangTaehyun/oh-my-agentic-score-cloud.git
cd oh-my-agentic-score-cloud
```

### 2. Create Production .env

```bash
# Generate secure secrets
JWT_ACCESS=$(openssl rand -hex 32)
JWT_REFRESH=$(openssl rand -hex 32)
DB_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 32)
HASURA_TOKEN=$(openssl rand -hex 32)

cat > .env << EOF
# ============================================================
# OMAS Cloud — Production Environment Variables
# ============================================================

# -- Deployment --
TAG=latest
REGISTRY=ghcr.io/hwangtaehyun/oh-my-agentic-score-cloud

# -- Domain --
DOMAIN=oh-my-agentic-score.com
API_DOMAIN=api.oh-my-agentic-score.com

# -- JWT Secrets --
APP_JWT_ACCESS_TOKEN_SECRET=${JWT_ACCESS}
APP_JWT_REFRESH_TOKEN_SECRET=${JWT_REFRESH}

# -- Database --
APP_DATABASE_USER=omas
APP_DATABASE_PASSWORD=${DB_PASS}
APP_DATABASE_DBNAME=omas
APP_DATABASE_HOST=postgres

# -- Hasura --
APP_HASURA_ADMIN_TOKEN=${HASURA_TOKEN}
APP_HASURA_GRAPHQL_ENDPOINT=http://hasura:8080/v1/graphql

# -- GitHub OAuth --
APP_GITHUB_CLIENT_ID=<your-github-client-id>
APP_GITHUB_CLIENT_SECRET=<your-github-client-secret>

# -- Dev mode --
APP_DEV=false
EOF
```

### 3. Create Symlinks

```bash
ln -sf ../.env server/.env
ln -sf ../.env web/.env
```

### 4. Deploy

```bash
bash scripts/deploy-prod.sh
```

## Deployment Commands

### Full Deploy (Recommended)

```bash
cd ~/oh-my-agentic-score-cloud
bash scripts/deploy-prod.sh
```

This runs in order:
1. Creates Docker networks (`omas_network`, `omas_traefik`)
2. Builds & starts server stack (Postgres → Hasura → Go API)
3. Starts Traefik reverse proxy (port 81)
4. Builds & starts web frontend (Nginx + React SPA)

### Manual Deploy (Step by Step)

```bash
cd ~/oh-my-agentic-score-cloud

# 1. Create networks
docker network create omas_network 2>/dev/null || true
docker network create omas_traefik 2>/dev/null || true

# 2. Server stack
cd server
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --build
cd ..

# 3. Traefik
docker compose up -d

# 4. Web
cd web
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --build
cd ..
```

### Update & Redeploy

```bash
cd ~/oh-my-agentic-score-cloud
git pull origin main
bash scripts/deploy-prod.sh
```

### Redeploy Single Service

```bash
# Server only
cd server
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --build omas-server

# Web only
cd web
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d --build
```

### Stop All OMAS Services

```bash
cd ~/oh-my-agentic-score-cloud
cd web && docker compose -f docker-compose.yaml -f docker-compose.production.yaml down && cd ..
docker compose down
cd server && docker compose -f docker-compose.yaml -f docker-compose.production.yaml down && cd ..
```

## Health Checks

```bash
# All containers
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep omas

# API health
curl -s http://localhost:81/health -H 'Host: oh-my-agentic-score.com'

# Web SPA
curl -s http://localhost:81/ -H 'Host: oh-my-agentic-score.com' | head -5

# Logs
docker logs omas-server --tail 50 -f
docker logs omas-web --tail 50 -f
docker logs omas-traefik --tail 50 -f
```

## Docker Compose Layering

Each service uses 3-layer compose:

| Layer | File | Purpose |
|-------|------|---------|
| **Base** | `docker-compose.yaml` | Service definitions, build context, env vars with defaults |
| **Dev Override** | `docker-compose.override.yaml` | Auto-loaded by `docker compose up`. Vite HMR, Air hot reload, host ports |
| **Production** | `docker-compose.production.yaml` | Explicit `-f` flag. Disable dev mode, restrict CORS, remove ports, production Traefik labels |

**Dev** (auto): `docker compose up -d` (loads base + override automatically)
**Prod** (explicit): `docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d`

## Network Architecture

```
omas_traefik (external, created by deploy script)
├── omas-traefik     (Traefik reverse proxy)
├── omas-server      (Go API)
├── omas-hasura      (GraphQL)
└── omas-web         (Nginx SPA)

omas_network (external, created by deploy script)
├── omas-traefik     (needs access to backend)
├── omas-postgres    (database)
├── omas-hasura      (needs postgres)
├── omas-server      (needs postgres + hasura)
└── omas-web         (needs omas-server via nginx proxy)
```

## Troubleshooting

### "network not found" Error
```bash
docker network create omas_network 2>/dev/null || true
docker network create omas_traefik 2>/dev/null || true
```

### Project Name Collision with ai-assistant
All OMAS compose files have explicit `name:` keys (`omas`, `omas-web`, `omas-proxy`). If you see ai-assistant containers being affected, verify the `name:` key exists at the top of each compose file.

### Hasura Migration Fails
```bash
docker logs omas-hasura --tail 100
# If migration error, check: server/hasura/migrations/
```

### Web Build Fails — Missing gen/ Files
Generated Connect-RPC code must be in git. If missing:
```bash
# On dev machine
cd server && make proto-gen
cd ../web && bun run generate:buf
git add -f server/gen/ web/src/api/gen/
git commit -m "chore: update generated proto code"
git push origin main
```

### Port 81 Not Responding
```bash
# Check Traefik is running
docker logs omas-traefik --tail 20

# Check ALB target group health
# ALB must route to port 81 on this EC2 instance
```
