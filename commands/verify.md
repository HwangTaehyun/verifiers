---
name: verify
description: 전체 검증 실행 — 모든 V01~V08 validator를 즉시 실행하여 결과 리포트
---

## 전체 검증을 실행합니다.

현재 프로젝트에 대해 모든 validator (V01~V08)를 실행하고 결과를 리포트합니다.

### 실행 방법

다음 명령을 실행하세요:

```bash
echo '{"cwd": "'$(pwd)'"}' | uv run --script ~/.claude/verifiers/hooks/stop_validator.py
```

### 결과 해석

- `{"decision": "approve"}` → 모든 검증 통과
- `{"decision": "block", "additionalContext": "..."}` → 에러 발견, additionalContext에 상세 내용

### 에러가 발견되면

각 Finding의 FIX 지시에 따라 수정하세요. 주요 검증:

| Validator | 검증 내용 |
|-----------|----------|
| V01 | env/config 동기화 |
| V02 | genqlient stale + omitempty |
| V03 | proto/connect stale + handler |
| V04 | hasura migration 무결성 |
| V05 | docker-compose 네트워크/포트 |
| V06 | go vet + build + test |
| V07 | tsc + eslint + any 검출 |
| V08 | 시크릿 + CORS + PHI |
