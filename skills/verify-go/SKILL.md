---
name: verify-go
description: Go 작업 시 go vet, gofmt, go build 검증 (PostToolUse는 빠른 검사만, Stop에서 golangci-lint + test)
hooks:
  PostToolUse:
    - matcher: "Edit|Write|MultiEdit"
      hooks:
        - type: command
          command: "uv run --script ~/.claude/verifiers/hooks/validators/go_quality.py"
          timeout: 30
---

## Go 코드 검증이 활성화되었습니다.

`*.go`, `go.mod`, `go.sum` 수정 시 자동으로 검증됩니다.

### PostToolUse 검사 (빠른, <5초)
- **V06-GO-VET**: 의심스러운 코드 패턴 (unreachable code, 잘못된 printf 등)
- **V06-GOFMT**: 포맷 검사 (Go 커뮤니티 표준)
- **V06-BUILD-FAIL**: 컴파일 에러

### Stop hook 검사 (느린, 포괄적 — Tier 3에서 자동)
- **V06-LINT-***: golangci-lint 종합 코드 품질 (50+ 린터)
- **V06-TEST-FAIL**: 테스트 실패 (go test -race)
