# V56 — prometheus-metrics-endpoint

> **Owner**: `hooks/validators/prometheus_metrics_endpoint.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `server/go.mod`, `**/go.mod`, `server/cmd/**/*.go`, `**/cmd/**/*.go`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V56-NO-PROMETHEUS-SDK` | warning | `server/go.mod` lacks `github.com/prometheus/client_golang` in the `require` block AND at least one `.go` file exists under `cmd/` (HTTP server project confirmed). |
| `V56-PROMETHEUS-NOT-WIRED` | warning | SDK is declared in go.mod but no non-test `cmd/**/*.go` file registers a `/metrics` route. |

## Why this verifier exists

Distributed tracing (V49, OTel) and metrics (V56, Prometheus) are complementary, not duplicate:

- **V49** enforces *traces* — request spans, DB query durations, propagation context. Answers "where did the time go for this specific request?"
- **V56** enforces *metrics* — counters, gauges, histograms aggregated across all requests. Answers "what is my service's Rate / Error rate / Duration (RED) right now?"

Both must be present in a production service. A service with only traces has no dashboards, no alerting thresholds, and no SLO tracking. A service with only metrics cannot diagnose individual slow requests.

Evidence pattern: projects add `github.com/prometheus/client_golang` as a dependency but forget to wire `promhttp.Handler()` to the `/metrics` path, leaving the Prometheus scraper with nothing to scrape. V56 flags both the missing SDK and the unwired SDK as separate actionable warnings.

The [Google SRE Book — Four Golden Signals](https://sre.google/sre-book/monitoring-distributed-systems/#xref_monitoring_golden-signals) (published 2016, retrieved 2026-04-30) defines latency, traffic, errors, and saturation as the four signals every service must measure. Prometheus is the standard mechanism for exposing these signals for scraping. Without a `/metrics` endpoint none of the four signals are observable.

## Design rationale

- **Warning, not error.** Some projects may expose metrics via a sidecar or a push gateway rather than a pull endpoint. The flag alerts; enforcement is optional.
- **Two-part check: SDK + route.** Having `client_golang` in go.mod is not enough — many projects add the dependency but never call `promhttp.Handler()`. V56 checks both: (a) the dependency is declared, **and** (b) the `/metrics` route is actually registered.
- **Not-applicable guard.** If `server/` doesn't exist or there are no non-test `.go` files under `cmd/`, the check returns `[]`. This prevents false positives on worker-only services that have no HTTP listener at all.
- **Test file exclusion.** `*_test.go` files are skipped for the route check. A route registered only in a test does not satisfy the production wiring requirement.
- **Router-agnostic detection.** The route check recognises `mux.Handle`, `mux.HandleFunc`, `r.Handle`, `r.Get`, `http.Handle`, and a fallback: any file that both imports `client_golang` and contains the string `"/metrics"`. This covers stdlib `net/http`, chi, gorilla/mux, and custom routers without per-router special-casing.

## How it checks

Lives in `hooks/validators/prometheus_metrics_endpoint.py`.

### Top-level

```python
def _check(ctx):
    go_mod_path = _find_go_mod(ctx)
    if go_mod_path is None:
        return []
    # Guard: not applicable if no cmd/**/*.go
    if not has_cmd_go_files(go_mod_path):
        return []
    go_mod_text = go_mod_path.read_text()
    if not _has_prometheus_sdk(go_mod_text):
        return [Finding(rule="V56-NO-PROMETHEUS-SDK", ...)]
    if not _has_metrics_route_in_cmd(go_mod_path):
        return [Finding(rule="V56-PROMETHEUS-NOT-WIRED", ...)]
    return []
```

### Step 1 — SDK presence in `go.mod`

```python
_PROM_SDK_RE = re.compile(r"github\.com/prometheus/client_golang")

def _has_prometheus_sdk(go_mod_text: str) -> bool:
    return bool(_PROM_SDK_RE.search(go_mod_text))
```

If absent and the project has an HTTP binary (`cmd/**/*.go` exists) → `V56-NO-PROMETHEUS-SDK`.

### Step 2 — `/metrics` route registration

```python
_METRICS_ROUTE_RE = re.compile(
    r'(?:mux|r|router|http|s|srv)'
    r'\.(?:Handle(?:Func)?|Get|Post)\s*\(\s*"/metrics"'
)
```

Walk all non-test `.go` files under `cmd/`. Match the route regex, or fall back to: file imports `client_golang` AND contains `"/metrics"` literal.

### Route patterns recognised

| Pattern | Router |
|---|---|
| `mux.Handle("/metrics", ...)` | stdlib ServeMux |
| `mux.HandleFunc("/metrics", ...)` | stdlib ServeMux |
| `r.Handle("/metrics", ...)` | chi / gorilla |
| `r.Get("/metrics", ...)` | chi |
| `http.Handle("/metrics", ...)` | stdlib default mux |
| File imports `client_golang` + `"/metrics"` literal | any custom wiring |

## Could be more effective

- **Auth middleware verification.** `/metrics` often contains sensitive cardinality data (user IDs, tenant names in labels). V56 could detect whether the route is placed behind an auth middleware or internal-only listener.
- **Custom registry check.** Projects that use `prometheus.NewRegistry()` instead of the default registry may call `promhttp.HandlerFor(reg, ...)`. V56 could detect both forms.
- **Histogram bucket validation.** Default `prometheus.DefBuckets` are often wrong for latency distributions. A future check could flag `prometheus.NewHistogramVec` calls that don't specify custom buckets.
- **Label cardinality guard.** High-cardinality labels (user IDs, request IDs) cause Prometheus OOM. V56 could scan `prometheus.NewCounterVec` / `NewHistogramVec` calls for suspicious label names.
- **Scrape config cross-check.** Verify that a Prometheus scrape config (`prometheus.yml` or ServiceMonitor CRD) actually targets this service's `/metrics` path.

## References

- [Google SRE Book — Four Golden Signals](https://sre.google/sre-book/monitoring-distributed-systems/#xref_monitoring_golden-signals) — Google, published 2016, retrieved 2026-04-30. Defines latency, traffic, errors, and saturation as the canonical observability signals.
- [Prometheus — Metric naming best practices](https://prometheus.io/docs/practices/naming/) — Prometheus, continuously updated, retrieved 2026-04-30. Naming conventions, label cardinality, and metric type selection.
- [prometheus/client_golang README](https://github.com/prometheus/client_golang) — Prometheus, continuously developed since 2014, retrieved 2026-04-30. Official Go client; `promhttp.Handler()` is the standard scrape endpoint.

## Examples

### Pass

```go
// server/cmd/server/main.go
import (
    "net/http"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
    mux := http.NewServeMux()
    mux.Handle("/metrics", promhttp.Handler())
    mux.HandleFunc("/livez", livenessHandler)
    mux.HandleFunc("/readyz", readinessHandler)
    http.ListenAndServe(":8080", mux)
}
```

```
# server/go.mod
require (
    github.com/prometheus/client_golang v1.19.0
)
```

```go
// chi router — also passes
r := chi.NewRouter()
r.Handle("/metrics", promhttp.Handler())
```

### Fail

```
# server/go.mod (no prometheus)
require (
    github.com/lib/pq v1.10.0
    google.golang.org/protobuf v1.28.0
    // github.com/prometheus/client_golang not present
)
# → V56-NO-PROMETHEUS-SDK
```

```go
// server/cmd/server/main.go (SDK in go.mod but /metrics not wired)
import (
    "net/http"
    // promhttp is imported somewhere but never registered
)

func main() {
    mux := http.NewServeMux()
    // /metrics route missing
    http.ListenAndServe(":8080", mux)
}
// → V56-PROMETHEUS-NOT-WIRED
```
