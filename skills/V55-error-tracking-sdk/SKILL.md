# V55 — error-tracking-sdk

> **Owner**: `hooks/validators/error_tracking_sdk.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `server/go.mod`, `**/go.mod`, `web/package.json`, `**/package.json`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V55-NO-GO-ERROR-TRACKING` | error | `server/go.mod` has no `github.com/getsentry/sentry-go` dependency **and** at least one `.go` file exists under `server/internal/` |
| `V55-NO-WEB-ERROR-TRACKING` | error | `web/package.json` exists but neither `dependencies` nor `devDependencies` contains `@sentry/react`, `@sentry/browser`, `@sentry/nextjs`, or `@sentry/vue` |

## Why this verifier exists

In production, two classes of failure are otherwise completely silent:

1. **Go panics and handler errors** — an unrecovered panic kills the goroutine and returns a 500; without error tracking, the only signal is a user complaint or a spike in error-rate metrics if the team has built those. Structured Sentry events capture the stack trace, request context, user ID, and environment, making root-cause analysis minutes instead of hours.

2. **Frontend JavaScript errors** — unhandled promise rejections, React error boundaries, and network failures are swallowed by the browser unless a global error handler forwards them. Sentry's browser SDK installs `window.onerror` and `window.onunhandledrejection` handlers automatically.

Medical and finance projects have an additional compliance dimension: audit trails often require that every unhandled error is captured, timestamped, and attributed to a release version. Sentry's release tracking and source-map upload satisfy this requirement cheaply.

V55 flags the absence of error tracking as an **error** (not a warning) because unlike observability (V49, deferred-optional), error capture is a baseline operational requirement — you cannot operate a production service safely without knowing when it fails.

## Design rationale

- **Error severity.** Unlike V49 (OTEL, warning), missing error tracking is not something teams should defer. A service with untracked panics is operationally blind in the most critical moment.
- **Go: internal/ guard.** If `server/go.mod` exists but `server/internal/` has no `.go` files, the project is likely a scaffolded starter with no business logic yet. Forcing Sentry setup before any handlers exist is premature. The guard requires at least one real `.go` file under `internal/` before flagging.
- **Web: devDependencies counts.** Sentry JS SDKs are compiled into the bundle at build time; whether they live in `dependencies` or `devDependencies` does not affect runtime behavior. Both buckets are checked.
- **Multiple Sentry JS packages accepted.** `@sentry/react` is the canonical choice for React apps, but `@sentry/browser` (vanilla), `@sentry/nextjs` (Next.js), and `@sentry/vue` (Vue) are all equivalent — any one of them satisfies the check.
- **GlitchTip compatibility.** GlitchTip is a self-hosted Sentry-compatible backend. It accepts events from the same `getsentry/sentry-go` and `@sentry/react` SDKs, so teams using GlitchTip for compliance/on-prem reasons are automatically satisfied.
- **No version pinning.** V55 does not care which version of the SDK is used, only that the dependency is declared.
- **Malformed package.json.** A JSON parse error is caught and logged; the validator returns no findings rather than crashing the hook pipeline.

## How it checks (implementation)

Lives in `hooks/validators/error_tracking_sdk.py`. Both `validate_file` (Tier 2) and `validate_project` (Tier 3) delegate to the same internal `_check(ctx)` function so behaviour is identical regardless of which tier fires.

### Top-level

```python
def _check(ctx: ProjectContext) -> list[Finding]:
    findings = []
    findings.extend(_go_check(ctx))
    findings.extend(_web_check(ctx))
    return findings
```

### Go check pseudocode

```python
def _go_check(ctx):
    go_mod = find server/go.mod via ctx.server_dir or <root>/server/go.mod
    if go_mod is None:
        return []  # not a Go project

    if not any .go file under go_mod.parent / "internal":
        return []  # empty starter, skip

    text = go_mod.read_text()
    if re.search(r"github\.com/getsentry/sentry-go\b", text):
        return []  # found

    return [Finding(rule="V55-NO-GO-ERROR-TRACKING", severity="error", ...)]
```

### Web check pseudocode

```python
def _web_check(ctx):
    pkg_json = <root>/web/package.json
    if not pkg_json.exists():
        return []

    try:
        pkg = json.loads(pkg_json.read_text())
    except json.JSONDecodeError:
        logger.exception(...)
        return []  # malformed — no crash

    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    sentry_packages = {"@sentry/react", "@sentry/browser", "@sentry/nextjs", "@sentry/vue"}
    if sentry_packages & set(all_deps):
        return []  # found

    return [Finding(rule="V55-NO-WEB-ERROR-TRACKING", severity="error", ...)]
```

## Could be more effective

- **Verify Sentry is initialized.** Having `getsentry/sentry-go` in go.mod doesn't mean `sentry.Init()` is ever called. V55 could grep `cmd/server/main.go` for `sentry.Init(` (similar to how V49 checks `otelhttp` import).
- **DSN environment variable check.** Sentry is useless without a DSN. V55 could verify that `SENTRY_DSN` (Go) or `VITE_SENTRY_DSN` (web) appears in `.env.example` so developers know to set it.
- **Release tracking.** Sentry error grouping across deploys requires a `Release` field set to the git SHA or semver. V55 could check that `sentry.ClientOptions{Release: ...}` is populated.
- **Source map upload.** Frontend Sentry events without source maps show minified stack traces. V55 could check that `@sentry/vite-plugin` or equivalent is in `devDependencies` and configured in `vite.config.ts`.
- **Sampling rate validation.** A `TracesSampleRate` of `1.0` in production will generate excessive volume and cost. V55 could warn when the sample rate is set to 1.0 in non-development code paths.
- **GlitchTip / alternate backends.** The `getsentry/sentry-go` regex catches both Sentry SaaS and GlitchTip. If teams use other SDK-compatible backends (e.g., `bugsink`), a configurable allowlist would reduce false positives.

## References

- [Sentry Go SDK](https://docs.sentry.io/platforms/go/) — Sentry, continuously updated, retrieved 2026-04-30. Official quickstart and configuration reference for `getsentry/sentry-go`.
- [Sentry React SDK](https://docs.sentry.io/platforms/javascript/guides/react/) — Sentry, continuously updated, retrieved 2026-04-30. Official guide for `@sentry/react` setup including `Sentry.init()`, `BrowserTracing`, and error boundaries.
- [GlitchTip — Sentry-compatible self-hosted backend](https://glitchtip.com/documentation) — GlitchTip, continuously updated, retrieved 2026-04-30. Drop-in replacement for Sentry SaaS; accepts events from standard Sentry SDKs.

## Examples

### ✓ Pass

```
# server/go.mod
module myapp

go 1.21

require (
    github.com/getsentry/sentry-go v0.27.0
    github.com/lib/pq v1.10.0
)
```

```go
// server/cmd/server/main.go
import (
    "os"
    "time"
    "github.com/getsentry/sentry-go"
    sentryhttp "github.com/getsentry/sentry-go/http"
)

func main() {
    sentry.Init(sentry.ClientOptions{
        Dsn:              os.Getenv("SENTRY_DSN"),
        TracesSampleRate: 0.1,
    })
    defer sentry.Flush(2 * time.Second)

    mux := http.NewServeMux()
    handler := sentryhttp.New(sentryhttp.Options{}).Handle(mux)
    http.ListenAndServe(":8080", handler)
}
```

```json
// web/package.json
{
  "dependencies": {
    "react": "^18.3.0",
    "@sentry/react": "^7.119.0"
  }
}
```

```ts
// web/src/main.tsx
import * as Sentry from '@sentry/react';

Sentry.init({
  dsn: import.meta.env.VITE_SENTRY_DSN,
  integrations: [Sentry.browserTracingIntegration()],
  tracesSampleRate: 0.1,
});
```

### ✗ Fail

```
# server/go.mod (no sentry-go)
module myapp

go 1.21

require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
)
# AND server/internal/handler.go exists
# → V55-NO-GO-ERROR-TRACKING
```

```json
// web/package.json (no Sentry SDK)
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "axios": "^1.6.0"
  }
}
// → V55-NO-WEB-ERROR-TRACKING
```
