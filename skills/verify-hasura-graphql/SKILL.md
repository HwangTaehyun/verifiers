---
name: verify-hasura-graphql
description: Hasura가 프로젝트에 있을때, raw sql을 사용하지 않고 무조건 graphql을 쓰도록 검사하는 skill
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/hasura_graphql_enforcement.py"
          timeout: 10
---

## Hasura GraphQL enforcement 검증이 활성화되었습니다.

Hasura가 있는 프로젝트에서 Go 서비스 파일 수정 시 raw SQL 대신 GraphQL 사용을 강제 검증합니다.

### 검증 항목
- **V20-HASURA-FOUND**: 프로젝트에 Hasura 설정이 감지됨 (hasura/, docker-compose.yaml에서 hasura 이미지)
- **V20-RAW-SQL-FORBIDDEN**: Hasura 프로젝트에서 raw SQL 사용 금지 (sql.DB 사용, .Query*, .Exec* 호출)
- **V20-MISSING-GRAPHQL**: GraphQL 클라이언트가 서비스에서 사용되지 않음 (gqlClient 필드 누락)
- **V20-SQL-IMPORT**: database/sql 패키지 import 감지 (GraphQL로 대체해야 함)

### 허용되는 예외
- Migration 파일 (`**/migrations/**/*.sql`)
- Test 파일 (`**/*_test.go`)
- Mock/Setup 코드 (`**/mocks/**`, `**/setup/**`)

### GraphQL 우선 정책
Hasura가 있는 프로젝트에서는:
1. 모든 database 연산은 GraphQL을 통해 수행
2. genqlient 생성 코드 사용 권장
3. raw SQL은 migration에서만 허용
