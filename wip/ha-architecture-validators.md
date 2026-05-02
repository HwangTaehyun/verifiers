# HA / 좋은 서버 구조 — 검증 가능성 분석 + Phase 72+ 후보

> Status: **draft, not implemented**. 사용자 (taehyun) 의 2026-05-02 세션 요청에서
> 출발. "API handler ≠ msg publisher ≠ consumer 분리" 같은 HA 결정을 verifier 가
> 검증할 수 있게 하자는 아이디어. 구현 전에 적정선을 먼저 정리.

---

## 1. 사용자 예시의 정체 — "Failure Domain Isolation"

> "api handler 쪽 코드는 절대 죽으면 안되서, msg publisher 랑 다른 서버로 뜨는게 제일 좋고, 또 consumer 는 당연히 마찬가지고"

이건 단일 카테고리 결정: **배포 단위 (binary / container / pod) 별 단일 책임**.

같은 family 의 다른 결정들:

| 카테고리 | 예시 결정 |
|---------|----------|
| A. Failure isolation | 1 binary = 1 role. API ≠ publisher ≠ consumer ≠ scheduler |
| B. Replication | prod replicas ≥ 2. multi-AZ. DB read-replica |
| C. Graceful degradation | Circuit breaker, timeout-everywhere, bulkhead, retry+backoff |
| D. Health & lifecycle | /livez ≠ /readyz, terminationGracePeriodSeconds, drain on SIGTERM |
| E. State management | Stateless API, externalized session, idempotent ops |
| F. Observability | Distributed tracing, correlation IDs, SLO 메트릭 |
| G. MQ patterns | Manual ack, DLQ, idempotent producer |
| H. Resource governance | CPU/memory limits, HPA, PodDisruptionBudget |

---

## 2. 현재 verifier 가 이미 커버하는 부분 (6-8 룰)

| V-ID | HA 카테고리 |
|------|------------|
| **V25** go-multibinary | A. graceful shutdown + tools.go |
| **V26** docker-prod-hardening | C/D/H. resource limits + dev flags 금지 + CORS hardening |
| **V36** go-http-hardening | C. HTTP server timeouts + graceful shutdown |
| **V49** OTel-instrumentation | F. OpenTelemetry SDK presence |
| **V50** health-endpoint-split | D. /livez vs /readyz |
| **V56** prometheus-metrics-endpoint | F. /metrics endpoint |

→ "0 → 1" 이 아니라 **보강** 작업. 새 룰들은 기존 그룹과 자연스럽게 묶임.

---

## 3. MVP — 사용자 예시 직접 매핑하는 2 개 룰

### V60-CMD-SINGLE-RESPONSIBILITY (Go cmd binary 분리)

**검출 대상**: `cmd/<bin>/main.go` 가 다음 카테고리 중 2 개 이상 import?

```
internal/handlers/...     ← API
internal/consumer/...     ← queue consumer
internal/publisher/...    ← queue publisher
internal/scheduler/...    ← cron / job
```

**위반 예**:

```go
// cmd/server/main.go — BAD
package main
import (
    "myapp/internal/handlers"  // HTTP API
    "myapp/internal/consumer"  // Kafka consumer
)
// 하나의 binary, API 가 죽으면 consumer 도 죽음
```

**추천**:

```
cmd/api/main.go         ← HTTP only
cmd/consumer/main.go    ← Kafka only
cmd/publisher/main.go   ← outbox processor
```

**file_patterns**: `cmd/**/main.go`
**구현 비교**: V25 (go-multibinary) 와 매우 비슷한 패턴, ~80 LOC.
**탐지 방식**: import 라인 정규식 매칭 + role-bucket 분류.
**설정 가능성**: `.verifiers/config.yaml` 에 `ha.role_dirs:` 키로 디렉토리 매핑 override (예: `internal/api` 도 handler 로 인정).

---

### V61-COMPOSE-SERVICE-SINGLE-ROLE (docker-compose 분리)

**검출 대상**: 하나의 service 가 `ports:` + queue env 둘 다?

**위반 예**:

```yaml
# docker-compose.yaml — BAD
services:
  app:
    ports: ["8080:8080"]                    # HTTP 노출
    environment:
      - KAFKA_CONSUMER_GROUP=xyz             # + 큐 구독
```

**추천**:

```yaml
services:
  api:
    ports: ["8080:8080"]
    command: ["./cmd/api"]
  consumer:
    command: ["./cmd/consumer"]              # 같은 image, 다른 command
```

**file_patterns**: `**/docker-compose*.yaml`, `**/docker-compose*.yml`
**구현 비교**: V05 (docker-compose) 가 이미 yaml 파싱 — extend 만, ~40 LOC.
**탐지 방식**: yaml 파싱 → service 별로 (port 노출 boolean) × (큐 env 키워드 존재 boolean) 둘 다 true 면 finding.
**큐 env 키워드 셋**: `KAFKA_*`, `RABBITMQ_*`, `PUBSUB_*`, `SQS_*`, `NATS_*`, `*_CONSUMER_GROUP`, `*_PUBLISHER_*`.

---

→ **V60 + V61 두 개로 사용자 명시 결정 (API ≠ publisher ≠ consumer) 완전 커버**.

---

## 4. K8s 사용 시 — 조건부 활성 룰 (Phase 73)

K8s 디렉토리 (`k8s/`, `helm/`, `manifests/`) 자동 감지 시만 활성. Hasura 자동 감지 패턴 재활용.

### V62-K8S-DEPLOY-SINGLE-ROLE
- Deployment container 가 ingress + queue 둘 다 받으면 finding.
- yaml 파싱 + container args / env 분석.

### V63-PROD-REPLICAS-GE-2
- `k8s/prod/` 또는 `manifests/prod/` 의 `replicas: 1` 검출.
- Severity: warning (의도적 singleton 인 경우 주석으로 silence 가능).

### V64-K8S-MISSING-PROBE
- container 에 `livenessProbe` / `readinessProbe` 없음.
- V50 (/livez/readyz split) 와 보강 관계.

### V65-PDB-PRESENT-IF-MULTI-REPLICA
- `replicas > 1` 인데 PodDisruptionBudget 없음.
- Cross-file yaml 분석 (Deployment ↔ PDB selector 매칭).

---

## 5. 검출 불가능한 영역 — verifier 가 **안 해야** 할 것

- ❌ "이 서비스가 진짜 HA 필요한가?" — 비즈니스 결정
- ❌ "현재 트래픽 기준 replicas=2 가 적절한가?" — 운영 / HPA 영역
- ❌ "이 큐가 정말 manual ack 필요한가?" — 메시지 재처리 정책
- ❌ "circuit breaker threshold = 50% 가 맞나?" — 부하 테스트 영역
- ❌ "DLQ 처리 빈도" — 운영 SLO 영역

→ 이런 건 **SRE / 아키텍트 review 영역**. verifier 가 대체 시도하면 false positive 폭증 + 신뢰성 잃음.

**verifier 의 적정선**: 정적 분석으로 명확히 잡을 수 있는 HA **안티패턴** 차단.

---

## 6. Over-engineering 경계선

❌ **하지 말아야 할 것**:
- "Saga pattern 사용 강제"
- "이벤트 소싱 강제"
- "CQRS 강제"
- "Hexagonal architecture 강제"
- 13+ 개 신규 룰 한 번에 도입

→ 팀 / 서비스 규모에 따라 다르고, verifier 가 강제하면 **마찰 비용 > 가치**.

✅ **해야 할 것**:
- 실제 위반 시 **장애로 직결되는** 안티패턴만
- 사용자 자신의 운영 경험에서 나온 결정
- 기존 그룹과 묶을 수 있는 좁은 범위

---

## 7. 권장 단계 — 점진적 도입

### Phase 72 (MVP)
- ✅ V60-CMD-SINGLE-RESPONSIBILITY (Go)
- ✅ V61-COMPOSE-SERVICE-SINGLE-ROLE (docker-compose)
- 새 group `ha-architecture` 생성, V25 / V36 / V50 / V60 / V61 묶음
- 새 V## 등록 + `BUILTIN_GROUPS["ha-architecture"]` 추가
- `lib/config_loader.py` 의 7 카테고리 표 갱신

### Phase 73 (K8s 적용 시 — 조건부 활성)
- V62-V65 (Deployment 단일 역할 + replicas + probe + PDB)
- ProjectContext 에 `kubernetes_dir` cached_property 추가 (Hasura detect 패턴)

### Phase 74+ (실제 필요성 확인 후)
- MQ 패턴 (V66+: manual ack, DLQ, idempotent producer)
- 시기상조면 보류

---

## 8. 결정 트리 — 새 룰을 추가할까 말까?

룰 도입 전 self-check:

1. **장애로 직결되나?** — yes 면 +1, no 면 −1
2. **정적 분석으로 명확히 검출 가능한가?** — yes 면 +1, heuristic 만 가능하면 0
3. **false positive 율이 낮을 거라 확신할 수 있나?** — yes 면 +1, 모르면 0
4. **사용자가 silence 할 수 있는 escape (config / 주석) 가 있나?** — yes 면 +1
5. **기존 룰셋과 자연스럽게 묶이나?** — yes 면 +1, 외톨이면 −1

→ score ≥ 3 만 통과. V60/V61 은 둘 다 score 4-5.

---

## 9. 사용자 결정 기다리는 항목

- [ ] Phase 72 (V60 + V61) 진행 의사
- [ ] V60 의 role-dir 매핑이 사용자 프로젝트 컨벤션과 맞는지 확인
  - ax-finance-project 의 cmd/ 디렉토리 구조 확인 필요
  - 다른 모노레포에서 다른 컨벤션 (`apps/`, `services/`) 쓰는지?
- [ ] V61 의 큐 env 키워드 셋 확장이 필요한지 (Pulsar, Redis Streams, etc.)
- [ ] K8s 룰 (Phase 73) 의 우선순위
- [ ] MQ 룰 (Phase 74+) 보류 vs 즉시 진행

---

## Appendix — 참고할 산업 표준

- **Google SRE Book**, Ch. 22 (Addressing Cascading Failures): https://sre.google/sre-book/addressing-cascading-failures/
- **AWS Well-Architected Framework**, Reliability Pillar: https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html
- **12-Factor App**, esp. VI (Processes), VIII (Concurrency), IX (Disposability): https://12factor.net/
- **Kubernetes Production Readiness Checklist** (CNCF): https://github.com/mercari/production-readiness-checklist
- **Microservices Patterns** by Chris Richardson, Ch. 4 (Saga), Ch. 7 (External API): ISBN 978-1617294549

이 docs 들의 공통 결론: HA 는 **인프라 + 코드 + 운영** 3 자 협업. verifier 는 그 중 코드/설정 부분의 정적 분석 가능한 안티패턴만 책임.

---

## 변경 이력

- 2026-05-02 — 초안 작성 (Phase 71 ship 후 사용자 요청)
