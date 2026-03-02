---
name: verify-input
description: 함수 작성 시 입력 검증 및 보안 패턴 체크리스트 (SQL injection, XSS, Path traversal, SSRF)
---

## Input Validation Guard가 활성화되었습니다.

함수를 작성하거나 수정할 때 아래 체크리스트를 확인하세요.

### 1. SQL Injection 방지
- [ ] SQL 쿼리에 문자열 포매팅 (`f"SELECT ... {user_input}"`) 사용하지 않음
- [ ] Parameterized queries / prepared statements 사용 (`?`, `$1`, `:param`)
- [ ] ORM 사용 시 raw query에 사용자 입력 직접 삽입하지 않음

**Bad:**
```python
query = f"SELECT * FROM users WHERE name = '{name}'"  # SQL injection!
```

**Good:**
```python
cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
```

### 2. XSS (Cross-Site Scripting) 방지
- [ ] `dangerouslySetInnerHTML` (React) 사용하지 않음 (불가피 시 DOMPurify 사용)
- [ ] `template.HTML()` (Go) 사용 시 반드시 sanitize
- [ ] `innerHTML` 직접 설정하지 않음
- [ ] 사용자 입력을 HTML에 삽입 시 반드시 escape

### 3. Path Traversal 방지
- [ ] 사용자 입력으로 파일 경로를 직접 구성하지 않음
- [ ] `../` 패턴 필터링 또는 `filepath.Clean()` / `os.path.realpath()` 사용
- [ ] 허용된 디렉토리 범위 검증 (chroot 또는 prefix 확인)

**Bad:**
```python
path = f"/uploads/{user_filename}"  # Path traversal!
```

**Good:**
```python
safe_name = os.path.basename(user_filename)
path = os.path.join("/uploads", safe_name)
assert os.path.realpath(path).startswith("/uploads/")
```

### 4. SSRF (Server-Side Request Forgery) 방지
- [ ] 사용자 입력 URL로 HTTP 요청하지 않음 (불가피 시 allowlist 사용)
- [ ] 내부 IP (127.0.0.1, 10.x, 172.16.x, 192.168.x) 필터링
- [ ] DNS rebinding 공격 방어 (resolve 후 IP 확인)

### 5. 함수 파라미터 유효성 검사
- [ ] 필수 파라미터 null/nil/None 체크
- [ ] 문자열 길이 제한 (max length)
- [ ] 숫자 범위 검증 (min/max)
- [ ] enum 값 검증 (허용된 값 목록)
- [ ] 배열/슬라이스 크기 제한

### 6. Context Validation
- [ ] 인증/인가 확인 후 데이터 접근
- [ ] Rate limiting 적용 여부
- [ ] 멱등성 (idempotency) 보장 여부
- [ ] 동시성 (concurrency) 안전성

### 적용 가이드
이 스킬은 자동 검증이 아닌 **가이드**입니다. 함수를 작성한 후 위 체크리스트를 스스로 확인하세요.
특히 사용자 입력을 직접 받는 API 핸들러, 폼 처리 함수, 파일 업로드 핸들러에서 중요합니다.
