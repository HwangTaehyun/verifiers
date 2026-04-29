# V03 — proto-connect

> **Owner**: `hooks/validators/proto_connect.py`
> **Tier**: 2 (PostToolUse) — buf lint + stale-gen on every relevant edit. 3 (Stop) — adds handler-coverage + breaking-change scan.
> **File patterns**: `**/proto/**/*.proto`, `**/buf.yaml`, `**/buf.gen.yaml`, `**/gen/**/*.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V03-BUF-LINT` | error | `buf lint` returned at least one finding (parsed from `file:line:col:rule` format). |
| `V03-STALE-GEN` | warning | A `.proto` content hash is newer than the cache, OR `gen/**/*.go` mtime predates a proto's mtime. |
| `V03-UNIMPLEMENTED-RPC` | error | A `service Foo { rpc Bar(...) returns (...); }` has no Go handler function matching it under `internal/`. |
| `V03-BREAKING` | error | `buf breaking --against main` reports a wire-incompatible change vs the main branch. Works inside git worktrees by resolving `git rev-parse --git-common-dir`. |

## Why this verifier exists

A protobuf contract is the source of truth for both the server (Connect-RPC handlers) and every client (frontend ES bindings, mobile, third-party). Three failure modes pile up over a project's life:

1. **Bad style.** `optional` misuse, missing comments, package-version mismatch — `buf lint` catches all of them, but only if it actually runs. V03 forces the run on every relevant edit.
2. **Generated code drift.** Developer edits `users.proto`, regenerates the Go bindings but not the TS bindings (or vice versa). Type mismatch only surfaces on first runtime call.
3. **Unimplemented RPCs.** The proto declares the contract; Go is supposed to implement every Service.Method. AI agents are particularly prone to define an RPC and forget the handler — the gen layer compiles fine, but the runtime returns "method not found".
4. **Breaking changes.** Renaming a field number is wire-incompatible — every running client breaks. `buf breaking` catches this against the main branch, but only if invoked.

V03 turns all four into hook-time checks so the regression dies before the commit.

## Design rationale

- **Two-pass design (Tier 2 vs Tier 3).** Lint + stale runs on every edit because it's cheap (~100 ms). Handler matching + breaking change runs only on Stop because each is a heavier walk.
- **`buf breaking` against `main`, not HEAD.** A series of commits inside a feature branch can repeatedly introduce + revert breaking changes; what matters is whether the *final* shape on the branch differs from main. V03 uses `git rev-parse --git-common-dir` so the comparison works inside `git worktree` checkouts (where `.git` is a file pointing to the actual common dir).
- **Hash + mtime double-check (matches V02).** Same rationale: survives `git checkout` mtime resets and `cp -p` content drifts.
- **Handler matching is regex, not AST.** `func (s *FooServer) Bar(...)` is enough surface to match `service Foo { rpc Bar }`. The AST upgrade would catch generic methods but isn't worth the Python↔Go bridge cost yet.

## How it checks (implementation)

### `_check_buf_lint(ctx)` — V03-BUF-LINT

```python
result = subprocess.run(
    ["buf", "lint"],
    cwd=str(ctx.proto_dir),
    capture_output=True, text=True, timeout=20,
)
# stdout lines look like:  proto/foo.proto:12:3:FIELD_LOWER_SNAKE_CASE  Foo
LINE = re.compile(r'^([^:]+):(\d+):(\d+):([A-Z_]+)\s+(.+)$')
for line in result.stdout.splitlines():
    if (m := LINE.match(line)):
        yield Finding(
            severity="error",
            file=m.group(1),
            line=int(m.group(2)),
            rule=f"V03-BUF-LINT-{m.group(4)}",
            message=m.group(5),
            ...
        )
```

If `buf` is missing on PATH (`FileNotFoundError`), V03 emits zero findings — it does not synthesize a "buf not installed" error because that's a setup problem, not a code problem.

### `_check_stale_generated(ctx)` — V03-STALE-GEN

Same hash + mtime pattern as V02, applied to `proto/**/*.proto` inputs vs `gen/**/*.go` outputs. The cache lives at `gen/.proto-hash-cache.json`.

### `_check_handler_coverage(ctx)` — V03-UNIMPLEMENTED-RPC (Stop)

```python
# 1. Parse proto: find every service { rpc X(...) returns (...) }
PROTO = re.compile(
    r'service\s+(\w+)\s*\{([^}]+)\}', re.DOTALL
)
RPC = re.compile(r'^\s*rpc\s+(\w+)\s*\(')
declared: set[tuple[str, str]] = set()
for proto_file in ctx.proto_dir.rglob("*.proto"):
    src = proto_file.read_text()
    for svc_match in PROTO.finditer(src):
        svc, body = svc_match.group(1), svc_match.group(2)
        for rpc_match in RPC.finditer(body):
            declared.add((svc, rpc_match.group(1)))

# 2. Walk Go: find every func (recv *FooServer) Bar(ctx, req) (resp, err)
GO_HANDLER = re.compile(
    r'func\s+\([^)]+\*(\w+)\)\s+(\w+)\s*\([^)]*context\.Context'
)
implemented: set[tuple[str, str]] = set()
for go_file in (ctx.server_dir / "internal").rglob("*.go"):
    for m in GO_HANDLER.finditer(go_file.read_text()):
        implemented.add((m.group(1), m.group(2)))

# 3. Diff
for svc, rpc in declared - implemented:
    yield Finding(rule="V03-UNIMPLEMENTED-RPC", ...)
```

Service-name match is on the receiver type (`FooServer`), not the proto package — assumes the convention `service Foo` ↔ `type FooServer struct`. Projects that deviate would need a `.verifiers/config.yaml` knob (not yet implemented).

### `_check_breaking(ctx)` — V03-BREAKING (Stop)

```python
# Resolve common dir (works inside `git worktree`)
common_dir = subprocess.run(
    ["git", "rev-parse", "--git-common-dir"],
    cwd=str(ctx.project_root),
    capture_output=True, text=True, timeout=5,
).stdout.strip()

# Run buf breaking
result = subprocess.run(
    ["buf", "breaking", str(ctx.proto_dir),
     "--against", f"git#{common_dir}#branch=main,subdir=proto"],
    capture_output=True, text=True, timeout=30,
)
# Same `file:line:col:rule message` parse as buf lint
```

Failures are emitted as `V03-BREAKING-<RULE>` so the rule code (e.g. `FIELD_NO_DELETE`) carries through.

### Could be more effective

- **AST proto parser.** The `service { rpc }` regex misses `option`-decorated services and nested message-only proto files. A `protobuf.parser` Python lib would be exact.
- **Per-language gen-staleness.** Currently the cache tracks `proto → go gen`. The same project also generates TS via `buf.build/bufbuild/es:v1.10.0`. A future enhancement should track multiple generators independently — today TS-side staleness slips past V03.
- **Per-service `protovalidate` enforcement.** A `required` field with no `(buf.validate.field).required = true` is a contract bug. Belongs to a future V23 (buf-governance) per the Phase 27 audit.
- **`buf format --diff` enforcement.** Lint catches violations but format drift is silent. A `buf format --exit-code` check would close the gap; cheap to add.

## References

- [Buf — Lint and Breaking Change checkers](https://buf.build/docs/breaking/overview/) — Buf, *continuously updated*, retrieved 2026-04-30. The two checkers V03 wraps and the `--against` syntax for branch comparison.
- [Buf — Style guide for Protobuf](https://buf.build/docs/best-practices/style-guide) — Buf, *continuously updated*, retrieved 2026-04-30. Source of the lint rules V03 surfaces.
- [Connect-RPC — Implementing services](https://connectrpc.com/docs/go/serving-clients/) — The Connect Authors, *continuously updated*, retrieved 2026-04-30. The handler-method shape V03-UNIMPLEMENTED-RPC matches against.
- [Google Protocol Buffers — Style Guide](https://protobuf.dev/programming-guides/style/) — Google, *continuously updated*, retrieved 2026-04-30. Upstream of Buf's lint defaults.
- [Git — `git-worktree`](https://git-scm.com/docs/git-worktree) — Git project, *continuously updated*, retrieved 2026-04-30. Why `--git-common-dir` matters for breaking-change comparison inside worktrees.

## Examples

### ✓ Pass

```protobuf
// proto/users.proto
service UserService {
  rpc CreateUser(CreateUserRequest) returns (CreateUserResponse) {}
}
```

```go
// internal/users/handler.go — handler exists, name matches
func (s *UserServiceServer) CreateUser(
    ctx context.Context, req *connect.Request[pb.CreateUserRequest],
) (*connect.Response[pb.CreateUserResponse], error) {
    // ...
}
```

### ✗ Fail

```protobuf
service UserService {
  rpc UpdateUser(UpdateUserRequest) returns (UpdateUserResponse) {}
}
```

```go
// internal/users/handler.go — no UpdateUser method anywhere
// → V03-UNIMPLEMENTED-RPC (error)
```

```protobuf
// edited: removed field 3 (was string email = 3) → wire-incompatible
message User {
  string id = 1;
  string name = 2;
  // string email = 3;   ← removed
}
// → V03-BREAKING (error, FIELD_NO_DELETE)
```
