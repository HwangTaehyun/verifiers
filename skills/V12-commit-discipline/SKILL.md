# V12 — commit-discipline

> **Owner**: `hooks/validators/commit_discipline.py`
> **Tier**: 3 (Stop) only — Tier 2 (PostToolUse) is irrelevant because git working-tree state is the input, and that state is meaningful only at end-of-turn.
> **File patterns**: empty (matches every file). The whole-project view is what matters.

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V12-UNSTAGED-CHANGES` | warning | `git status --porcelain` shows working-tree modifications. The Stop hook is about to end the turn but the user has uncommitted work. |
| `V12-LARGE-DIFF` | warning | The unstaged + staged change touches more files than `thresholds.commit.large_diff_files` (default 15). Suggests splitting before commit. |
| `V12-MIXED-CHANGE` | warning | Same diff contains both rename / delete (`R` / `D`) and modify (`M`) entries — Kent Beck's "structural vs behavioral" mix that obscures the intent. |
| `V12-NO-TEST-IN-FEATURE` | warning | Source files (`*.go`, `*.ts(x)`, `*.py`) changed but no test files (`*_test.go`, `*.test.ts(x)`, `test_*.py`) changed. Bug-fix or feature without a test landing alongside it. |
| `V12-COMMIT-MSG-FORMAT` | warning | The most recent commit message (or, in some flows, the commit-message draft) does not match `^(feat|fix|refactor|docs|test|chore|style|perf|ci|build|revert)(\(.+\))?!?:\s+.+`. |

## Why this verifier exists

V12 enforces **Kent Beck's "Tidy First?" / atomic-commit discipline** at the moment a turn ends:

- **Atomic commits.** A 50-file diff covering 4 different intents is unreviewable. Splitting it into 4 commits gives reviewers a fighting chance.
- **Structural / behavioral separation.** Renaming a file *while* changing its semantics in the same commit hides the rename from diff tools. Splitting is cheap; not splitting is permanent code-archaeology debt.
- **Test alongside change.** A feature without a test or a bug-fix without a regression test is a permanent invitation to re-break.
- **Conventional Commits format.** Tooling-friendly history (changelog generation, automated semantic-version bumps, search) requires consistent messages.

V12 fires at Stop because that's where the user's "I'm done with this slice" intent is — and that's the moment to nudge them toward a tidier commit.

## Design rationale

- **Tier 3 only.** Working-tree state mid-edit is meaningless ("I'm in the middle of typing"). Stop is the right inflection point.
- **All warnings, not errors.** Every V12 rule has legitimate exceptions: a deliberately big "merge feature branch" commit, a structural-only refactor with no test changes (rename only), an emergency hotfix that bypasses Conventional Commits. Hard-failing Stop on these would erode trust in the validator.
- **`thresholds.commit.large_diff_files` is configurable.** A monorepo with auto-generated code can legitimately have 50-file commits; a small library can't. Project-level knob.
- **`-- INTENTIONAL:` style escape is *not* present.** Unlike V04's DDL, V12's warnings can't be silenced per-finding because they're noisy by design — the user is supposed to see them and decide.
- **Commit-message format takes the most recent commit.** Not the next-to-be-written one (we don't have access). The model is: the user just amended/committed, V12 reads what landed.

## How it checks (implementation)

Lives in `hooks/validators/commit_discipline.py`. `validate_project` is the only entry point.

### `validate_project(ctx)` — top-level

```python
def validate_project(self, ctx):
    findings: list[Finding] = []
    large_diff_threshold = ctx.config.thresholds.commit.large_diff_files
    cwd = str(ctx.project_root)

    # 1. Working-tree status
    status = _run_git(["status", "--porcelain"], cwd)
    if not status:
        return findings  # clean tree — nothing to check (already committed)
    status_lines = [l for l in status.split("\n") if l.strip()]
    yield Finding(
        severity="warning",
        rule="V12-UNSTAGED-CHANGES",
        message=f"{len(status_lines)} unstaged/uncommitted change(s)",
        ...
    )

    # 2. Parse status into (status_code, file_path) tuples
    all_files: list[tuple[str, str]] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        all_files.append((code, path))

    # 3. Large-diff
    if len(all_files) >= large_diff_threshold:
        yield Finding(rule="V12-LARGE-DIFF", ...)

    # 4. Mixed structural + behavioral
    findings.extend(self._check_mixed_changes(all_files, cwd))

    # 5. Test-alongside-source
    findings.extend(self._check_test_coverage(all_files, ctx))

    # 6. Commit-message format
    findings.extend(self._check_commit_msg_format(cwd))
    return findings
```

### `_check_mixed_changes(all_files, cwd)` — V12-MIXED-CHANGE

```python
# Use `git diff --name-status HEAD` to get the full status (R/D/M)
# rather than working-tree-only `git status` (which can't see renames cleanly).
diff = _run_git(["diff", "--name-status", "HEAD"], cwd).splitlines()
has_structural = any(line[:1] in ("R", "D") for line in diff)
has_behavioral = any(line[:1] in ("M",) for line in diff)
if has_structural and has_behavioral:
    yield Finding(rule="V12-MIXED-CHANGE", ...)
```

### `_check_test_coverage(all_files, ctx)` — V12-NO-TEST-IN-FEATURE

```python
SOURCE_PATTERNS = (".go", ".py", ".ts", ".tsx")
TEST_PATTERNS = ("_test.go", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

def _is_source(p):
    return p.endswith(SOURCE_PATTERNS) and not _is_test(p) \
           and not p.endswith(("_test.go",)) \
           and "test_" not in Path(p).name
def _is_test(p):
    return p.endswith(TEST_PATTERNS) or Path(p).name.startswith("test_")

source_changed = any(_is_source(p) for _, p in all_files)
test_changed = any(_is_test(p) for _, p in all_files)
if source_changed and not test_changed:
    yield Finding(rule="V12-NO-TEST-IN-FEATURE", ...)
```

### `_check_commit_msg_format(cwd)` — V12-COMMIT-MSG-FORMAT

```python
msg = _run_git(["log", "-1", "--pretty=%s"], cwd).strip()
CONVENTIONAL = re.compile(
    r'^(feat|fix|refactor|docs|test|chore|style|perf|ci|build|revert)'
    r'(\([^)]+\))?!?:\s+.+'
)
if msg and not CONVENTIONAL.match(msg):
    yield Finding(rule="V12-COMMIT-MSG-FORMAT", ...)
```

The trailing `!` (breaking-change marker) is supported. Scopes (`feat(api): ...`) are accepted.

### Could be more effective

- **`-- INTENTIONAL:` per-finding silencer.** Add a way to mark "this commit is deliberately mixed" via a footer like `Tidy: skip-mixed`. Costs little; gives a graceful escape.
- **Auto-suggest split.** Given a mixed diff, V12 could compute file-level clusters (`structural_files`, `behavioral_files`) and emit the suggested split as `git add <files1> && git commit ...`. High UX value, modest implementation cost.
- **Commit-message ↔ change-shape consistency.** A `feat:` message with a delete-only diff is suspicious. Mapping (verb prefix) ↔ (change shape) gives an extra signal.
- **Squash detection.** If `git log` shows 12 fix-up commits queued for amend/squash, that's an antipattern (the user is using commits as a save mechanism, not a logical unit). Tracking and surfacing would help.
- **CI-impact awareness.** Touching `.github/workflows/*.yml` in the same commit as application code blurs ownership. Could be a separate `V12-CI-MIXED` rule.

## References

- [Kent Beck — *Tidy First?*](https://www.oreilly.com/library/view/tidy-first/9781098151232/) — Kent Beck, *published 2023-11*, retrieved 2026-04-30. Source of the structural vs behavioral separation V12-MIXED-CHANGE enforces.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) — Conventional Commits authors, *published 2019, stable 1.0.0*, retrieved 2026-04-30. Source of the regex V12-COMMIT-MSG-FORMAT enforces.
- [The Pragmatic Programmer — *Programming by Coincidence*](https://pragprog.com/titles/tpp20/the-pragmatic-programmer-20th-anniversary-edition/) — Hunt & Thomas, *published 1999, 20th anniv. ed. 2019*, retrieved 2026-04-30. The "ship feature without test" anti-pattern V12-NO-TEST-IN-FEATURE flags.
- [git — Status porcelain v1](https://git-scm.com/docs/git-status#_porcelain_format_version_1) — Git project, *continuously updated*, retrieved 2026-04-30. The output format V12 parses.

## Examples

### ✓ Pass

```
$ git status --porcelain
M  internal/users/repository.go
M  internal/users/repository_test.go     ← test alongside source

$ git log -1 --pretty=%s
feat(users): add Update method to repository
```

### ✗ Fail

```
$ git status --porcelain
 M internal/users/repository.go          ← uncommitted          → V12-UNSTAGED-CHANGES (warning)
 M internal/users/handler.go             ← (also no test)
 M internal/orders/repository.go
 M internal/orders/handler.go
 ... 18 files modified ...               → V12-LARGE-DIFF (warning, > 15)
R  old_name.go -> new_name.go            ← rename
                                         + modify together     → V12-MIXED-CHANGE (warning)

$ git log -1 --pretty=%s
"WIP refactor"                           → V12-COMMIT-MSG-FORMAT (warning, not Conventional)
```
