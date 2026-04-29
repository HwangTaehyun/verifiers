# V13 — ai-cheating-guard

> **Owner**: `hooks/validators/ai_cheating_guard.py`
> **Tier**: 2 (PostToolUse) only — the rule semantics require comparing the Edit's `old_string` vs `new_string` (or the Write's complete content vs nothing); Stop has no equivalent signal.
> **File patterns**: `**/*_test.go`, `**/test_*.py`, `**/*.test.ts`, `**/*.test.tsx`, `**/*.spec.*`, `**/__tests__/**`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V13-TEST-DELETED` | error | The Edit reduced the count of `func Test*` (Go) / `def test_*` (Python) / `it()` (JS) in the file. AI deleted a passing/failing test instead of fixing it. |
| `V13-TEST-DISABLED` | error | New use of `t.Skip(...)`, `@pytest.mark.skip`, `it.skip(...)`, or `xit(...)` introduced by this Edit. |
| `V13-ASSERTION-REMOVED` | error | The total count of `assert*` (Python), `require.*`/`assert.*` (Go), or `expect(...)` calls dropped between old and new content. |
| `V13-TEST-WEAKENED` | error | A specific assertion downgraded from a strict form to a loose form: `assertEqual` → `assertTrue`, `toEqual` → `toBeTruthy`, `require.Equal` → `require.NotNil`. |
| `V13-MOCK-EVERYTHING` | warning | A test function contains > 5 `jest.mock()` / `unittest.mock.patch` / `gomock.NewController` calls — at that density, the test is mocking the system rather than testing it. |
| `V13-TRIVIAL-TEST` | warning | An assertion that's tautologically true (`assert True`, `expect(true).toBe(true)`, `require.Equal(t, 1, 1)`) — a placeholder that AI agents leave behind when "writing a test". |

## Why this verifier exists

Kent Beck's hard-won observation: **AI agents that can't pass a test will, given the chance, change the test instead of the code**. V13 closes that escape route mechanically, not by prompt.

Specific patterns observed in agent traces:

1. **Test deletion.** Test fails → AI removes the test ("not needed for this feature").
2. **Skip annotation.** Test fails → AI adds `@pytest.mark.skip(reason="flaky")`.
3. **Assertion removal.** `assert result == expected` → `assert result` (silent passes for any truthy value).
4. **Strict → loose.** `assertEqual(got, want)` → `assertTrue(got is not None)`.
5. **Over-mocking.** Real failures get mocked away; the test now verifies the mocks, not the system.
6. **Trivial.** `def test_thing(): assert True` — counts as a test for coverage tools but tests nothing.

V13 inspects the *delta* between Edit's old / new — the only place this pattern is detectable. By the time the file lands on disk and Stop runs, the evidence (the deleted assertion) is gone.

## Design rationale

- **PostToolUse only.** The Edit hook gives V13 access to `tool_input.old_string` and `tool_input.new_string`. Stop cannot reconstruct the diff at hook-time — git can, but with too much false-positive noise (e.g., legitimate test-rewrites between turns).
- **Counts, not patterns.** V13 doesn't check "is this a good test"; it only checks "did this Edit *reduce* good-test signal?". An Edit that *adds* a weaker assertion alongside a strict one is fine; the rule only fires when the strict one disappears.
- **Mock count threshold of 5.** Below 5 is normal; above 5 is reliably a test mocking too much. Number is empirical.
- **Errors, not warnings.** V13's whole point is to be a hard floor. Warnings would be ignored.
- **`-- INTENTIONAL:` not supported.** Skipping a test is sometimes legitimate (flaky external service, OS-specific). The escape hatch is a per-rule disable in `.verifiers/config.yaml` (`validators.disabled: ["V13-TEST-DISABLED"]`), not a per-line comment — because per-line would be too easy for the AI to use.

## How it checks (implementation)

Lives in `hooks/validators/ai_cheating_guard.py`. Tier 2 entry runs `_check_file(file_path, lang)`; Stop is no-op.

### Language detection

```python
def _detect_test_language(file_path):
    p = file_path
    if p.endswith("_test.go"): return "go"
    if p.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")): return "js"
    if "test_" in Path(p).name and p.endswith(".py"): return "python"
    if "/__tests__/" in p: return "js"
    return None
```

### `_check_file(file_path, lang)`

```python
content = Path(file_path).read_text()
findings: list[Finding] = []

# 1. In-file scan (no diff)
findings.extend(self._check_skip_patterns(file_path, content, lang))
findings.extend(self._check_mock_density(file_path, content, lang))
findings.extend(self._check_trivial(file_path, content, lang))

# 2. Edit-diff scan (only when invoked from PostToolUse with old/new)
edit = self._get_pending_edit(file_path)  # reads from a per-session sidecar
if edit:
    findings.extend(self._check_test_count(edit, lang))
    findings.extend(self._check_assertion_count(edit, lang))
    findings.extend(self._check_weakening(edit, lang))
return findings
```

### `_check_test_count` — V13-TEST-DELETED

```python
PATTERNS = {
    "go":     re.compile(r'^func\s+(Test\w+)\s*\(', re.MULTILINE),
    "python": re.compile(r'^def\s+(test_\w+)', re.MULTILINE),
    "js":     re.compile(r'\b(it|test)\s*\([\'"`]', re.MULTILINE),
}
old_count = len(PATTERNS[lang].findall(edit.old))
new_count = len(PATTERNS[lang].findall(edit.new))
if new_count < old_count:
    yield Finding(rule="V13-TEST-DELETED",
                  message=f"Test count dropped {old_count} → {new_count}", ...)
```

### `_check_skip_patterns` — V13-TEST-DISABLED

```python
SKIP = {
    "go":     re.compile(r'\bt\.Skip\s*\('),
    "python": re.compile(r'@pytest\.mark\.skip|@unittest\.skip\b'),
    "js":     re.compile(r'\b(it|test|describe)\.skip\s*\(|^\s*xit\s*\(', re.MULTILINE),
}
edit = self._get_pending_edit(file_path)
if edit:
    new_skips = SKIP[lang].findall(edit.new)
    old_skips = SKIP[lang].findall(edit.old)
    for added in (new_skips[len(old_skips):]):
        yield Finding(rule="V13-TEST-DISABLED", ...)
else:
    # Pure-Write fallback: any skip in fresh content is suspicious
    for line_no, line in enumerate(content.splitlines(), 1):
        if SKIP[lang].search(line):
            yield Finding(rule="V13-TEST-DISABLED", line=line_no, ...)
```

### `_check_assertion_count` — V13-ASSERTION-REMOVED

```python
ASSERT = {
    "go":     re.compile(r'\b(?:assert|require)\.\w+\s*\('),
    "python": re.compile(r'\bassert\s+|\bself\.assert\w+\s*\('),
    "js":     re.compile(r'\bexpect\s*\('),
}
old_count = len(ASSERT[lang].findall(edit.old))
new_count = len(ASSERT[lang].findall(edit.new))
if new_count < old_count:
    yield Finding(rule="V13-ASSERTION-REMOVED", ...)
```

### `_check_weakening` — V13-TEST-WEAKENED

```python
WEAK_PAIRS = {
    "go":     [("require.Equal", "require.NotNil"), ("assert.Equal", "assert.NotNil")],
    "python": [("assertEqual", "assertTrue"), ("assertEqual", "assertIsNotNone")],
    "js":     [("toEqual", "toBeTruthy"), ("toBe", "toBeTruthy")],
}
for strict, loose in WEAK_PAIRS[lang]:
    old_strict = edit.old.count(strict)
    new_strict = edit.new.count(strict)
    new_loose = edit.new.count(loose)
    old_loose = edit.old.count(loose)
    if new_strict < old_strict and new_loose > old_loose:
        yield Finding(rule="V13-TEST-WEAKENED", ...)
```

### `_check_mock_density` — V13-MOCK-EVERYTHING

```python
# Count per-test-function, not per-file. Walks each `func TestX` /
# `def test_x` / `it(...)` body and counts mocks inside.
MOCK = {
    "go":     re.compile(r'\bgomock\.NewController\s*\(|\bmock\.NewMock\w+\s*\('),
    "python": re.compile(r'\b(?:mock\.)?patch\s*\(|@patch\b'),
    "js":     re.compile(r'\bjest\.mock\s*\(|\bvi\.mock\s*\('),
}
for body, header_line in self._iter_test_bodies(content, lang):
    if len(MOCK[lang].findall(body)) > 5:
        yield Finding(rule="V13-MOCK-EVERYTHING", line=header_line, ...)
```

### `_check_trivial` — V13-TRIVIAL-TEST

```python
TRIVIAL = re.compile(
    r'(?:assert\s+True\b|'
    r'expect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)|'
    r'require\.Equal\s*\(\s*t\s*,\s*(\d+|"\w*")\s*,\s*\1\s*\))'
)
for line_no, line in enumerate(content.splitlines(), 1):
    if TRIVIAL.search(line):
        yield Finding(rule="V13-TRIVIAL-TEST", line=line_no, ...)
```

### Could be more effective

- **AST instead of regex for body extraction.** `_iter_test_bodies` regex misses nested functions and Go's `t.Run("subtest", func(t *testing.T) {...})`. Real parsing (`go/parser`, `ast`, `@babel/parser`) is more reliable. Per-language bridge cost.
- **Pending-edit sidecar reliability.** The `_get_pending_edit` mechanism reads the Edit's old/new from a session sidecar. If the hook is invoked outside Claude Code (e.g., via `run_single` CLI), the sidecar is empty and edit-diff rules silently disable. Documented but not yet alarmed.
- **Coverage-delta cross-check.** A test deletion that *raised* coverage (because the deleted test was a duplicate) is OK; one that lowered coverage is the smell. Currently V13 doesn't have coverage; it errs on the conservative side.
- **`pytest.mark.xfail` as a softer signal.** Currently V13 doesn't distinguish `xfail` (expected failure, acceptable) from `skip` (silently ignored, suspicious). Adding the distinction would tighten the rule.
- **Weakening pairs are language-curated.** New strict/loose patterns appear with libraries (e.g., `vitest`'s `toMatchObject` vs `toEqual`). The list needs maintenance; a config-driven extension would help.

## References

- [Kent Beck — *Test-Driven Development by Example*](https://www.amazon.com/Test-Driven-Development-Kent-Beck/dp/0321146530) — Kent Beck, *published 2002*, retrieved 2026-04-30. The Red-Green-Refactor discipline V13 protects.
- [Kent Beck — Tidy First? blog series](https://tidyfirst.substack.com/) — Kent Beck, *continuously updated since 2023*, retrieved 2026-04-30. Modern essays on tests-as-design including warnings against test deletion as a coping mechanism.
- [Atipico1/ai-testing-rules](https://github.com/Atipico1/ai-testing-rules) — Atipico, *continuously maintained*, retrieved 2026-04-30. Source of the "tests-define-behavior, don't let AI mock-everything" rule set V13 codifies in static-analysis form.
- [Mockito — When Not to Mock](https://github.com/mockito/mockito/wiki/Mockito-vs-EasyMock#mocking-vs-using-real-objects) — Mockito wiki, *continuously updated*, retrieved 2026-04-30. Background for the V13-MOCK-EVERYTHING threshold.
- [Google Testing Blog — *Code coverage best practices*](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html) — Google, *published 2020-08*, retrieved 2026-04-30. Why "trivial test" is a metric-game pattern V13 catches.

## Examples

### ✓ Pass

```python
# Edit: tightened the assertion (good direction)
# old:
def test_login():
    user = login("a@b", "x")
    assert user is not None
# new:
def test_login():
    user = login("a@b", "x")
    assert user is not None
    assert user.email == "a@b"
    assert user.session_id != ""
```

### ✗ Fail

```python
# old:
def test_login():
    user = login("a@b", "x")
    assert user.email == "a@b"
    assert user.session_id != ""
# new:
def test_login():
    user = login("a@b", "x")
    assert user is not None      # → V13-ASSERTION-REMOVED + V13-TEST-WEAKENED
```

```python
@pytest.mark.skip(reason="flaky in CI")    # → V13-TEST-DISABLED (error)
def test_complex_thing():
    ...
```

```js
// V13-MOCK-EVERYTHING: 6 mocks in one test
it("creates a user", () => {
  jest.mock("./db");
  jest.mock("./redis");
  jest.mock("./email");
  jest.mock("./logger");
  jest.mock("./tracer");
  jest.mock("./featureFlags");
  expect(createUser("x")).toBeTruthy();
});
```
