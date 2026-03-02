---
name: verify-linter
description: 프로젝트의 린터 설정이 올바르게 구성되어 있는지 검증 (golangci-lint, ruff, ESLint)
---

## Linter Config Guard가 활성화되었습니다.

프로젝트의 린터 설정 파일을 분석하여 필수 규칙이 활성화되어 있는지 검증합니다.

### 검증 항목
- **V16-NO-LINTER-CONFIG**: 프로젝트에 린터 설정 파일 없음 (warning)
  - Go: `.golangci.yml` 없음
  - Python: `ruff.toml` 또는 `pyproject.toml [tool.ruff]` 없음
  - TS: `eslint.config.js` 또는 `.eslintrc.*` 없음
- **V16-MISSING-ERROR-RULES**: 에러 처리 린터 규칙 비활성화 (warning)
  - Go: `errcheck` disabled
  - Python: `E722` (bare except) ignored
  - TS: `no-empty` disabled
- **V16-MISSING-UNUSED-RULES**: 미사용 코드 감지 규칙 비활성화 (warning)
  - Go: `unused` disabled
  - Python: `F401` (unused import) ignored
  - TS: `no-unused-vars` disabled
- **V16-MISSING-SECURITY-RULES**: 보안 린터 규칙 비활성화 (warning)
  - Go: `gosec` disabled
  - Python: Bandit `S` rules not selected
  - TS: `no-eval` disabled

### 실행
```bash
echo '{"cwd": "'$(pwd)'"}' | uv run --script ~/.claude/verifiers/hooks/validators/linter_config_guard.py
```
