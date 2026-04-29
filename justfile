# verifiers — Reusable verification system for AI agent coding workflows
# Run `just --list` to see all available recipes

set shell := ["bash", "-cu"]

# 사용 가능한 레시피 표시
default:
    @just --list

# ═══════════════════════════════════════════
# 설치/삭제
# ═══════════════════════════════════════════

# Global 설치 (hooks + skills + agents + commands → ~/.claude/)
install:
    @echo "Installing verifiers globally..."
    # Ensure directories exist
    mkdir -p ~/.claude/hooks
    mkdir -p ~/.claude/skills
    mkdir -p ~/.claude/agents/team
    mkdir -p ~/.claude/commands
    # Verifiers base symlink (skills reference validators through this)
    ln -sf {{justfile_directory()}} ~/.claude/verifiers
    # Skills (Tier 2: 상황별 검증)
    ln -sf {{justfile_directory()}}/skills/verify/ ~/.claude/skills/verify
    ln -sf {{justfile_directory()}}/skills/verify-env/ ~/.claude/skills/verify-env
    ln -sf {{justfile_directory()}}/skills/verify-docker/ ~/.claude/skills/verify-docker
    ln -sf {{justfile_directory()}}/skills/verify-graphql/ ~/.claude/skills/verify-graphql
    ln -sf {{justfile_directory()}}/skills/verify-proto/ ~/.claude/skills/verify-proto
    ln -sf {{justfile_directory()}}/skills/verify-hasura/ ~/.claude/skills/verify-hasura
    ln -sf {{justfile_directory()}}/skills/verify-go/ ~/.claude/skills/verify-go
    ln -sf {{justfile_directory()}}/skills/verify-ts/ ~/.claude/skills/verify-ts
    ln -sf {{justfile_directory()}}/skills/verify-ui/ ~/.claude/skills/verify-ui
    ln -sf {{justfile_directory()}}/skills/verify-go-test/ ~/.claude/skills/verify-go-test
    ln -sf {{justfile_directory()}}/skills/verify-ts-test/ ~/.claude/skills/verify-ts-test
    ln -sf {{justfile_directory()}}/skills/verify-py-test/ ~/.claude/skills/verify-py-test
    ln -sf {{justfile_directory()}}/skills/verify-commit/ ~/.claude/skills/verify-commit
    ln -sf {{justfile_directory()}}/skills/verify-cheating/ ~/.claude/skills/verify-cheating
    ln -sf {{justfile_directory()}}/skills/verify-complexity/ ~/.claude/skills/verify-complexity
    ln -sf {{justfile_directory()}}/skills/verify-deps/ ~/.claude/skills/verify-deps
    ln -sf {{justfile_directory()}}/skills/verify-linter/ ~/.claude/skills/verify-linter
    ln -sf {{justfile_directory()}}/skills/verify-input/ ~/.claude/skills/verify-input
    ln -sf {{justfile_directory()}}/skills/verify-mock/ ~/.claude/skills/verify-mock
    ln -sf {{justfile_directory()}}/skills/test-classical/ ~/.claude/skills/test-classical
    # Agents
    ln -sf {{justfile_directory()}}/agents/stack-verifier.md ~/.claude/agents/stack-verifier.md
    ln -sf {{justfile_directory()}}/agents/ui-verifier.md ~/.claude/agents/ui-verifier.md
    ln -sf {{justfile_directory()}}/agents/tdd-writer.md ~/.claude/agents/tdd-writer.md
    ln -sf {{justfile_directory()}}/agents/team/builder.md ~/.claude/agents/team/builder.md
    ln -sf {{justfile_directory()}}/agents/team/validator.md ~/.claude/agents/team/validator.md
    # Commands
    ln -sf {{justfile_directory()}}/commands/verify.md ~/.claude/commands/verify.md
    ln -sf {{justfile_directory()}}/commands/build-with-validation.md ~/.claude/commands/build-with-validation.md
    ln -sf {{justfile_directory()}}/commands/tdd.md ~/.claude/commands/tdd.md
    ln -sf {{justfile_directory()}}/commands/tdd-write.md ~/.claude/commands/tdd-write.md
    ln -sf {{justfile_directory()}}/commands/tdd-update.md ~/.claude/commands/tdd-update.md
    # settings.json hook 등록 (Tier 1 + Tier 3)
    uv run {{justfile_directory()}}/scripts/merge_settings.py
    @echo "✅ Installed globally. Restart Claude Code to activate."

# Global 삭제
uninstall:
    @echo "Uninstalling verifiers..."
    rm -f ~/.claude/verifiers
    rm -f ~/.claude/skills/verify ~/.claude/skills/verify-* ~/.claude/skills/test-classical
    rm -f ~/.claude/agents/stack-verifier.md
    rm -f ~/.claude/agents/ui-verifier.md
    rm -f ~/.claude/agents/tdd-writer.md
    rm -f ~/.claude/agents/team/builder.md ~/.claude/agents/team/validator.md
    rm -f ~/.claude/commands/verify.md
    rm -f ~/.claude/commands/build-with-validation.md
    rm -f ~/.claude/commands/tdd.md ~/.claude/commands/tdd-write.md ~/.claude/commands/tdd-update.md
    uv run {{justfile_directory()}}/scripts/unmerge_settings.py
    @echo "✅ Uninstalled. Restart Claude Code."

# 특정 프로젝트에 설치 (프로젝트의 .claude/ 디렉토리에 심볼릭 링크)
install-project project_dir:
    @echo "Installing verifiers to {{project_dir}}/.claude/ ..."
    mkdir -p {{project_dir}}/.claude/hooks
    mkdir -p {{project_dir}}/.claude/skills
    mkdir -p {{project_dir}}/.claude/agents/team
    mkdir -p {{project_dir}}/.claude/commands
    ln -sf {{justfile_directory()}} {{project_dir}}/.claude/verifiers
    ln -sf {{justfile_directory()}}/skills/verify/ {{project_dir}}/.claude/skills/verify
    ln -sf {{justfile_directory()}}/skills/verify-env/ {{project_dir}}/.claude/skills/verify-env
    ln -sf {{justfile_directory()}}/skills/verify-docker/ {{project_dir}}/.claude/skills/verify-docker
    ln -sf {{justfile_directory()}}/skills/verify-graphql/ {{project_dir}}/.claude/skills/verify-graphql
    ln -sf {{justfile_directory()}}/skills/verify-proto/ {{project_dir}}/.claude/skills/verify-proto
    ln -sf {{justfile_directory()}}/skills/verify-hasura/ {{project_dir}}/.claude/skills/verify-hasura
    ln -sf {{justfile_directory()}}/skills/verify-go/ {{project_dir}}/.claude/skills/verify-go
    ln -sf {{justfile_directory()}}/skills/verify-ts/ {{project_dir}}/.claude/skills/verify-ts
    ln -sf {{justfile_directory()}}/skills/verify-ui/ {{project_dir}}/.claude/skills/verify-ui
    ln -sf {{justfile_directory()}}/skills/verify-go-test/ {{project_dir}}/.claude/skills/verify-go-test
    ln -sf {{justfile_directory()}}/skills/verify-ts-test/ {{project_dir}}/.claude/skills/verify-ts-test
    ln -sf {{justfile_directory()}}/skills/verify-py-test/ {{project_dir}}/.claude/skills/verify-py-test
    ln -sf {{justfile_directory()}}/skills/verify-commit/ {{project_dir}}/.claude/skills/verify-commit
    ln -sf {{justfile_directory()}}/skills/verify-cheating/ {{project_dir}}/.claude/skills/verify-cheating
    ln -sf {{justfile_directory()}}/skills/verify-complexity/ {{project_dir}}/.claude/skills/verify-complexity
    ln -sf {{justfile_directory()}}/skills/verify-deps/ {{project_dir}}/.claude/skills/verify-deps
    ln -sf {{justfile_directory()}}/skills/verify-linter/ {{project_dir}}/.claude/skills/verify-linter
    ln -sf {{justfile_directory()}}/skills/verify-input/ {{project_dir}}/.claude/skills/verify-input
    ln -sf {{justfile_directory()}}/skills/verify-mock/ {{project_dir}}/.claude/skills/verify-mock
    ln -sf {{justfile_directory()}}/skills/test-classical/ {{project_dir}}/.claude/skills/test-classical
    ln -sf {{justfile_directory()}}/agents/stack-verifier.md {{project_dir}}/.claude/agents/stack-verifier.md
    ln -sf {{justfile_directory()}}/agents/ui-verifier.md {{project_dir}}/.claude/agents/ui-verifier.md
    ln -sf {{justfile_directory()}}/agents/tdd-writer.md {{project_dir}}/.claude/agents/tdd-writer.md
    ln -sf {{justfile_directory()}}/agents/team/builder.md {{project_dir}}/.claude/agents/team/builder.md
    ln -sf {{justfile_directory()}}/agents/team/validator.md {{project_dir}}/.claude/agents/team/validator.md
    ln -sf {{justfile_directory()}}/commands/verify.md {{project_dir}}/.claude/commands/verify.md
    ln -sf {{justfile_directory()}}/commands/build-with-validation.md {{project_dir}}/.claude/commands/build-with-validation.md
    ln -sf {{justfile_directory()}}/commands/tdd.md {{project_dir}}/.claude/commands/tdd.md
    ln -sf {{justfile_directory()}}/commands/tdd-write.md {{project_dir}}/.claude/commands/tdd-write.md
    ln -sf {{justfile_directory()}}/commands/tdd-update.md {{project_dir}}/.claude/commands/tdd-update.md
    # settings.json hook 등록 (Tier 1 + Tier 3)
    uv run {{justfile_directory()}}/scripts/merge_settings.py --settings-path {{project_dir}}/.claude/settings.json
    @echo "✅ Installed to {{project_dir}}. Restart Claude Code to activate."

# 특정 프로젝트에서 삭제
uninstall-project project_dir:
    @echo "Uninstalling verifiers from {{project_dir}}/.claude/ ..."
    rm -f {{project_dir}}/.claude/verifiers
    rm -f {{project_dir}}/.claude/skills/verify {{project_dir}}/.claude/skills/verify-* {{project_dir}}/.claude/skills/test-classical
    rm -f {{project_dir}}/.claude/agents/stack-verifier.md
    rm -f {{project_dir}}/.claude/agents/ui-verifier.md
    rm -f {{project_dir}}/.claude/agents/tdd-writer.md
    rm -f {{project_dir}}/.claude/agents/team/builder.md {{project_dir}}/.claude/agents/team/validator.md
    rm -f {{project_dir}}/.claude/commands/verify.md
    rm -f {{project_dir}}/.claude/commands/build-with-validation.md
    rm -f {{project_dir}}/.claude/commands/tdd.md {{project_dir}}/.claude/commands/tdd-write.md {{project_dir}}/.claude/commands/tdd-update.md
    uv run {{justfile_directory()}}/scripts/unmerge_settings.py --settings-path {{project_dir}}/.claude/settings.json
    @echo "✅ Uninstalled from {{project_dir}}."

# ═══════════════════════════════════════════
# 검증 실행
# ═══════════════════════════════════════════

# 전체 검증 실행 (현재 디렉토리 기준)
verify:
    echo '{"cwd": "'"$(pwd)"'"}' | uv run --script {{justfile_directory()}}/hooks/stop_validator.py

# 특정 validator만 실행
verify-one name:
    echo '{"cwd": "'"$(pwd)"'"}' | uv run --script {{justfile_directory()}}/hooks/run_single.py {{name}}

# ═══════════════════════════════════════════
# 개발
# ═══════════════════════════════════════════

# 테스트 실행
test:
    cd {{justfile_directory()}} && uv run pytest tests/ -v

# 린트
lint:
    cd {{justfile_directory()}} && uv run ruff check .

# 포맷
format:
    cd {{justfile_directory()}} && uv run ruff format .

# 의존성 설치
setup:
    cd {{justfile_directory()}} && uv sync

# 로그 확인
logs:
    tail -f {{justfile_directory()}}/logs/*.jsonl

# 로그 초기화
clean-logs:
    rm -f {{justfile_directory()}}/logs/*.jsonl
    rm -f {{justfile_directory()}}/logs/.gen-hash-cache.json
    @echo "✅ Logs cleaned."
