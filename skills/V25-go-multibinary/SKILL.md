# V25 — go-multibinary

> **Owner**: `hooks/validators/go_multibinary.py`
> **Tier**: 2 (PostToolUse) and 3 (Stop) — same project sweep on both because every check is project-level.
> **File patterns**: `**/cmd/**/main.go`, `**/tools.go`, `**/.air*.toml`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V25-NO-GRACEFUL-SHUTDOWN` | warning | A `cmd/<name>/main.go` lacks `signal.NotifyContext(...)` or `signal.Notify(... SIGTERM ...)`. SIGTERM from docker / k8s will hard-kill in-flight work. |
| `V25-NO-TOOLS-FILE` | warning | The Go root has no `tools.go`. Dev-only deps (buf, golangci-lint, mockgen, ...) end up at per-developer versions. |
| `V25-TOOLS-NO-BUILD-TAG` | warning | `tools.go` exists but lacks `//go:build tools` (or `// +build tools`). Without the tag, dev tool deps leak into production builds. |
| `V25-AIR-DEAD-PATH` | warning | A `.air.<name>.toml` references `cmd/<name>/` that no longer exists (e.g., after rename). |
| `V25-CMD-NO-AIR-CONFIG` | warning | A `cmd/<name>/` exists with no matching `.air.<name>.toml`. Hot-reload won't work for that binary. The bare `.air.toml` is allowed to cover `cmd/server/`. |

V25 only fires when `cmd/` is detected (in `server/` or root). Single-binary repos at root pay zero cost.

## Why this verifier exists

Multi-binary Go monorepos accumulate three patterns that AI agents (and humans) silently break:

1. **Forgot graceful shutdown.** A new worker is added; the developer copies a stub `func main() { server.Run() }` from a tutorial. SIGTERM kills it mid-DB-transaction; data is half-written; queue messages aren't acked. The bug manifests only on production deploys.
2. **No tools.go (or no build tag).** A developer installs `genqlient` locally, generates code, ships. Another developer clones, runs the same command, gets a different `genqlient` version (whatever's on PATH). Reproducibility is gone.
3. **Air config orphans.** Renaming `cmd/old-worker/` to `cmd/new-worker/` is a one-grep refactor *except* for the `.air.old-worker.toml` left behind. Or adding a new `cmd/processor/` and forgetting to add `.air.processor.toml` — hot-reload silently doesn't work for the new binary.

V25 codifies all three so the regression dies at hook-time.

## Design rationale

- **All rules are warnings.** Each has legitimate edge cases (single-binary projects with no Air; tools.go genuinely not needed; a `cmd/migration/` that's run-once and shouldn't long-run for SIGTERM). Hard-failing would erode trust.
- **`signal.NotifyContext` OR `signal.Notify(... SIGTERM)` accepted.** The newer NotifyContext (Go 1.16+) is preferred but the older form is just as correct. V25 accepts either.
- **`tools.go` build tag accepts both forms.** `//go:build tools` is the modern (Go 1.17+) form; `// +build tools` is the legacy form. Either is enough; V25 doesn't push the migration.
- **Bare `.air.toml` covers `cmd/server/`.** The user's monorepo has `.air.toml` (canonical server) plus `.air.outbound.toml` (worker). V25 allows the bare name to cover `cmd/server/` so users don't need to rename to `.air.server.toml`.
- **Air mapping check via filename suffix + cmd/bin path.** V25 reads the `bin = "./tmp/<name>"` and `cmd = "go build ... ./cmd/<name>"` lines from each Air toml — both are common idiomatic forms.
- **Project-level scan, no per-cmd Tier 2 optimization.** Every rule depends on cross-file state (cmd dirs vs air configs); per-edit narrowing isn't a meaningful win.

## How it checks (implementation)

Lives in `hooks/validators/go_multibinary.py`.

### `_go_root(ctx)` — gating predicate

```python
def _go_root(ctx):
    candidates = []
    if ctx.server_dir is not None:
        candidates.append(ctx.server_dir)
    candidates.append(ctx.project_root)
    for d in candidates:
        if (d / "cmd").is_dir():
            return d
    return None
```

### `_enumerate_cmd_dirs(go_root)` and `_enumerate_air_configs(go_root)`

```python
def _enumerate_cmd_dirs(go_root):
    return [d for d in sorted((go_root / "cmd").iterdir())
            if d.is_dir() and (d / "main.go").is_file()]

def _enumerate_air_configs(go_root):
    return [p for p in sorted(go_root.iterdir())
            if p.is_file() and p.name.startswith(".air") and p.suffix == ".toml"]
```

### `_check_graceful_shutdown(cmd_dirs)` — V25-NO-GRACEFUL-SHUTDOWN

```python
SIGNAL_PATTERNS = (
    re.compile(r"\bsignal\.NotifyContext\s*\("),
    re.compile(r"\bsignal\.Notify\s*\([^)]*SIGTERM"),
)

for cmd_dir in cmd_dirs:
    src = (cmd_dir / "main.go").read_text()
    if any(p.search(src) for p in SIGNAL_PATTERNS):
        continue                       # has SIGTERM handler ✓
    yield Finding(rule="V25-NO-GRACEFUL-SHUTDOWN", file=str(cmd_dir / "main.go"), ...)
```

### `_check_tools_go(go_root)` — V25-NO-TOOLS-FILE / V25-TOOLS-NO-BUILD-TAG

```python
tools_path = go_root / "tools.go"
if not tools_path.is_file():
    yield Finding(rule="V25-NO-TOOLS-FILE", ...)
    return

src = tools_path.read_text()
first_lines = src.splitlines()[:3]
has_new_tag = any("//go:build tools" in line for line in first_lines)
has_legacy_tag = any("// +build tools" in line for line in first_lines)
if not (has_new_tag or has_legacy_tag):
    yield Finding(rule="V25-TOOLS-NO-BUILD-TAG", ...)
```

### `_check_air_mapping(go_root, cmd_dirs)` — V25-AIR-DEAD-PATH / V25-CMD-NO-AIR-CONFIG

```python
AIR_BIN = re.compile(r'^\s*bin\s*=\s*["\']\s*\.?/?(?:tmp|bin)/(?P<name>[\w\-]+)\s*["\']', re.MULTILINE)
AIR_CMD = re.compile(r'\./cmd/(?P<name>[\w\-]+)', re.MULTILINE)

cmd_names = {d.name for d in cmd_dirs}

# 1. Each .air toml → which cmd does it reference?
air_to_cmd = {}
for air in _enumerate_air_configs(go_root):
    content = air.read_text()
    cmd_match = AIR_CMD.search(content)        # try cmd path first
    bin_match = AIR_BIN.search(content)        # fall back to bin path
    air_to_cmd[air] = (cmd_match or bin_match).group("name") if (cmd_match or bin_match) else None

# 2. Air → dead cmd
for air, ref in air_to_cmd.items():
    if ref and ref not in cmd_names:
        yield Finding(rule="V25-AIR-DEAD-PATH", file=str(air), ...)

# 3. cmd without air (with .air.toml-covers-cmd/server exemption)
referenced = {ref for ref in air_to_cmd.values() if ref}
for cmd in cmd_dirs:
    if cmd.name in referenced:
        continue
    if cmd.name == "server" and any(a.name == ".air.toml" for a in air_configs):
        continue
    yield Finding(rule="V25-CMD-NO-AIR-CONFIG", file=str(cmd / "main.go"), ...)
```

### Could be more effective

- **Real Go AST for shutdown detection.** A regex misses `signal.NotifyContext` invocations behind a helper (`server.Setup(ctx)` that internally registers). Using `go/parser` + symbol resolution would close this; cost is the Python↔Go bridge.
- **Verify the shutdown context is actually plumbed through.** Currently V25 only checks "is SIGTERM handler registered". The deeper bug is "ctx is registered but never passed to `server.Run`". A minimal data-flow check would catch the second case.
- **Per-cmd-dir health check.** Each `cmd/<name>/main.go` should have a non-trivial body (not just `func main() { fmt.Println("hello") }`). A line-count or "does it actually start something" check would surface stub binaries left from scaffolding.
- **`.air.toml` content validation.** V25 only matches the cmd/bin path; it doesn't verify other Air settings (build flags, log level, watch dirs). A schema check would surface misconfigurations like watching `node_modules/`.
- **`Makefile` / `justfile` cmd target consistency.** Each cmd should have a `make run-<name>` or `just run-<name>` target. Cross-checking would catch "I added a new cmd but the Makefile doesn't know about it" cases.

## References

- [Go modules — How can I track tool dependencies for a module?](https://github.com/golang/go/wiki/Modules#how-can-i-track-tool-dependencies-for-a-module) — Go team, *continuously updated*, retrieved 2026-04-30. The canonical `tools.go` + `//go:build tools` pattern V25 enforces.
- [Go — `os/signal` documentation](https://pkg.go.dev/os/signal) — Go team, *continuously updated*, retrieved 2026-04-30. The `signal.NotifyContext` API V25 looks for.
- [CNCF — Graceful shutdown patterns](https://www.cncf.io/blog/2023/04/27/best-practices-for-implementing-graceful-shutdown-in-kubernetes/) — CNCF, *published 2023-04-27*, retrieved 2026-04-30. Why SIGTERM handling matters in containerized deployments.
- [Air — Live reload for Go apps](https://github.com/air-verse/air) — Air maintainers, *continuously maintained*, retrieved 2026-04-30. The `.air.toml` format and the `bin` / `cmd` keys V25 parses.
- [Effective Go — Concurrency](https://go.dev/doc/effective_go#concurrency) — Go team, *continuously updated*, retrieved 2026-04-30. The idiomatic baseline for context-driven shutdown.

## Examples

### ✓ Pass

```go
// server/cmd/server/main.go
package main

import (
    "context"
    "os/signal"
    "syscall"
)

func main() {
    ctx, cancel := signal.NotifyContext(context.Background(),
        syscall.SIGINT, syscall.SIGTERM)
    defer cancel()

    srv := newServer()
    go srv.Run(ctx)

    <-ctx.Done()
    srv.Shutdown(context.Background())
}
```

```go
// server/tools.go
//go:build tools
// +build tools

package tools

import (
    _ "github.com/Khan/genqlient"
    _ "github.com/bufbuild/buf/cmd/buf"
    _ "github.com/golangci/golangci-lint/cmd/golangci-lint"
)
```

```toml
# server/.air.toml — canonical server
[build]
cmd = "go build -o ./tmp/server ./cmd/server"
bin = "./tmp/server"
```

```toml
# server/.air.outbound.toml — finance-outbound-worker
[build]
cmd = "go build -o ./tmp/outbound ./cmd/finance-outbound-worker"
bin = "./tmp/outbound"
```

### ✗ Fail

```go
// cmd/worker/main.go — no SIGTERM handler
package main
func main() { runWorker() }              // → V25-NO-GRACEFUL-SHUTDOWN
```

```go
// tools.go — no build tag
package tools
import _ "github.com/Khan/genqlient"      // → V25-TOOLS-NO-BUILD-TAG
```

```
server/
  cmd/
    server/main.go
    new-worker/main.go                    # added recently
  .air.toml                                # covers cmd/server/
  .air.legacy.toml                         # → V25-AIR-DEAD-PATH (cmd/legacy/ doesn't exist)
                                           # → V25-CMD-NO-AIR-CONFIG for cmd/new-worker/
```
