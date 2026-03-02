---
name: verify-hasura
description: Hasura 작업 시 migration 타임스탬프 순서, up/down 쌍, 위험 DDL, metadata 일관성 검증
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/hasura_migration.py"
          timeout: 15
---

## Hasura migration 검증이 활성화되었습니다.

`hasura/migrations/**/*.sql`, `hasura/metadata/**/*.yaml` 수정 시 자동으로 검증됩니다.

### 검증 항목
- **V04-TIMESTAMP-ORDER**: migration 디렉토리 타임스탬프 오름차순 위반
- **V04-DUPLICATE-TIMESTAMP**: 동일 타임스탬프 migration 중복
- **V04-MISSING-FILE**: up.sql 또는 down.sql 누락
- **V04-DANGEROUS-DDL**: DROP TABLE, TRUNCATE 등 위험 DDL 경고 (up.sql)
- **V04-METADATA-ORPHAN**: metadata에 테이블은 있으나 migration에 CREATE 없음
