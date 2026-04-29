# V23 — buf-governance

> **Owner**: `hooks/validators/buf_governance.py`
> **Tier**: 2 (PostToolUse) — lock drift + protovalidate (cheap). 3 (Stop) — adds `buf breaking` against main.
> **File patterns**: `**/buf.yaml`, `**/buf.lock`, `**/buf.gen.yaml`, `**/proto/**/*.proto`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V23-LOCK-DRIFT` | warning | A dep is in `buf.yaml` but not in `buf.lock` (or vice versa); also fires when `buf.lock` is missing entirely with non-empty deps. |
| `V23-BREAKING-<RULE>` | error | `buf breaking <buf_dir> --against git#...#branch=main,subdir=...` reported a wire-incompatible change. The original Buf rule code is preserved (`FIELD_NO_DELETE`, `FILE_SAME_PACKAGE`, etc.). |
| `V23-PROTOVALIDATE-MISSING` | warning | A `.proto` field whose name is in the required-hint set (`id`, `email`, `user_id`, `tenant_id`, `username`, `name`, ...) has no `(buf.validate.field)` or legacy `(validate.rules)` annotation. |

V23 only fires when `buf.yaml` is detected (in `server/` or project root). Projects without Buf incur zero cost.

## Why this verifier exists

V03 (`proto-connect`) covers `buf lint` and handler coverage. V23 sits on the **contract-governance** layer that V03 doesn't touch:

1. **`buf.yaml` ↔ `buf.lock` drift.** A common workflow bug: a developer adds a dep line to `buf.yaml` but forgets `buf dep update`. Builds work locally because of the cache; another developer clones, `buf generate` fails with cryptic "module not found". V23 catches the drift on the same Edit that introduces it.
2. **Wire-incompatible breaking changes.** `FIELD_NO_DELETE`, `FIELD_NO_TYPE_CHANGE`, `FIELD_SAME_NAME` — each is a literal break of the contract every running client depends on. `buf breaking` against `main` is the canonical check; V23 forces it on every Stop and uses `git rev-parse --git-common-dir` so it works inside `git worktree` checkouts.
3. **`protovalidate` underuse.** Marking an `id` or `email` field without `(buf.validate.field).required = true` lets clients send the empty value; the schema accepts it and the handler later rejects it with HTTP 500. The name-based heuristic catches the high-frequency cases where a proto field's name *strongly implies* it shouldn't be empty.

## Design rationale

- **All three checks gated on `buf.yaml` existence.** Projects without Buf get zero V23 findings and zero subprocess calls. Probe is `_find_buf_dir(ctx)` — checks `server/buf.yaml` then root `buf.yaml`.
- **`buf breaking` only at Stop.** The shellout takes seconds against a git remote; running it on every Edit would blow Tier 2's budget.
- **Lock drift is symmetric.** Missing-from-lock and stale-in-lock both warn — the user usually wants `buf dep update` to run regardless of which side is "behind".
- **Protovalidate heuristic is name-based.** A real check would need a per-field "is this required by business logic?" signal — impossible to derive statically. The heuristic targets the empirically high-frequency cases: `id`, `email`, `user_id`, `tenant_id`, `account_id`, `phone`, `phone_number`, `name`, `username`. False-positive rate manageable; user can disable per-rule.
- **Breaking findings preserve the Buf rule code.** `V23-BREAKING-FIELD_NO_DELETE` lets users selectively disable e.g. `validators.disabled: ["V23-BREAKING-FIELD_SAME_NAME"]` without disabling all of breaking-change detection.
- **`git rev-parse --git-common-dir`.** A worktree's `.git` is a file, not a directory; pointing `buf breaking --against git#<dir>` at the worktree's `.git` file fails. Resolving the common-dir up front is what makes Connect-RPC dev-in-worktrees workflow work.

## How it checks (implementation)

Lives in `hooks/validators/buf_governance.py`.

### `_find_buf_dir(ctx)` — gating predicate

```python
def _find_buf_dir(ctx):
    candidates = []
    if ctx.server_dir is not None:
        candidates.append(ctx.server_dir)
    candidates.append(ctx.project_root)
    for d in candidates:
        if (d / "buf.yaml").is_file():
            return d
    return None
```

### Tier 2 — `validate_file`

```python
def validate_file(self, ctx, file_path):
    buf_dir = _find_buf_dir(ctx)
    if buf_dir is None:
        return []
    findings = []
    findings.extend(self._check_lock_drift(buf_dir))
    findings.extend(self._check_protovalidate(buf_dir))
    return findings
```

### `_check_lock_drift(buf_dir)` — V23-LOCK-DRIFT

```python
yaml_doc = yaml.safe_load((buf_dir / "buf.yaml").read_text()) or {}
declared = {dep for dep in (yaml_doc.get("deps") or []) if isinstance(dep, str)}

if not declared:
    return []  # no deps → no lock to compare

lock_path = buf_dir / "buf.lock"
if not lock_path.exists():
    yield Finding(rule="V23-LOCK-DRIFT", message="buf.lock is missing", ...)
    return

lock_doc = yaml.safe_load(lock_path.read_text()) or {}
locked = set()
for dep in (lock_doc.get("deps") or []):
    if isinstance(dep, dict):
        name = dep.get("name") or dep.get("module")
        if isinstance(name, str):
            locked.add(name)

# Symmetric diff: declared - locked AND locked - declared
for missing in sorted(declared - locked):
    yield Finding(rule="V23-LOCK-DRIFT", file=str(yaml_path),
                  message=f"Dep '{missing}' declared but not pinned", ...)
for stale in sorted(locked - declared):
    yield Finding(rule="V23-LOCK-DRIFT", file=str(lock_path),
                  message=f"Dep '{stale}' in lock but no longer declared", ...)
```

### `_check_breaking(ctx, buf_dir)` — V23-BREAKING-<RULE> (Stop)

```python
# 1. Resolve common-dir for git-worktree compatibility
cd = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                    cwd=str(ctx.project_root), capture_output=True, text=True, timeout=5)
if cd.returncode != 0:
    return []
common_dir = cd.stdout.strip()

# 2. Compute subdir (relative to project root)
subdir = str(buf_dir.relative_to(ctx.project_root)).replace("\\", "/")

# 3. Shell out
result = subprocess.run(
    ["buf", "breaking", str(buf_dir),
     "--against", f"git#{common_dir}#branch=main,subdir={subdir}"],
    capture_output=True, text=True, timeout=30,
)
if result.returncode == 0:
    return []

# 4. Parse  file:line:col:RULE_NAME message
LINE = re.compile(r"^([^:]+):(\d+):(\d+):([A-Z_]+)\s+(.+)$")
for line in (result.stdout + result.stderr).splitlines():
    if (m := LINE.match(line)):
        yield Finding(
            severity="error",
            file=m.group(1), line=int(m.group(2)),
            rule=f"V23-BREAKING-{m.group(4)}",
            message=m.group(5),
            ...
        )
```

If `buf` is missing on PATH, the FileNotFoundError is logged via `log_exception` and V23 emits zero breaking-findings — same fail-open philosophy as V03's `buf lint` shellout.

### `_check_protovalidate(buf_dir)` — V23-PROTOVALIDATE-MISSING

```python
REQUIRED_HINT_NAMES = ("id", "user_id", "account_id", "tenant_id",
                        "email", "phone", "phone_number", "name", "username")

PROTO_FIELD = re.compile(
    r"^\s*"
    r"(?:repeated\s+|optional\s+)?"
    r"(?:[\w.]+)\s+"        # type
    r"(\w+)\s*=\s*\d+"       # name + tag
    r"([^;]*);",             # rest of line including [(...) options]
)

for proto_file in (buf_dir / "proto").rglob("*.proto"):
    src = proto_file.read_text()
    for line_no, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith(("//", "/*", "*")):
            continue
        m = PROTO_FIELD.match(line)
        if not m:
            continue
        field_name, rest = m.group(1), m.group(2)
        if field_name not in REQUIRED_HINT_NAMES:
            continue
        if "buf.validate.field" in rest:
            continue                                  # has new-style annotation
        if "(validate.rules)" in rest:
            continue                                  # legacy protoc-gen-validate
        yield Finding(rule="V23-PROTOVALIDATE-MISSING", line=line_no, ...)
```

### Could be more effective

- **Real proto AST.** The regex misses multi-line field declarations (`string id\n = 1\n [...]\n;`) and reserved-range syntax. `protobuf.parser` (Python) would be exact at the cost of an extra dep.
- **Schema-driven `required` extraction.** A future enhancement could read the proto's documentation comments for explicit `Required:` markers and reduce the heuristic's false-positive rate.
- **`buf format --diff` enforcement.** Buf format drift isn't a V03 / V23 check yet. One-line addition: `buf format --diff` exit code → `V23-FORMAT-DRIFT`.
- **Plugin matrix coverage.** `buf.gen.yaml` lists multiple language plugins (`go`, `es`, `doc`, `openapiv3`). A future check: each output dir (`gen/`, `gen/es`, `gen/doc`) is fresh against the proto inputs. Currently V03's stale-gen check covers Go only.
- **Per-RPC breaking-change exemption.** Some breaking changes are intentional (a `WithdrawCoins` RPC retired with all clients migrated). Currently V23 has no exemption mechanism; a `// buf:no-breaking` style annotation in the proto would be cleaner than blanket-disabling V23.
- **`buf push` against the registry.** A truly hermetic check would push to the registry's draft channel and verify the type-checker accepts it. Out of hook scope; CI-grade.

## References

- [Buf — Style guide for Protobuf](https://buf.build/docs/best-practices/style-guide) — Buf, *continuously updated*, retrieved 2026-04-30. Source for the lint + style rules `buf lint` enforces (V03 already calls it; V23 builds on top).
- [Buf — Lint and breaking-change checkers](https://buf.build/docs/breaking/overview/) — Buf, *continuously updated*, retrieved 2026-04-30. The two checkers and the `--against` syntax.
- [Buf — Modules and dependencies](https://buf.build/docs/bsr/module/dependencies/) — Buf, *continuously updated*, retrieved 2026-04-30. The `buf.yaml` deps + `buf.lock` semantics V23-LOCK-DRIFT enforces.
- [Buf — protovalidate](https://buf.build/docs/protovalidate/) — Buf, *continuously updated*, retrieved 2026-04-30. The validation library V23-PROTOVALIDATE-MISSING expects.
- [protovalidate — `string.email`, `required`, etc.](https://github.com/bufbuild/protovalidate/blob/main/proto/buf/validate/validate.proto) — Buf, *continuously maintained*, retrieved 2026-04-30. The actual rule names V23 mentions in its `fix` text.
- [Connect-RPC — Backward compatibility guide](https://connectrpc.com/docs/protocol/) — Connect Authors, *continuously updated*, retrieved 2026-04-30. The wire-format guarantees `buf breaking` exists to enforce.
- [git — `git-worktree`](https://git-scm.com/docs/git-worktree) — Git project, *continuously updated*, retrieved 2026-04-30. The reason `--git-common-dir` resolution matters before invoking `buf breaking`.

## Examples

### ✓ Pass

```yaml
# server/buf.yaml
version: v2
modules: [{path: proto}]
deps:
  - buf.build/bufbuild/protovalidate
```

```yaml
# server/buf.lock
version: v2
deps:
  - name: buf.build/bufbuild/protovalidate
    commit: <pin>
```

```protobuf
import "buf/validate/validate.proto";
message CreateUser {
  string id    = 1 [(buf.validate.field).required = true];
  string email = 2 [(buf.validate.field).string.email = true];
}
```

### ✗ Fail

```yaml
# buf.yaml adds dep, buf.lock not regenerated
deps:
  - buf.build/bufbuild/protovalidate     # ← new
  - buf.build/googleapis/googleapis
# buf.lock has only googleapis           → V23-LOCK-DRIFT (warning)
```

```protobuf
message CreateUser {
  string id    = 1;                      // → V23-PROTOVALIDATE-MISSING
  string email = 2;                      // → V23-PROTOVALIDATE-MISSING
}
```

```
$ buf breaking proto --against 'git#...#branch=main,subdir=proto'
proto/users.proto:5:3:FIELD_NO_DELETE    Field "email" was deleted from message "User".
proto/users.proto:4:3:FIELD_SAME_NAME    Field "2" on message "User" changed name from "name" to "display_name".
→ V23-BREAKING-FIELD_NO_DELETE (error)
→ V23-BREAKING-FIELD_SAME_NAME (error)
```
