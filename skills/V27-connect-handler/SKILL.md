# V27 — connect-handler

> **Owner**: `hooks/validators/connect_handler.py`
> **Tier**: 2 (PostToolUse) and 3 (Stop) — same project sweep on both.
> **File patterns**: `**/*.go`, `**/proto/**/*.proto`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V27-UNIMPLEMENTED-RPC` | error | A `service Foo { rpc Bar(...) ... }` in proto has no Go method `func (s *FooServer) Bar(ctx, req)` anywhere under `server/internal/`. |
| `V27-NO-INTERCEPTORS` | error | A `<pkg>connect.NewXxxHandler(impl, ...)` registration call has no `connect.WithInterceptors(...)` option. |
| `V27-MISSING-AUTH-INTERCEPTOR` | warning | `WithInterceptors(...)` is present but no interceptor name contains `auth`. |
| `V27-MISSING-LOGGING-INTERCEPTOR` | warning | Same shape, missing `logging`. |
| `V27-MISSING-VALIDATION-INTERCEPTOR` | warning | Same shape, missing `validation`. |
| `V27-RAW-ERROR-RETURN` | warning | A handler returns `err` / `errors.New(...)` / `fmt.Errorf(...)` / `ErrXxx` directly without wrapping in `connect.NewError(connect.Code*, ...)`. |

V27 only fires when Connect-RPC is detected — at least one Go file under `server/` imports `connectrpc.com/connect` or the legacy `github.com/bufbuild/connect-go`. Projects without Connect get zero V27 cost.

## Why this verifier exists

V03 (`proto-connect`) already covers `buf lint` + a basic proto→Go RPC mapping. V27 is the **handler-side contract enforcement** layer — three failure modes that V03 doesn't catch:

1. **Wrong handler shape.** A handler that compiles but doesn't match the Connect signature (`ctx context.Context, req *connect.Request[T]`) is silently un-registered. The runtime returns "method not implemented" with no compile-time signal.
2. **Missing interceptors.** Auth + logging + validation are the three universal cross-cutting concerns; if a single handler is registered without them, that's a back-door auth bypass + an audit-log gap. AI agents writing wiring code skip the interceptor list with surprising regularity.
3. **Sentinel errors leaking.** A handler that returns `errors.New("not found")` directly produces HTTP 500 with no gRPC status code mapping. Mobile clients can't distinguish "this user doesn't exist" from "the database is down". `connect.NewError(connect.CodeNotFound, err)` is the documented pattern.

V27 turns all three into hook-time findings.

## Design rationale

- **Connect detection is project-wide.** A single Go file importing `connectrpc.com/connect` is enough to activate V27. Avoids per-file gating overhead.
- **Handler matching is regex on `*FooServer` receivers.** The convention `service Foo { ... }` ↔ `type FooServer struct{}` is the Connect canonical pattern. Projects that diverge (`*Handler`, `*Service`) need a config knob (TODO).
- **Interceptor name matching is loose.** `AuthInterceptor`, `RequireAuth`, `JwtAuth`, `auth.Middleware` all have `auth` as a substring. Same for `logging` (`Logger`, `RequestLogging`, `LoggingInterceptor`) and `validation` (`Validate`, `ProtoValidator`, `ValidationInterceptor`). Loose matching maximizes signal at a small false-negative cost.
- **`V27-NO-INTERCEPTORS` is `error`, missing-individual is `warning`.** No interceptors at all is unambiguous bug; missing one of the three has legitimate edge cases (a public RPC explicitly exempt from auth).
- **Sentinel error detection runs only inside handler bodies.** A repository function returning `ErrNotFound` is fine; only the handler's `return ..., err` matters. V27 finds handler bodies via the strict signature regex, scans within.
- **Same-line `connect.NewError` short-circuits the rule.** A handler that does `return nil, connect.NewError(connect.CodeNotFound, err)` is correct and shouldn't fire. The rule explicitly checks for `connect.NewError` on the same line as the `return`.

## How it checks (implementation)

Lives in `hooks/validators/connect_handler.py`.

### `_detect_connect(ctx)` — project-wide gate

```python
CONNECT_IMPORTS = ("connectrpc.com/connect", "github.com/bufbuild/connect-go")

def _detect_connect(ctx):
    if ctx.server_dir is None:
        return False
    for go_file in ctx.server_dir.rglob("*.go"):
        text = go_file.read_text()
        if any(imp in text for imp in CONNECT_IMPORTS):
            return True
    return False
```

### `_check_handler_completeness(ctx)` — V27-UNIMPLEMENTED-RPC

```python
# 1. Collect declared (Service, Method) from proto
PROTO_SERVICE = re.compile(r"service\s+(\w+)\s*\{([^}]*)\}", re.DOTALL)
PROTO_RPC = re.compile(r"^\s*rpc\s+(\w+)\s*\(", re.MULTILINE)

declared = set()
for proto_file in proto_dir.rglob("*.proto"):
    src = proto_file.read_text()
    for svc_match in PROTO_SERVICE.finditer(src):
        svc, body = svc_match.group(1), svc_match.group(2)
        for rpc_match in PROTO_RPC.finditer(body):
            declared.add((svc, rpc_match.group(1)))

# 2. Collect implemented (Service, Method) from Go
GO_HANDLER = re.compile(r"""
    func\s+\(\s*\w+\s+\*(?P<recv>\w+)\s*\)\s+
    (?P<name>\w+)\s*\(\s*[^)]*\bcontext\.Context\b
""", re.VERBOSE)

implemented = set()
for go_file in (ctx.server_dir / "internal").rglob("*.go"):
    for m in GO_HANDLER.finditer(go_file.read_text()):
        recv, name = m.group("recv"), m.group("name")
        if recv.endswith("Server"):
            service = recv[: -len("Server")]
            implemented.add((service, name))

# 3. Diff
for svc, rpc in declared - implemented:
    yield Finding(rule="V27-UNIMPLEMENTED-RPC", ...)
```

### `_check_interceptors(ctx)` — V27-NO-INTERCEPTORS / V27-MISSING-*-INTERCEPTOR

```python
HANDLER_REGISTER = re.compile(r"\b(?P<connect_pkg>\w+)\.New(?P<service>\w+)Handler\s*\(")
WITH_INTERCEPTORS = re.compile(r"connect\.WithInterceptors\s*\(")
REQUIRED = ("auth", "logging", "validation")

for go_file in internal_root.rglob("*.go"):
    src = go_file.read_text()
    for m in HANDLER_REGISTER.finditer(src):
        # 2000-char window after the call's first paren
        window = src[m.start() : m.start() + 2000]
        close = window.find(")\n", window.find("("))
        window = window[: close if close > 0 else 1500]

        if not WITH_INTERCEPTORS.search(window):
            yield Finding(rule="V27-NO-INTERCEPTORS", ...)
            continue

        for name in REQUIRED:
            if name not in window.lower():
                yield Finding(rule=f"V27-MISSING-{name.upper()}-INTERCEPTOR", ...)
```

### `_check_error_returns(ctx)` — V27-RAW-ERROR-RETURN

```python
GO_HANDLER_STRICT = re.compile(r"""
    func\s+\(\s*\w+\s+\*\w+\s*\)\s+\w+\s*\(
    \s*\w+\s+context\.Context\s*,
    \s*\w+\s+\*connect\.Request\[
""", re.VERBOSE)

RAW_ERROR_RETURN = re.compile(r"""
    \breturn\s+
    (?:nil\s*,\s*)?
    (?:
        (?:fmt\.)?Errorf\s*\( |
        errors\.New\s*\( |
        Err[A-Z]\w+ |        # ErrXxx sentinel
        err\b
    )
""", re.VERBOSE)

CONNECT_WRAP = re.compile(r"\bconnect\.NewError\s*\(")

for handler_match in GO_HANDLER_STRICT.finditer(src):
    body, _ = _extract_body(src, handler_match.end())
    for m in RAW_ERROR_RETURN.finditer(body):
        # If this line also contains connect.NewError, skip
        line_start = body.rfind("\n", 0, m.start()) + 1
        line_end = body.find("\n", m.end()) or len(body)
        if CONNECT_WRAP.search(body[line_start:line_end]):
            continue
        yield Finding(rule="V27-RAW-ERROR-RETURN", ...)
```

`_extract_body` walks balanced braces from the function header `{` to the matching `}` so the scan is bounded to one handler at a time.

### Could be more effective

- **Real Go AST.** Receiver-name matching (`*FooServer`) misses generic methods, type-aliased receivers, and methods on embedded structs. `go/parser` would be exact.
- **Per-handler `auth: false` annotation.** A public RPC (`HealthCheck`, `Login`) legitimately doesn't need auth. Currently V27 has no exemption mechanism; a `// connect:public` comment annotation could relax `V27-MISSING-AUTH-INTERCEPTOR` for matching handlers.
- **gRPC code-mapping inference.** Currently V27 tells the user to "match the Connect code to the failure". A smarter check could detect `errors.Is(err, ErrNotFound)` patterns and suggest `connect.CodeNotFound` automatically.
- **Status-code coverage check.** A handler that always returns `connect.CodeInternal` for every error path is a code smell — every gRPC failure mode collapses to "Internal", costing client error-handling. A future check could count distinct codes per handler.
- **Interceptor *order* check.** Auth must wrap logging (so unauthenticated requests don't pollute logs). Currently V27 doesn't check interceptor ordering.
- **Cross-service interceptor consistency.** All `New*Handler` calls in a project should use the same interceptor set. Currently V27 checks each independently — a project that wires `AuthInterceptor` for users but skips it for orders has inconsistency that should be flagged.

## References

- [Connect-RPC — Implementing services (Go)](https://connectrpc.com/docs/go/serving-clients/) — Connect Authors, *continuously updated*, retrieved 2026-04-30. The handler signature V27 enforces.
- [Connect-RPC — Interceptors](https://connectrpc.com/docs/go/interceptors/) — Connect Authors, *continuously updated*, retrieved 2026-04-30. The `connect.WithInterceptors` API V27 looks for.
- [Connect-RPC — Error codes](https://connectrpc.com/docs/protocol/#error-codes) — Connect Authors, *continuously updated*, retrieved 2026-04-30. The 16 standard codes V27 pushes users toward.
- [Connect-RPC — `connect.NewError`](https://pkg.go.dev/connectrpc.com/connect#NewError) — Connect Authors, *continuously updated*, retrieved 2026-04-30.
- [protovalidate — Validating proto messages in Go](https://buf.build/docs/protovalidate/quickstart/go/) — Buf, *continuously updated*, retrieved 2026-04-30. The validation interceptor V27 looks for in the interceptor list.

## Examples

### ✓ Pass

```go
// server/internal/users/handler.go
type UserServiceServer struct {
    gqlClient gqlclient.Client
}

func (s *UserServiceServer) CreateUser(
    ctx context.Context,
    req *connect.Request[pb.CreateUserRequest],
) (*connect.Response[pb.CreateUserResponse], error) {
    user, err := s.gqlClient.CreateUser(ctx, req.Msg)
    if err != nil {
        if errors.Is(err, gqlclient.ErrUniqueViolation) {
            return nil, connect.NewError(connect.CodeAlreadyExists, err)
        }
        return nil, connect.NewError(connect.CodeInternal, err)
    }
    return connect.NewResponse(&pb.CreateUserResponse{User: user}), nil
}
```

```go
// server/internal/server/wire.go
mux.Handle(usersv1connect.NewUserServiceHandler(
    impl,
    connect.WithInterceptors(
        AuthInterceptor(jwtVerifier),
        LoggingInterceptor(logger),
        ValidationInterceptor(),
    ),
))
```

### ✗ Fail

```protobuf
service UserService {
  rpc UpdateUser(UpdateUserRequest) returns (UpdateUserResponse);
}
// no Go handler with `func (s *UserServiceServer) UpdateUser(...)`
// → V27-UNIMPLEMENTED-RPC
```

```go
// no WithInterceptors option
mux.Handle(usersv1connect.NewUserServiceHandler(impl))
                                              // → V27-NO-INTERCEPTORS
```

```go
mux.Handle(usersv1connect.NewUserServiceHandler(
    impl,
    connect.WithInterceptors(
        AuthInterceptor(jwtVerifier),
        LoggingInterceptor(logger),
                              // → V27-MISSING-VALIDATION-INTERCEPTOR
    ),
))
```

```go
func (s *UserServiceServer) GetUser(ctx, req) (...) {
    user, err := s.repo.Find(ctx, req.Msg.Id)
    if err != nil {
        return nil, err          // → V27-RAW-ERROR-RETURN
    }
    return connect.NewResponse(user), nil
}
```
