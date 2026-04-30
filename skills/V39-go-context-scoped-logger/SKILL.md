# V39 — go-context-scoped-logger

> **Owner**: `hooks/validators/go_context_scoped_logger.py` (planned, not yet implemented)
> **Tier**: 2 (PostToolUse)
> **File patterns**: `server/internal/**/*.go` (excluding `*_test.go`)

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V39-GLOBAL-LOGGER-MISUSE` | warning | A `.go` file in `server/internal/` contains calls to global logger (`log.Info`, `log.Error`, `log.Warn`, `log.Debug`) but does not retrieve a context-scoped logger via `zerolog.Ctx(ctx)` anywhere in the same file. |

## Why this verifier exists

Distributed request tracing requires every log line to carry the same `request_id` so logs from multiple services can be stitched together. Three failure modes emerge when developers use global loggers:

1. **Manual correlation ID threading.** Every log call must manually pass `Str("request_id", rid)` or developers will forget and ship logs without correlation. Evidence: `server/internal/middleware/request_logging.go:40` and `server/internal/middleware/connect_logging.go:34` both use bare `log.Info()` calls with no context; the request ID must be repeated at each call site, violating DRY.

2. **Silent correlation gaps.** A repository function logs an error without the request ID because it wasn't passed the context. The error appears in logs but can't be correlated to the originating request. Developers don't realize the correlation is missing.

3. **Audit trail fragmentation.** In a medical/finance context, incomplete request tracing is a compliance risk — you can't reconstruct what happened in a user's transaction across service boundaries.

The best practice is to store the logger in the context once at request entry (`ctx = logger.WithContext(ctx)`) and retrieve it downstream (`zerolog.Ctx(ctx)`). Every log then automatically carries the request ID.

[zerolog README — Contextual Logging](https://github.com/rs/zerolog#contextual-logging) — rs/zerolog authors, *continuously developed since 2017-05*, retrieved 2026-04-30. [Go blog: Structured Logging with slog](https://go.dev/blog/slog) — Go team, published 2023-08-22, retrieved 2026-04-30 — the same pattern applies to slog's API.

## Design rationale

- **Rule is warning, not error.** Some files legitimately never handle context (e.g., utility functions that don't touch request context). A warning signals "review this carefully" without blocking.
- **Only non-test files are checked.** Test setup often uses global loggers for simplicity; V39 excludes `*_test.go`.
- **File must contain both global calls AND lack context retrieval.** A file that uses global logger but has zero `zerolog.Ctx()` calls is flagged. This avoids false negatives where a file mixes patterns.
- **"Global logger" is straightforward:** `log.Info`, `log.Error`, `log.Warn`, `log.Debug` (or with suffixes like `Infof`, `Errorw`). Calls to functions like `log.WithContext()` or `log.With()` are not global and don't trigger this rule.
- **Detection is regex-based on function calls.** No AST walk; simpler, faster, lower false-negative rate on real code.

## How it checks (implementation plan)

Lives in `hooks/validators/go_context_scoped_logger.py`. Scans `.go` files under `server/internal/` excluding test files.

### Top-level check

```python
def validate_file(self, ctx, file_path):
    if file_path.name.endswith("_test.go"):
        return []
    
    findings = []
    src = file_path.read_text()
    
    # If file has global logger calls but no context-scoped retrieval, flag it
    if self._has_global_logger_calls(src) and not self._has_zerolog_ctx(src):
        findings.append(Finding(
            rule="V39-GLOBAL-LOGGER-MISUSE",
            file=str(file_path),
            message="File uses global logger calls (log.Info, log.Error, etc.) "
                    "but does not retrieve context-scoped logger via zerolog.Ctx(ctx)"
        ))
    
    return findings
```

### `_has_global_logger_calls(src)` — detect global logger usage

```python
GLOBAL_LOGGER_CALLS = re.compile(
    r"\blog\.(Info|Error|Warn|Debug|Infof|Errorf|Warnf|Debugf|Errorw|Infow|Warnw|Debugw)"
)

def _has_global_logger_calls(self, src: str) -> bool:
    return bool(GLOBAL_LOGGER_CALLS.search(src))
```

### `_has_zerolog_ctx(src)` — detect context-scoped logger retrieval

```python
ZEROLOG_CTX = re.compile(r"\bzerolog\.Ctx\s*\(\s*\w+\s*\)")

def _has_zerolog_ctx(self, src: str) -> bool:
    return bool(ZEROLOG_CTX.search(src))
```

## Could be more effective

- **Check slog patterns.** Go 1.21+ `slog` package uses `slog.FromContext()` and `slog.InfoContext()`. V39 could support both zerolog and slog.
- **Validate context is threaded correctly.** Check that `ctx = logger.WithContext(ctx)` assignments exist at request entry points (middleware), not just that `zerolog.Ctx()` is called.
- **Detect partial context wrapping.** Flag files where some handlers use context-scoped loggers and others don't — inconsistency is a code smell.
- **Inspect interceptors.** Automatically verify that the authentication/logging middleware actually calls `logger.WithContext()` before passing the context downstream.
- **Follow context flow.** Track context parameters through function calls to ensure they're not dropped mid-handler (e.g., calling a repo function without `ctx`).

## References

- [zerolog — Contextual Logging](https://github.com/rs/zerolog#contextual-logging) — rs/zerolog Authors, *continuously developed since 2017-05*, retrieved 2026-04-30. The `Logger.WithContext()` and `Ctx()` API.
- [Go blog: Structured Logging with slog](https://go.dev/blog/slog) — Go Authors, published 2023-08-22, retrieved 2026-04-30. The context-scoped pattern applied to Go's standard library logging.
- [zerolog — Examples](https://github.com/rs/zerolog#example) — rs/zerolog Authors, *continuously updated*, retrieved 2026-04-30. Real-world usage patterns.
- [OpenTelemetry — Context Propagation](https://opentelemetry.io/docs/reference/specification/context/api-requirements/) — OpenTelemetry, *continuously updated*, retrieved 2026-04-30. The context-propagation standard that distributed logging implements.

## Examples

### ✓ Pass

```go
// server/internal/handler/user.go
package handler

import (
    "github.com/rs/zerolog"
)

func (h *Handler) CreateUser(ctx context.Context, req *pb.CreateUserRequest) (*pb.CreateUserResponse, error) {
    logger := zerolog.Ctx(ctx)  // ✓ Retrieve context-scoped logger
    
    user, err := h.repo.Create(ctx, req)
    if err != nil {
        logger.Error().Err(err).Msg("Failed to create user")
        return nil, err
    }
    
    logger.Info().Interface("user", user).Msg("User created")
    return &pb.CreateUserResponse{User: user}, nil
}
```

```go
// server/internal/middleware/logging.go
package middleware

import (
    "github.com/rs/zerolog"
)

func LoggingInterceptor(logger zerolog.Logger) connect.Interceptor {
    return func(next connect.UnaryFunc) connect.UnaryFunc {
        return func(ctx context.Context, req connect.AnyRequest) (connect.AnyResponse, error) {
            rid := uuid.New().String()
            // ✓ Store logger in context at request entry
            ctx = logger.With().Str("request_id", rid).Logger().WithContext(ctx)
            
            res, err := next(ctx, req)
            return res, err
        }
    }
}
```

### ✗ Fail

```go
// server/internal/repository/user.go
package repository

import "github.com/rs/zerolog/log"

func (r *Repo) GetUser(ctx context.Context, id string) (*User, error) {
    user, err := r.db.Query(ctx, id)
    if err != nil {
        log.Error().Err(err).Msg("Failed to query user")  // ✗ Global logger
        return nil, err
    }
    
    log.Info().Interface("user", user).Msg("User retrieved")  // ✗ Global logger
    return user, nil
}

// No zerolog.Ctx(ctx) call in this file
// → V39-GLOBAL-LOGGER-MISUSE
```

```go
// server/internal/service/payment.go
package service

import "github.com/rs/zerolog/log"

func (s *Service) ProcessPayment(ctx context.Context, req *PaymentRequest) error {
    // Multiple global log calls
    log.Info().Msg("Starting payment")
    
    result, err := s.gateway.Charge(ctx, req.Amount)
    if err != nil {
        log.Error().Err(err).Msg("Charge failed")  // ✗ No request_id attached
        return err
    }
    
    log.Info().Msg("Payment succeeded")
    // ✗ V39-GLOBAL-LOGGER-MISUSE (no zerolog.Ctx call)
    return nil
}
```
