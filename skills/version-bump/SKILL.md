---
name: version-bump
description: "GitHub 프로젝트 버저닝 — git tag 기반 semver bump, commit, tag, push 자동화. 'bump', 'version', 'tag', 'release' 키워드에 반응."
---

## Version Bump Skill

Git tag 기반 Semantic Versioning (SemVer) 워크플로우입니다.
커밋, 태그 생성, push를 자동화합니다.

---

### 워크플로우

#### Step 1: 현재 버전 확인

```bash
git tag --sort=-v:refname | head -5
```

최신 `vX.Y.Z` 태그를 찾습니다. 태그가 없으면 `v0.0.0`에서 시작합니다.

#### Step 2: 변경 사항 분석

```bash
git status -u
git diff --stat HEAD
git log --oneline $(git describe --tags --abbrev=0 2>/dev/null || echo HEAD~5)..HEAD
```

마지막 태그 이후의 커밋들을 분석하여 bump 유형을 결정합니다.

#### Step 3: Bump 유형 결정

| Bump Type | When | Example |
|-----------|------|---------|
| **major** (`X+1.0.0`) | Breaking changes, API 호환성 깨짐 | `v1.0.0 → v2.0.0` |
| **minor** (`X.Y+1.0`) | 새로운 기능 추가, 하위 호환 유지 | `v1.0.0 → v1.1.0` |
| **patch** (`X.Y.Z+1`) | 버그 수정, 문서 수정, 소소한 변경 | `v1.0.0 → v1.0.1` |

**커밋 메시지 기반 자동 판단:**
- `feat:` → minor bump
- `fix:`, `docs:`, `chore:`, `style:`, `refactor:` → patch bump
- `BREAKING CHANGE` 또는 `feat!:` → major bump
- 사용자가 명시적으로 bump 유형을 지정하면 그것을 따릅니다

#### Step 4: 스테이징 및 커밋

변경 사항이 있으면 커밋합니다:

```bash
# 1. 변경 파일 확인
git status

# 2. 관련 파일만 선택적으로 스테이징 (git add -A 금지)
git add <specific-files>

# 3. Conventional Commit 메시지로 커밋
git commit -m "$(cat <<'EOF'
<type>: <concise description>

<optional body with details>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

#### Step 5: 태그 생성

```bash
git tag -a v<NEW_VERSION> -m "v<NEW_VERSION>: <summary of changes>"
```

**태그 메시지 규칙:**
- `v` prefix 필수 (예: `v0.4.0`, NOT `0.4.0`)
- 콜론 뒤에 간결한 변경 요약

#### Step 6: Push

```bash
# 커밋 push (로컬 브랜치 → 리모트 브랜치 매핑 확인)
git push origin <local-branch>:<remote-branch>

# 태그 push
git push origin v<NEW_VERSION>
```

**주의:** `master → main` 매핑 등 로컬/리모트 브랜치 이름이 다를 수 있으므로 반드시 확인합니다.

#### Step 7: 결과 보고

```
## Version Bump 완료

| 항목 | 값 |
|------|---|
| Previous | v<OLD> |
| New | v<NEW> |
| Bump Type | major/minor/patch |
| Commit | <short-hash> |
| Branch | <local> → origin/<remote> |
| Tag | v<NEW> |
```

---

### 안전 규칙

1. **커밋 전 확인**: 스테이징할 파일을 명시적으로 선택 (`.env`, credentials 제외)
2. **태그 중복 방지**: 이미 존재하는 태그와 동일한 버전 금지
3. **Force push 금지**: `--force` 옵션 사용하지 않음
4. **Hook 존중**: `--no-verify` 사용하지 않음
5. **브랜치 확인**: push 전 로컬/리모트 브랜치 매핑 확인

### 사용 예시

```
# 사용자 요청 예시
"bump commit tag push"        → 자동으로 bump 유형 판단
"patch bump하고 push해줘"     → patch bump 강제
"v1.0.0으로 릴리즈해줘"       → 특정 버전으로 태그
"커밋하고 태그 달아줘"        → 현재 변경사항 커밋 + 태그
```
