---
name: verify-deps
description: Clean Architecture 의존성 방향 검증 — 내부 레이어가 외부 레이어를 import하면 차단 (Uncle Bob 원칙)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/dependency_guard.py"
          timeout: 15
---

## Dependency Direction Guard가 활성화되었습니다.

`*.go`, `*.py`, `*.ts`, `*.tsx` 수정 시 자동으로 import 방향을 검증합니다.

### 검증 항목
- **V15-WRONG-DEPENDENCY**: 내부 레이어가 외부 레이어를 import (error)
- **V15-CIRCULAR-IMPORT**: 패키지/모듈 간 순환 참조 (error)
- **V15-LAYER-SKIP**: 레이어를 건너뛴 의존 (warning)

### 기본 레이어 규칙
**Go**: domain(0) < repository(1) < service(2) < handler(3) < cmd(4)
**TypeScript**: types(0) < utils(1) < hooks(2) < components(3) < pages(4) < app(5)
**Python**: models(0) < repositories(1) < services(2) < views(3) < cli(4)

### 커스텀 설정
`.verifiers/layers.yaml` 파일로 프로젝트별 레이어 규칙을 정의할 수 있습니다:
```yaml
go:
  layers:
    domain: 0
    repository: 1
    service: 2
    handler: 3
    cmd: 4
```
