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
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 1 ]; then
      GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RED=$'\033[0;31m'; NC=$'\033[0m'
    else
      GREEN=""; CYAN=""; DIM=""; BOLD=""; RED=""; NC=""
    fi
    start_ms=$(($(date +%s%N) / 1000000))
    SRC="{{justfile_directory()}}"
    DST="$HOME/.claude"

    echo -e "${CYAN}❯${NC} Installing verifiers → ${BOLD}~/.claude/${NC}"
    mkdir -p "$DST/hooks" "$DST/skills" "$DST/agents/team" "$DST/commands"
    ln -sfn "$SRC" "$DST/verifiers"

    SKILLS=(verify verify-env verify-docker verify-graphql verify-proto verify-hasura
            verify-go verify-ts verify-ui verify-go-test verify-ts-test verify-py-test
            verify-commit verify-cheating verify-complexity verify-deps verify-linter
            verify-input verify-mock test-classical write-business-function)
    for s in "${SKILLS[@]}"; do
      ln -sfn "$SRC/skills/$s/" "$DST/skills/$s"
    done
    echo -e "  ${GREEN}✓${NC} Skills ${DIM}(${#SKILLS[@]})${NC}"

    AGENTS=(stack-verifier ui-verifier tdd-writer)
    for a in "${AGENTS[@]}"; do
      ln -sfn "$SRC/agents/$a.md" "$DST/agents/$a.md"
    done
    ln -sfn "$SRC/agents/team/builder.md" "$DST/agents/team/builder.md"
    ln -sfn "$SRC/agents/team/validator.md" "$DST/agents/team/validator.md"
    echo -e "  ${GREEN}✓${NC} Agents ${DIM}($((${#AGENTS[@]} + 2)))${NC}"

    COMMANDS=(verify build-with-validation tdd tdd-write tdd-update)
    for c in "${COMMANDS[@]}"; do
      ln -sfn "$SRC/commands/$c.md" "$DST/commands/$c.md"
    done
    echo -e "  ${GREEN}✓${NC} Commands ${DIM}(${#COMMANDS[@]})${NC}"

    if uv run "$SRC/scripts/merge_settings.py" >/dev/null 2>&1; then
      echo -e "  ${GREEN}✓${NC} Hooks merged ${DIM}(Tier 1/2/3)${NC}"
    else
      echo -e "  ${RED}✗${NC} Hook merge failed — re-run with verbose: uv run $SRC/scripts/merge_settings.py" >&2
      exit 1
    fi

    elapsed=$(( $(date +%s%N) / 1000000 - start_ms ))
    echo -e "${BOLD}${GREEN}✓${NC} Installed in ${BOLD}${elapsed}ms${NC}"
    echo -e "  ${DIM}→ Restart Claude Code to activate.${NC}"

# Global 삭제
uninstall:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 1 ]; then
      GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; NC=$'\033[0m'
    else
      GREEN=""; CYAN=""; DIM=""; BOLD=""; NC=""
    fi
    start_ms=$(($(date +%s%N) / 1000000))
    SRC="{{justfile_directory()}}"
    DST="$HOME/.claude"

    echo -e "${CYAN}❯${NC} Uninstalling verifiers ${DIM}from ~/.claude/${NC}"
    rm -f "$DST/verifiers"
    rm -f "$DST/skills/verify" "$DST"/skills/verify-* "$DST/skills/test-classical" "$DST/skills/write-business-function"
    echo -e "  ${GREEN}✓${NC} Skills"
    rm -f "$DST/agents/stack-verifier.md" "$DST/agents/ui-verifier.md" "$DST/agents/tdd-writer.md"
    rm -f "$DST/agents/team/builder.md" "$DST/agents/team/validator.md"
    echo -e "  ${GREEN}✓${NC} Agents"
    rm -f "$DST/commands/verify.md" "$DST/commands/build-with-validation.md"
    rm -f "$DST/commands/tdd.md" "$DST/commands/tdd-write.md" "$DST/commands/tdd-update.md"
    echo -e "  ${GREEN}✓${NC} Commands"
    uv run "$SRC/scripts/unmerge_settings.py" >/dev/null 2>&1 || true
    echo -e "  ${GREEN}✓${NC} Hooks unmerged"

    elapsed=$(( $(date +%s%N) / 1000000 - start_ms ))
    echo -e "${BOLD}${GREEN}✓${NC} Uninstalled in ${BOLD}${elapsed}ms${NC}"
    echo -e "  ${DIM}→ Restart Claude Code.${NC}"

# 특정 프로젝트에 설치 (프로젝트의 .claude/ 디렉토리에 심볼릭 링크)
install-project project_dir:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 1 ]; then
      GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RED=$'\033[0;31m'; NC=$'\033[0m'
    else
      GREEN=""; CYAN=""; DIM=""; BOLD=""; RED=""; NC=""
    fi
    start_ms=$(($(date +%s%N) / 1000000))
    SRC="{{justfile_directory()}}"
    DST="{{project_dir}}/.claude"
    PROJECT_NAME=$(basename "{{project_dir}}")

    echo -e "${CYAN}❯${NC} Installing verifiers → ${BOLD}${PROJECT_NAME}${NC}"
    mkdir -p "$DST/hooks" "$DST/skills" "$DST/agents/team" "$DST/commands"
    ln -sfn "$SRC" "$DST/verifiers"

    SKILLS=(verify verify-env verify-docker verify-graphql verify-proto verify-hasura
            verify-go verify-ts verify-ui verify-go-test verify-ts-test verify-py-test
            verify-commit verify-cheating verify-complexity verify-deps verify-linter
            verify-input verify-mock test-classical write-business-function)
    for s in "${SKILLS[@]}"; do
      ln -sfn "$SRC/skills/$s/" "$DST/skills/$s"
    done
    echo -e "  ${GREEN}✓${NC} Skills ${DIM}(${#SKILLS[@]})${NC}"

    AGENTS=(stack-verifier ui-verifier tdd-writer)
    for a in "${AGENTS[@]}"; do
      ln -sfn "$SRC/agents/$a.md" "$DST/agents/$a.md"
    done
    ln -sfn "$SRC/agents/team/builder.md" "$DST/agents/team/builder.md"
    ln -sfn "$SRC/agents/team/validator.md" "$DST/agents/team/validator.md"
    echo -e "  ${GREEN}✓${NC} Agents ${DIM}($((${#AGENTS[@]} + 2)))${NC}"

    COMMANDS=(verify build-with-validation tdd tdd-write tdd-update)
    for c in "${COMMANDS[@]}"; do
      ln -sfn "$SRC/commands/$c.md" "$DST/commands/$c.md"
    done
    echo -e "  ${GREEN}✓${NC} Commands ${DIM}(${#COMMANDS[@]})${NC}"

    if uv run "$SRC/scripts/merge_settings.py" --settings-path "$DST/settings.json" >/dev/null 2>&1; then
      echo -e "  ${GREEN}✓${NC} Hooks merged ${DIM}(Tier 1/2/3 → $DST/settings.json)${NC}"
    else
      echo -e "  ${RED}✗${NC} Hook merge failed — re-run: uv run $SRC/scripts/merge_settings.py --settings-path $DST/settings.json" >&2
      exit 1
    fi

    elapsed=$(( $(date +%s%N) / 1000000 - start_ms ))
    echo -e "${BOLD}${GREEN}✓${NC} Installed in ${BOLD}${elapsed}ms${NC}"
    echo -e "  ${DIM}→ Restart Claude Code to activate.${NC}"

# 특정 프로젝트에서 삭제
uninstall-project project_dir:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -t 1 ]; then
      GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; NC=$'\033[0m'
    else
      GREEN=""; CYAN=""; DIM=""; BOLD=""; NC=""
    fi
    start_ms=$(($(date +%s%N) / 1000000))
    SRC="{{justfile_directory()}}"
    DST="{{project_dir}}/.claude"
    PROJECT_NAME=$(basename "{{project_dir}}")

    echo -e "${CYAN}❯${NC} Uninstalling verifiers ${DIM}from ${PROJECT_NAME}${NC}"
    rm -f "$DST/verifiers"
    rm -f "$DST/skills/verify" "$DST"/skills/verify-* "$DST/skills/test-classical" "$DST/skills/write-business-function"
    echo -e "  ${GREEN}✓${NC} Skills"
    rm -f "$DST/agents/stack-verifier.md" "$DST/agents/ui-verifier.md" "$DST/agents/tdd-writer.md"
    rm -f "$DST/agents/team/builder.md" "$DST/agents/team/validator.md"
    echo -e "  ${GREEN}✓${NC} Agents"
    rm -f "$DST/commands/verify.md" "$DST/commands/build-with-validation.md"
    rm -f "$DST/commands/tdd.md" "$DST/commands/tdd-write.md" "$DST/commands/tdd-update.md"
    echo -e "  ${GREEN}✓${NC} Commands"
    uv run "$SRC/scripts/unmerge_settings.py" --settings-path "$DST/settings.json" >/dev/null 2>&1 || true
    echo -e "  ${GREEN}✓${NC} Hooks unmerged"

    elapsed=$(( $(date +%s%N) / 1000000 - start_ms ))
    echo -e "${BOLD}${GREEN}✓${NC} Uninstalled in ${BOLD}${elapsed}ms${NC}"

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
