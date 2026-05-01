# Changelog

All notable changes to verifiers are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The audit IDs cited below (P0-x, P1-x, P2-x) refer to the project-improvement
audit completed in 2026-04. They're kept here so future commits can link to
the original rationale.

## [Unreleased]

### Changed (Phase65 — single-walk project file index eliminates GIL+IO contention)

Phase 64 made the verifier suite **18% faster on warm runs** (97s → 80s
on ax-finance-project, 21k+ files). Phase 65 takes it from there to
**93% faster** (80s → 6s) by fixing the actual bottleneck the Phase 64
benchmark exposed.

#### The bottleneck

Seven Tier 3 validators (V05/V14/V15/V38/V44/V45/V58) each ran their
own ``Path.glob("**/...")`` walk to find target files (Dockerfiles,
docker-compose YAML, source files, .golangci.yml). On a 21k-file
monorepo a single walk takes ~1.3 s; six concurrent walks via
``ThreadPoolExecutor(8w)`` measured **~16 s each**:

- ``Path.glob`` is a pure-Python iterator that holds the GIL during
  most of its work. Six threads competing for one GIL serialize each
  other.
- macOS APFS queues concurrent ``stat()`` syscalls under load, so the
  kernel work also serialized.

Result: the actual analysis (read 7 Dockerfiles + parse) was ~50 ms
per validator, but each validator's ``duration_ms`` measurement
included the walk wait. The six walk-heavy validators alone ate
~96 s of wall-clock per Stop hook.

#### The fix

New ``lib/file_index.py`` — ``ProjectFileIndex.build(root, exclude_globs)``
walks the project ONCE per Stop hook with ``os.walk(followlinks=False)``
and ``dirnames[:] = [...]`` directory-level pruning (an interface
``Path.glob`` doesn't expose). The index has two lookup tables:

- ``by_ext: dict[str, list[FileEntry]]`` — ``".go"`` → all .go files
- ``by_name: dict[str, list[FileEntry]]`` — ``"Dockerfile"`` → ...

``ProjectContext.file_index`` (a ``functools.cached_property``) lazily
builds the index on first access, then memoizes for the lifetime of
the ``ctx`` (one Stop hook = one fresh ``ctx``, so no stale-cache
risk across hook invocations).

#### What gets pruned

1. **Hardcoded ``DEFAULT_PRUNE_NAMES``** — directory names that never
   contain user code: ``.git``, ``.hg``, ``.svn``, ``node_modules``,
   ``vendor``, ``__pycache__``, ``.venv``, ``.tox``, ``.mypy_cache``,
   ``.pytest_cache``, ``.ruff_cache``, ``.next``, ``.turbo``. Pruned
   regardless of user config.
2. **User-configured ``exclude.paths``** — gitignore-style globs from
   ``.verifiers/config.yaml`` get translated to directory prefixes
   (``vendor/**`` → ``vendor``, ``web/build/**`` → ``web/build``,
   ``**/__generated__/**`` → ``**/__generated__`` any-depth basename).
   Path-prefix or any-depth-basename match → directory pruned mid-walk.

#### What changed in each validator

Each migrated validator went from running its own ``Path.glob`` to
querying ``ctx.file_index``:

| V-ID | Before | After |
|------|--------|-------|
| V05 | 4× ``ctx.project_root.glob("**/...")`` | ``ctx.file_index.find_by_pattern(...)`` × 2 |
| V14 | ``directory.rglob(glob_pattern)`` per language | ``ctx.file_index.find_by_pattern(*globs)`` + relative_to filter |
| V15 | ``directory.rglob(glob_pattern)`` per language | same as V14 |
| V38 | ``root.rglob(".golangci.yaml")`` | ``ctx.file_index.find_by_pattern(".golangci.yaml", ".golangci.yml")`` |
| V44 | 2× ``root.glob("**/Dockerfile*")`` | ``ctx.file_index.find_by_pattern("Dockerfile*", "*.Dockerfile")`` |
| V45 | 2× ``ctx.project_root.glob("**/Dockerfile*")`` | same |
| V58 | 2× ``root.glob("**/Dockerfile*")`` | same |

V14/V15 preserve their language-directory scoping (``*.go`` only under
``ctx.server_dir``, etc.) by filtering index results with
``Path.relative_to(directory)``.

#### Phase 63 cache hash also benefits

``lib.tier_cache.compute_input_hash`` used to do its own walk per
validator. Phase 65 makes it delegate to
``ProjectFileIndex.hash_for_patterns()`` so the hash compute also
shares the single walk. Stop validator now calls
``ctx.file_index.hash_for_patterns(v.file_patterns)`` directly,
eliminating 32 redundant walks per Stop hook.

#### Measured impact (ax-finance-project, 102,975 entries / 91,753 in node_modules)

| Scenario | Phase 64 | Phase 65 | Speedup |
|----------|---------:|---------:|--------:|
| COLD (caches wiped) | 96,902 ms | **6,182 ms** | **15.7×** |
| WARM #1 | 79,531 ms | **6,186 ms** | **12.9×** |
| WARM #2 | 79,929 ms | **5,966 ms** | **13.4×** |
| WARM #3 | 86,354 ms | **6,112 ms** | **14.1×** |
| EDIT 1 .go file | 79,364 ms | **5,791 ms** | **13.7×** |
| ALL CACHES OFF | 97,429 ms | **6,288 ms** | **15.5×** |

**74 seconds saved per Stop hook on this project.** Per-validator:

| V-ID | Phase 64 (cold) | Phase 65 (cold) | Notes |
|------|---------------:|---------------:|-------|
| V14-complexity-guard | 37,893 ms | **669 ms** | Phase 64.4 + 65 combined: 56× |
| V05-docker | 37,510 ms | ~50 ms | walk was the cost |
| V15-dependency-guard | 33,204 ms | (skipped or ~700 ms) | |
| V38-golangci-strictness | 23,507 ms | **7 ms** | walk was 100% of the cost |
| V44-dockerfile-base-digest | 23,093 ms | **3 ms** | walk was 100% of the cost |
| V45-dockerfile-healthcheck | 22,900 ms | **12 ms** | walk was 99% of the cost |
| V58-reproducible-build-markers | 21,248 ms | **47 ms** | walk + workflow load |

#### New wall floor: V06 + V07

The post-Phase-65 wall is bounded by V06 (``go test -race``) and V07
(``tsc --noEmit`` + eslint) at ~5.5 s each. These are real subprocess
work, not walk overhead. Phase 63 can't cache them (V06 is in
``TIER_CACHE_INELIGIBLE``; V07 has findings so PASS-state cache
doesn't apply). This is the genuine compilation/test cost of the
project's source — further optimization would require subprocess-level
caching of `go test` / `tsc` output, which is out of scope.

#### Files

- **New**: ``lib/file_index.py`` (~280 LOC), ``tests/test_file_index.py`` (37 tests)
- **Modified**: ``lib/project_context.py`` (cached_property), ``lib/tier_cache.py`` (delegates), ``hooks/stop_validator.py`` (uses ``ctx.file_index.hash_for_patterns``), 7 validators (replace ``Path.glob`` with ``find_by_pattern``)
- **Test count**: 1,545 → **1,582** (+37)

### Changed (Phase64 — 5 follow-up optimizations: exclude-aware hash, ext bucketing, Tier 2 parallel, V14/V15 incremental, perf audit)

After Phase 63 closed the structural Tier 2 ↔ Tier 3 dedup gap, the
remaining bottlenecks were small-but-additive: stat-walk over vendored
trees, O(N=49) regex match per Edit, sequential router dispatch when
many validators match, and full-project re-analysis even when only one
file changed. Phase 64 lands all five fixes in one batch.

#### 64.1 — `compute_input_hash` respects `exclude.paths`

`lib/tier_cache.compute_input_hash` now accepts an ``exclude_paths``
parameter (gitignore-style globs) and skips files matching any of
them. Wired through `hooks/stop_validator.py` so
`ctx.config.exclude.paths` automatically feeds in.

- **ROI**: 50-200ms per Stop on monorepos with `vendor/`,
  `node_modules/`, `**/__generated__/**`. Plus a correctness fix:
  excluded files no longer invalidate validator caches when they
  change (a `git pull` on vendored deps used to trigger full re-runs
  for every Go validator).
- **Tests**: 4 new in `tests/test_tier_cache.py` — vendored-file
  skip, modify-while-excluded keeps hash stable, default
  `exclude_paths=()` preserves prior behavior, nested glob
  `**/__generated__/**` works.

#### 64.2 — Extension-bucketed validator dispatch

`hooks/validators/__init__.py` gains `_build_dispatch_index()` (cached
via `@functools.lru_cache(maxsize=1)`) and a `get_matching_validators(file_path, active)`
helper. The index pre-buckets the 49 validators by file extension so
a `.go` Edit only does regex match against ~12 candidates instead of
all 49. `hooks/router.py` swapped to the new helper.

- **ROI**: ~1-2 ms per Edit. Marginal individually, ~100-200ms / hour
  on heavy editing sessions.
- **Tests**: 14 in `tests/test_dispatch_index.py` —
  `_classify_pattern` semantics, bucket separation (ext / residual /
  wildcard), wildcard validators (V08, V12) match every file,
  filename-only patterns (`go.mod`) still resolve, dedup, equivalence
  with legacy `should_run` for representative file set.

#### 64.3 — Tier 2 router parallel dispatch (4+ validators)

`hooks/router.py` now uses `ThreadPoolExecutor(max_workers=4)` when
4+ validators match a file. Below threshold the sequential path runs
to avoid spin-up overhead. New `_run_one_validator` worker mirrors
the Tier 3 contract: per-validator failures are caught + logged via
`log_exception`, never propagating up to kill the batch.

- **ROI**: typical `.go` Edit hits ~11 validators (V06+V09+V14+V15+V25+V27+V34+V35+V36+V38+V39).
  Sequential ~200-500ms → parallel ~50-150ms (~3x).
- **Escape hatch**: `VERIFIERS_PARALLEL=0` opts out (same env var
  already used for Tier 3).
- **Tests**: 9 in `tests/test_router_parallel.py` —
  parallel-equivalent-to-sequential, crash-isolation, threshold
  constants, escape hatch recognition, 11-way concurrency smoke.

#### 64.4 — V14 / V15 incremental scan via `lib/per_file_cache.py`

New `lib/per_file_cache.py` (~270 LOC). Per-file findings cache keyed
by ``(validator_id, file_path, mtime_ns, config_fingerprint)`` so
unchanged files reuse prior findings while changed files get real
analysis. Two-step `load` + `save` lifecycle keeps the on-disk JSON
write atomic.

- **V14** (complexity-guard): wired in `_scan_dir` →
  `_analyze_file_cached`. New module-level `_complexity_fingerprint`
  hashes the 8 thresholds so a config change wipes the cache.
- **V15** (dependency-guard): wired in `_scan_lang_files`. New
  `_v15_fingerprint` includes the Go module name + custom layers
  yaml so a layers config edit invalidates without false-positive
  reruns. Skips the file `read_text()` on cache hit (extra I/O save).
- **Storage**: `<root>/.verifiers/state/per-file-cache/V##.json`,
  bounded at `MAX_ENTRIES=10,000` with FIFO eviction by
  `recorded_at`.
- **Escape hatch**: `VERIFIERS_NO_PER_FILE_CACHE=1`.
- **ROI**: 1000+ file project, single-file edit → V14/V15 first-miss
  cost drops from ~3-5s (full project AST) to ~0.5s (one file +
  lookup). Combines with Phase 63 PASS-state cache: Phase 63 skips
  V14/V15 entirely when nothing matched changed; Phase 64.4 covers
  the case where SOMETHING changed but most files are still
  unchanged.
- **Tests**: 21 in `tests/test_per_file_cache.py` — round-trip,
  config fingerprint invalidation, version mismatch, corrupt JSON,
  malformed finding entries, MAX_ENTRIES eviction, escape hatch,
  `clear_cache` (one validator + all), end-to-end V14 integration.

#### 64.5 — `scripts/perf_audit.py`

Read-only observability tool that turns
`<project>/.verifiers/state/metrics/V##.jsonl` history into actionable
config recommendations:

- **disable candidates** — slow + quiet (≥1s mean, 0 findings, ≥50
  invocations) get `validators.disabled` YAML stub.
- **cache TTL bumps** — very slow (≥5s mean) + cacheable get
  `tier_cache.max_age_seconds: 1800` recommendation.
- **timeout bumps** — mean ≥50% of default 30s timeout get
  `timeouts.per_validator[V##]: <3× mean>` recommendation.
- **review notes** — quiet + uncacheable (V09/V10/V11/V21/V37 test
  runners) flagged for awareness.

Two output modes: human table (default) + `--json` for pipelines.
Uses the existing `lib.metrics.aggregate_metrics` aggregation so it
shares the same window + log-dir conventions as `validator_metrics.py`.

- **Why**: closes the loop on Phase 61–63 caching work. Without
  perf_audit, knowing whether the cache landed correctly required
  eyeballing per-Stop wall-clock numbers. Now the user can see
  validator-by-validator effectiveness + cost trade-offs and tune
  config from data.
- **Tests**: 15 in `tests/test_perf_audit.py` — `_is_cacheable`
  parity with `TIER_CACHE_INELIGIBLE`, every recommendation
  category, threshold constants, disable-takes-priority over
  timeout (the `continue` guard), zero-use validators excluded from
  slowest list, slowest sorted desc.

#### Phase 64 totals

- **+1 module** (`lib/per_file_cache.py`)
- **+1 script** (`scripts/perf_audit.py`)
- **+5 test files** (`test_dispatch_index`, `test_router_parallel`,
  `test_per_file_cache`, `test_perf_audit`, plus 4 new tests in
  existing `test_tier_cache`)
- **+63 tests** (1,482 → 1,545 total passing)
- **0 ruff warnings**

Each sub-phase has its own escape hatch (env var or YAML flag) so
worst-case rollback is a single export.

### Changed (Phase63 — Tier 2 ↔ Tier 3 dedup via PASS-state cache)

After Phase 61 (native + subprocess caches inside individual validators)
the next biggest cost was structural: the Stop hook re-runs every
validator on every `stop` message even when nothing in their input
files changed since the last Stop. The dominant case in the workflow
is "edit one .ts file → stop → V06 go-quality re-runs the full Go
project for 30s because Tier 3 has no awareness of what changed since
last Stop". Phase 63 closes that loop.

#### 63.1 New: `lib/tier_cache.py` (~270 LOC)

PASS-state cache keyed by a stat-based hash of each validator's
file inputs:

- **Hash key**: `sha256(path : st_size : st_mtime_ns)` for every file
  matching `validator.file_patterns`. Stat-based — no content read —
  so 2,000+ files hash in ~10 ms.
- **Storage**: `<project>/.verifiers/state/tier-cache/<V##>.json`
  with `{"ts": <epoch>, "input_hash": "<sha256>"}`. Atomic write
  (`tmp → os.replace`). Corrupt JSON → wipe + treat as miss.
- **Lookup**: `lookup_recent_pass(project_root, validator_id,
  input_hash, max_age_seconds)` — TRUE iff entry exists, hashes
  match, AND entry is younger than the TTL.
- **Record**: `record_pass(project_root, validator_id, input_hash)`
  — only called for validators that produced ZERO findings on the
  current Stop (and aren't sentinels).
- **Hard exclusion list** (`TIER_CACHE_INELIGIBLE`): V06, V09, V10,
  V11, V12, V21, V37 — test runners + `git`-state-aware checks
  whose result is non-deterministic given file inputs alone. These
  are never cached.
- **TTL**: default 5 minutes. Caps stale-cache risk for
  non-determinism the hash doesn't catch (clock skew, NFS mtime
  weirdness, system package upgrades).
- **Escape hatch**: `VERIFIERS_NO_TIER_CACHE=1` env var disables
  the entire mechanism — debugging without editing config.

#### 63.2 Wiring into `hooks/stop_validator.py`

- Filter active validator list before `run_all`: for each cacheable
  validator, compute the input hash and consult
  `lookup_recent_pass`. Hits are skipped entirely; misses go into
  the parallel runner.
- After `run_all`: group findings by V## prefix; for each cacheable
  validator with no findings AND no sentinel, call `record_pass`.
  Validators with findings are intentionally NOT cached so the user
  keeps seeing the issue until fixed.

#### 63.3 Config schema: `tier_cache:` block in `.verifiers/config.yaml`

```yaml
tier_cache:
  enabled: true        # master switch (default true)
  max_age_seconds: 300 # PASS TTL (default 5 minutes)
```

Both keys optional — empty config keeps defaults.

#### 63.4 Tests: `tests/test_tier_cache.py` (32 tests)

- `compute_input_hash`: empty / no-match / determinism /
  add-file / remove-file / modify-file / pattern dedup / invalid
  pattern / directory skip.
- `CacheEntry.is_fresh`: within TTL, expired.
- `lookup_recent_pass`: miss when no file, hit after record, miss
  on hash mismatch, miss on TTL expiration, ineligible validator,
  corrupt JSON, missing schema keys.
- `record_pass`: creates dir, atomic write (no `.tmp` leftover),
  skips ineligible, overwrites prior entry.
- Escape hatch: disables lookup, disables record, off when env
  unset.
- `clear_cache`: removes all entries, no-op when dir missing.
- End-to-end: cache invalidates on file change, holds when
  unrelated file changes.

#### 63.5 Expected ROI

For a typical workflow where a single .ts file edit drives the next
Stop hook: V06 (Go) + the rest of the Go-side validators (V25, V27,
V34-V35, V38-V39, V47, V49, V50) all pre-cached → skip ~30-60s of
work. Cache invalidation is automatic per file pattern, so when
.go files DO change, V06 still runs.

V21 / V11 / V09 / V10 / V12 / V37 still run every Stop (excluded
by design); the Stop hook's "if it approved, the checks ran"
guarantee is preserved for system-state-dependent validators.

### Changed (Phase62 — 4 small wins: adaptive workers + per-validator timeouts + pre-compiled patterns + lazy validator import cache)

- **N1 (adaptive workers)**: `parallel_runner.run_all` already
  defaulted to `min(DEFAULT_MAX_WORKERS=8, len(validators))` — keeps
  thread count proportional to active validators rather than
  always spawning 8. Verified during Phase 62 audit; no code change
  needed beyond a comment refresh.
- **N2 (per-validator timeouts)**: New `TimeoutsConfig` dataclass
  (`default: int = 30`, `per_validator: dict[str, int]`). The
  parallel runner's `_resolve_timeout(validator_id, ctx, default)`
  consults `ctx.config.timeouts.per_validator[V##]` with a min-1s
  clamp. Lets users tune slow checks (e.g. `V21: 180` for pytest)
  or fail-fast on flaky ones (`V19: 5` for ruff) via
  `.verifiers/config.yaml`.
- **N3 (pre-compiled `file_patterns`)**: New module-level
  `_compile_patterns(patterns: tuple[str, ...])` decorated with
  `@functools.lru_cache(maxsize=128)` translates fnmatch globs to
  regex once per pattern set. `BaseValidator.should_run` now uses
  the pre-compiled list; eliminates ~50-100 ms of repeated
  `fnmatch.translate` work across 49 validators per edit.
- **N4 (lazy validator import cache)**: `get_all_validators()`
  decorated with `@functools.lru_cache(maxsize=1)` so the
  router → stop_validator chain reuses imported modules + validator
  instances rather than re-importing 49 modules on every
  invocation. Cuts ~200 ms off Tier-2/Tier-3 sequential calls and
  preserves per-validator state across calls
  (e.g. `ProtoConnectValidator.hash_cache`). Tests can call
  `get_all_validators.cache_clear()` for a fresh registry.

### Changed (Phase61 — Performance optimizations: V06 Option C, V07 native cache, V03 subprocess cache)

Three performance optimizations applied per the user's directive
("Go쪽은 Option C, 캐시 적용, 7일 넘어가는 건 지워주고"):

#### 61.1 V06 go-quality — Option C parallelization

- **Problem**: 5 sub-commands (`go vet` → `gofmt` → `go build` →
  `golangci-lint` → `go test`) ran sequentially. Worst case 370s.
- **Fix**: Detect `golangci-lint` at runtime via `shutil.which`. If
  present (the common case), `golangci-lint` covers `govet` + `gofmt`
  by default — call them separately becomes redundant + emits duplicate
  findings. Two-stage execution:
  - Stage 1 (sequential): `go build ./...` runs solo because it writes
    to `$GOCACHE`.
  - Stage 2 (parallel via `ThreadPoolExecutor(max_workers=2)`):
    `golangci-lint run` and `go test -race ./...` run concurrently.
- **Fallback**: If `golangci-lint` is absent, the validator falls back
  to the legacy sequential 3-cmd path (`go vet` + `go build` + `go test`).
- **ROI**: 35% latency reduction in the typical case (370s → ~240s
  worst case; ~10-30s in caches-warm case).

#### 61.2 V07 ts-quality — Native cache flags

- **eslint**: Added `--cache --cache-strategy content --cache-location
  <project>/.verifiers/cache/eslint/` to both `_check_eslint_single`
  (Tier 2) and `_check_eslint_full` (Tier 3). Lock-file gate
  invalidates the cache when `web/bun.lockb` (or `package-lock.json` /
  `yarn.lock`) hash changes — guards against "plugin upgraded but
  cache still says PASS".
- **tsc**: Added `--incremental --tsBuildInfoFile <project>/.verifiers/
  cache/tsc.tsbuildinfo` to `_check_tsc` (Tier 3 only). Gated on
  TypeScript ≥ 5.0 (TS 4.x had `noEmit + incremental` bugs).
- **Escape hatch**: `VERIFIERS_NO_CACHE=1` env var disables all cache
  flags for both tools.
- **ROI**: eslint 20-30s → 2-3s warm (10x). tsc 15s → 1-3s warm
  (5-15x).

#### 61.3 V03 buf lint — Subprocess result cache (`lib/subprocess_cache.py`)

- New `lib/subprocess_cache.py` (~250 LOC + 13 tests). For tools
  with NO native cache (`buf` doesn't have one), hash inputs
  (proto files + buf.yaml + tool version + cmd args) → cache stdout/
  stderr/returncode. Cache hit returns instantly without invoking
  the subprocess.
- **Cache storage**: `<project>/.verifiers/state/subprocess-cache/
  <label>.json`. One file per cache key label (e.g. `V03-buf-lint`).
- **7-day TTL FIFO cleanup**: cache files older than 7 days
  (mtime check) are auto-deleted on every `cached_run` call. Per-file
  entry cap at 32 with FIFO eviction.
- **Atomic write**: tmp → `os.replace` so partial writes can't poison
  the cache.
- **Corrupt-file recovery**: malformed JSON → wipe + treat as miss.
- **Escape hatch**: `VERIFIERS_NO_CACHE=1` bypasses cache entirely.
- **V03 wired**: `_check_buf_lint` now calls `cached_run` with
  proto files + `buf.yaml` + `buf.gen.yaml` + `buf --version` as the
  hash inputs.
- **NOT applied to**: `pytest`, `go test`, `ruff`, `eslint`, `tsc`,
  `golangci-lint` — either output depends on system state (tests) or
  the tool has its own cache.

### Verification

- 1431 → 1450 tests (+19 new across V06 split test, V07 cache tests,
  subprocess_cache tests). ruff clean. format clean.

### Changed (Phase60 — Library extraction: workflow_loader)

Phase 51 pattern (codegen_staleness) applied to the second-largest
duplication site: `.github/workflows/*.yml` parsing. 6 validators
independently reimplemented the same directory walker; Phase 60
extracts it.

- **`lib/workflow_loader.py` extracted from V37, V40, V41, V43, V57, V58.**
  Two helpers:
  - `walk_workflow_paths(project_root)` — generator yielding
    workflow `Path` objects (no parse). Used by text-scan validators
    (V40 line-by-line `uses:` matcher, V57/V58 helpers that read
    raw text).
  - `walk_workflows(project_root)` — generator yielding `(Path, dict)`
    pairs with safe YAML parse. Skips unreadable files / malformed
    YAML / non-dict roots silently. Used by V41 (and any future
    consumer that wants parsed data).
  - `parse_workflow(path)` — single-file safe-parse, returns
    `dict | None`. Used by V42 (single fixed path) and as the
    underlying parser for `walk_workflows`.

- **6 validators migrated:**
  - V37 go-test-race-coverage: `_check_workflows` → `walk_workflow_paths`
  - V40 actions-sha-pin: `validate_project` → `walk_workflow_paths`
  - V41 actions-permissions-block: `validate_project` → `walk_workflows`
    (uses both the path AND the parsed dict, so this is the only
    consumer of the higher-level helper)
  - V43 ci-image-scanning: `validate_project` → `walk_workflow_paths`
  - V57 sbom-ci-step: `_check` → `walk_workflow_paths` (early-bail
    iteration as soon as any workflow satisfies SBOM)
  - V58 reproducible-build-markers: `_workflow_satisfies_sde` →
    `walk_workflow_paths`

  ~80 lines of duplicated `workflows_dir` walker code removed
  (12-15 lines × 6 consumers).

- **13 new unit tests** in `tests/test_workflow_loader.py` pinning:
  - `walk_workflow_paths`: dir-absent handling, .yml + .yaml both
    enumerated, sorted order, dedup by resolved path, lazy iteration
    (early break works).
  - `walk_workflows`: yields (Path, dict) pairs, malformed YAML
    skipped silently, empty file skipped, list-root skipped.
  - `parse_workflow`: valid → dict, missing → None, malformed →
    None, list-root → None.

- **Verification**: 1418 → 1431 tests (+13). All 6 migrated
  validator suites unchanged behavior — pre-existing tests still
  pass without modification.

- **Cumulative library extraction arc:**
  ```
  Phase 51  lib/codegen_staleness.py  (V02 + V03 share)
  Phase 60  lib/workflow_loader.py    (V37 V40 V41 V43 V57 V58 share)
  ```

### Changed (Phase59 — V05 / V44 dup cleanup)

After Phase 50 cleaned the original V03 / V05 / V27 rule duplicates,
Phase 58 added V44 (`dockerfile-base-digest-pin`) which turned out to
be a strict superset of V05's existing `BASE-IMAGE-LATEST` /
`DOCKERFILE-LATEST-TAG` rule — both now fire on `FROM image:latest`.
Phase 59 removes the V05 rule, V44 is canonical.

- **V05-BASE-IMAGE-LATEST removed.** V44-FROM-NO-DIGEST catches the
  same case (any `FROM image:tag` without `@sha256:` digest, including
  `:latest` and the no-tag implicit-latest case).
  - Deleted method `_check_base_image_latest` from
    `hooks/validators/docker_compose.py` (~40 LOC).
  - Removed call from `validate_project`.
  - Deleted `TestV05BaseImageLatest` class (3 tests) from
    `tests/test_docker_compose.py`. V44 coverage in
    `tests/test_dockerfile_base_digest.py::TestFromTagOnly` covers the
    same scenarios with stricter assertions.
  - Updated `skills/V05-docker/SKILL.md` Rules table (removed the
    `V05-DOCKERFILE-LATEST-TAG` row) + example block.

- **Other potential overlaps audited and kept (intentional layering):**
  - V05-MISSING-HEALTHCHECK ↔ V26-PROD-NO-HEALTHCHECK ↔ V45-DOCKERFILE-NO-HEALTHCHECK
    — three different surfaces (compose `depends_on`, prod compose,
    Dockerfile final stage). Phase 50 documented; still correct.
  - V11 (per-edit pytest) ↔ V21 (Stop pytest) — different tiers; V11
    docstring already documents the layering.
  - V01 secret detection ↔ V08 secret detection — different file
    scopes (YAML vs all sources); share `lib/secret_regexes.py` (Phase 38).
  - V12 (commit-discipline) ↔ V54 (commitlint-gate) — different
    enforcement points (post-commit detection vs pre-commit gate).

- **Algorithm-level dup deferred (lib extraction candidate for Phase 60):**
  - 7 validators now parse `.github/workflows/*.yml` independently
    (V37, V40, V41, V42, V43, V57, V58). Same-shape `yaml.safe_load`
    + per-step iteration. Strong candidate for `lib/workflow_loader.py`
    (Phase 51 pattern). Deferred since it's refactor, not dup-removal.

- **Net active validators**: 49 → 49 (same count; one rule removed
  from a validator that has 13 other rules).
- **Tests**: 1421 → 1418 (-3 V05 LATEST tests; equivalent coverage
  in V44 test suite).

### Removed (Phase58 wrap — V55 cut)

- **V55 — error-tracking-sdk** (Sentry/GlitchTip SDK presence check)
  was implemented in Phase 58 Sprint A but removed by user decision
  shortly after v0.7.0 tagged. Rationale: too opinionated for a
  template — teams pick their own tracking stack (Sentry / GlitchTip /
  Datadog / Honeybadger / none). The README incident that motivated
  V55 (Apr 2026 `/manual-invoice/drafts` HTTP-500 visibility gap) is
  better addressed by V49 (OTel) + V56 (Prometheus) + V50 (/livez
  vs /readyz) which together cover the observability surface without
  prescribing a vendor.

  V55 namespace stays reserved (no V-ID reuse) so older commits +
  audit history references remain stable. Same pattern as V24
  (Hasura permission audit) cut in Phase 46.

  Removed:
    - `hooks/validators/error_tracking_sdk.py`
    - `tests/test_error_tracking_sdk.py` (12 tests)
    - `skills/V55-error-tracking-sdk/` directory
  BUILTIN_GROUPS security: V55 dropped → 8 → 7 members.
  test_security_group_membership invariant updated.
  run_single.py NAME_MAP: 3 V55 aliases removed.

  Net active validators: 50 → 49.
  Test count: 1433 → 1421 (-12 V55 tests).

## [0.7.0] - 2026-04-30

Sixth tagged release. Closes the Phase 58 audit completely —
**all 8 proposed verifiers (V51-V58) are now implemented**, on top
of the v0.6.0 baseline (V01-V50).

**Headline:**
- 42 → **50 active validators** (+8 — every Phase 58 audit gap closed)
- 1341 → **1433 tests passing** (+92 in v0.7.0 release window)
- Phase 58 audit backlog: 0/8 → **8/8 complete**

### Added (Phase58 Sprint B — V51 / V52 / V57 / V58 implementation)

Final batch from Phase 58 audit. Backlog: 4/8 → 8/8.

- **V51 — adr-template-compliance** (`hooks/validators/adr_template_compliance.py`,
  ~140 LOC + 12 tests). Walks ADR directories (`docs/ADR/`,
  `docs/adr/`, `docs/architecture/decisions/`, `docs/decisions/`)
  and asserts each `*.md` follows Michael Nygard's canonical format
  (Context / Decision / Consequences sections + Status). Lenient
  Status detection (frontmatter / `## Status` / `**Status**:` bold).
  Skips template / README / index / `0000-*.md`. V51-ADR-MISSING-SECTION
  (info, per missing section).

- **V52 — readme-badges** (`hooks/validators/readme_badges.py`,
  ~120 LOC + 10 tests). Locates root `README.md` (case-insensitive)
  and checks for two badge categories: V52-NO-CI-BADGE (info) when
  no CI status badge present (GitHub Actions URL or shields.io
  workflow/status), V52-NO-LICENSE-BADGE (info) when no license
  badge. Codecov badges don't satisfy CI (different artifact).

- **V57 — sbom-ci-step** (`hooks/validators/sbom_ci_step.py`,
  ~140 LOC + 11 tests). Project-level scan of `.github/workflows/*.yml`
  for SBOM generators: `anchore/sbom-action`, `cyclonedx/gh-gomod-
  generate-sbom`, `cyclonedx-gomod`/`syft` run commands,
  `aquasecurity/trivy-action` with `format: cyclonedx|spdx-json`,
  `microsoft/sbom-action`. V57-NO-SBOM-CI (warning) when none found.
  Layered with V43: V43=CVE scanning, V57=SBOM artifact generation.

- **V58 — reproducible-build-markers** (`hooks/validators/reproducible_build_markers.py`,
  ~155 LOC + 13 tests). Production Dockerfile final stage must
  declare `ARG SOURCE_DATE_EPOCH` or `ENV SOURCE_DATE_EPOCH=` —
  OR the workflow that builds it must pass `SOURCE_DATE_EPOCH` via
  `build-args:`. Dev Dockerfiles (`*dev*` filename or `AS dev` final
  stage) exempt. V58-NO-SOURCE-DATE-EPOCH (warning).

- **Registry wiring**: 4 new imports + 4 instances under Phase58
  Sprint B marker.

- **`run_single.py` NAME_MAP**: 12 new aliases (3 per validator).

- **`BUILTIN_GROUPS` updated**:
  - process: + V51, V52
  - security: + V57
  - docker: + V58

- **Tests**: 1387 → 1433 passing (+46). Phase 52 invariants tests
  updated for new memberships (security: +V57, process: +V51, V52).

### v0.7.0 release content (since v0.6.0)

```
phase57    V46 V48 + v0.6.0 release tag (Phase 53: 17/17)
phase58-sprintA   V53 V54 V55 V56 (Phase 58 audit batch 1)
phase58-sprintB + v0.7.0   V51 V52 V57 V58 (Phase 58 complete: 8/8)
```

Total since v0.6.0: 8 new validators, ~1100 new LOC implementations,
~900 new LOC tests, +92 tests (1341 → 1433).

### Added (Phase58 Sprint A — Phase 58 audit, 4 new validators)

After v0.6.0 closed the Phase 53 audit (V01-V50), Phase 58 audit
identified 8 more best-practice gaps in ai-project-template across
docs / production observability / supply-chain. Sprint A ships the
top 4 (HIGH/MEDIUM impact); Sprint B (V51, V52, V57, V58 — LOW/
MEDIUM tail) queues for next phase.

- **V55 — error-tracking-sdk** (`hooks/validators/error_tracking_sdk.py`,
  ~165 LOC + 12 tests). Dual-path Sentry SDK presence check:
  V55-NO-GO-ERROR-TRACKING (error) when `server/go.mod` lacks
  `getsentry/sentry-go` AND `server/internal/*.go` exists; V55-NO-WEB-
  ERROR-TRACKING (error) when `web/package.json` lacks any of
  `@sentry/react`/`@sentry/browser`/`@sentry/nextjs`/`@sentry/vue`.
  Empty starter (no internal code yet) skipped. Triggered by README's
  documented Apr 2026 `/manual-invoice/drafts` HTTP-500 incident
  visibility gap.

- **V53 — github-community-files** (`hooks/validators/github_community_files.py`,
  ~150 LOC + 12 tests). Three independent presence checks:
  V53-NO-PR-TEMPLATE, V53-NO-ISSUE-TEMPLATE, V53-NO-CODEOWNERS — all
  warning severity. Accepts standard + lowercase + legacy filename
  variants and CODEOWNERS at root/docs/.github. Closes the
  bypassed-review surface on high-blast-radius paths
  (`server/internal/auth/`, `hasura/metadata/`).

- **V54 — commitlint-gate** (`hooks/validators/commitlint_gate.py`,
  ~180 LOC + 11 tests). Conditional: only fires when project
  *consumes* conventional commits (changelog generator in package.json
  scripts/deps OR Keep-a-Changelog formatted CHANGELOG.md) but
  *doesn't enforce* them. Recognizes 7 enforcement signals (commitlint
  configs, .commitlintrc.*, .husky/commit-msg, lefthook.yml
  `commit-msg:`, pre-commit `conventional-pre-commit`, commitlint in
  any package.json). V54-COMMITLINT-NOT-ENFORCED (warning).

- **V56 — prometheus-metrics-endpoint** (`hooks/validators/prometheus_metrics_endpoint.py`,
  ~190 LOC + 11 tests). Two-step layered with V49 (V49=traces,
  V56=metrics — different concerns):
  V56-NO-PROMETHEUS-SDK (warning) when `server/go.mod` lacks
  `prometheus/client_golang`; V56-PROMETHEUS-NOT-WIRED (warning) when
  SDK present but no `cmd/**/*.go` registers `/metrics` route. Router-
  agnostic detection (stdlib mux, chi, gorilla). Workers without
  HTTP service exempt.

- **Registry wiring**: 4 new imports + 4 instances in
  `hooks/validators/__init__.py` under Phase58 Sprint A marker.

- **`run_single.py` NAME_MAP**: 12 new aliases (3 per validator —
  e.g. `error-tracking-sdk` / `error-tracking` / `sentry`).

- **`BUILTIN_GROUPS` updated**:
  - security: + V55
  - process: + V53, V54
  - api-rpc-data: + V56

- **Tests**: 1341 → 1387 passing (+46 across 4 new test files).
  3 Phase 52 invariant tests updated to expect new memberships:
  `test_security_group_membership`, `test_process_group_membership`,
  and the two `expand_disabled_groups` process-expansion tests.

### Sprint B queue (next phase)

```
V51 adr-template-compliance        LOW
V52 readme-badges                  LOW
V57 sbom-ci-step                   MEDIUM
V58 reproducible-build-markers     LOW
```

After Sprint B completes → v0.7.0 release tag bundling all of
Phase 58.

## [0.6.0] - 2026-04-30

Fifth tagged release. Closes the Phase 53 audit completely —
**all 17 proposed verifiers (V34-V50) are now implemented**, on top of
the v0.5.0 baseline (V01-V27 minus V17/V24).

**Headline:**
- 25 → **42 active validators** (+17 new — every Phase 53 audit gap closed)
- 1230 → **1341 tests passing** (+111 in v0.6.0 release window)
- BUILTIN_GROUPS now covers all 7 categories with cross-domain
  membership (no orphan validators)
- Phase 53 audit backlog: 7/17 → **17/17 complete**

### Added (Phase57 — Final batch: V46 + V48)

Last 2 verifiers from Phase 53 audit shipped. Backlog: 15/17 → 17/17.

- **V46 — migration-enum-rollback** (`hooks/validators/migration_enum_rollback.py`,
  ~120 LOC + 10 tests). Walks `migrations/**/up.sql` files. For each
  containing `ALTER TYPE … ADD VALUE`, locates paired `down.sql` and
  asserts either an `ALTER TABLE` rename-swap or
  `-- MANUAL ROLLBACK REQUIRED` marker. PostgreSQL has no native
  `ALTER TYPE … DROP VALUE`, so silently irreversible enum migrations
  are a real schema drift hazard. V46-ENUM-IRREVERSIBLE (warning).
  Missing `down.sql` also flagged.

- **V48 — hasura-permission-rationale** (`hooks/validators/hasura_permission_rationale.py`,
  ~110 LOC + 10 tests). Walks Hasura table YAML metadata. For each
  table with `select_permissions` only (no insert/update/delete),
  asserts intent is documented either at repo level (`AGENTS.md` /
  `CLAUDE.md` / `docs/*.md` containing token `hasura-read-only` or
  `mutations-via-grpc`) OR per-table (YAML comment
  `# mutations: intentionally absent`). V48-HASURA-SELECT-ONLY-UNDOCUMENTED
  (info). Caches repo-level lookup per `validate_project` invocation
  for performance.

- **Registry wiring**: 2 new imports + 2 instances under Phase57 marker.
- **`run_single.py` NAME_MAP**: 4 new aliases.
- **`BUILTIN_GROUPS` updated**: V46, V48 → api-rpc-data.
- **Tests**: 1321 → 1341 passing (+20). Phase 52 invariants still pass —
  every active V## belongs to exactly one BUILTIN_GROUPS bucket.

### v0.6.0 release content (since v0.5.0)

```
phase54-sprint1   V36 V40 V47 V50  ★ medical/finance ship-blockers
phase54-sprint2   V37 V41 V43      ★ CI hardening + v0.5.0 tag
phase55           V34 V35 V42 V49  ★ Sprint 3 + Long tail batch 1
phase56           V38 V39 V44 V45  ★ Long tail batch 2
phase57 + v0.6.0  V46 V48          ★ Final batch + tag
```

Total: 17 new validators, ~3700 new LOC implementations, ~3000 new
LOC tests, +217 tests (1124 → 1341).

### Added (Phase55 — Sprint 3 + Long tail batch 1)

Four more validators from the Phase 53 audit shipped as full
implementations on top of v0.5.0. Brings the Phase 53 backlog
from 7/17 to 11/17 implemented.

- **V42 — dependabot-config** (`hooks/validators/dependabot_config.py`,
  ~190 LOC + 10 tests). Project-level config-presence check.
  Accepts `.github/dependabot.{yml,yaml}` or any Renovate config
  form (`.github/renovate.{json,json5}`, root `renovate.json`).
  V42-NO-DEPENDABOT (warning) when neither exists. When dependabot
  config is present, parses the `updates:` array and emits
  V42-DEPENDABOT-MISSING-ECOSYSTEM (warning) for each required
  ecosystem missing — `gomod` (if `server/go.mod` exists), `npm`
  (if `web/package.json` exists), and always `github-actions`.

- **V49 — otel-instrumentation** (`hooks/validators/otel_instrumentation.py`,
  ~150 LOC + 10 tests). Two-step Go observability check:
  V49-NO-OTEL-SDK (warning) when `go.mod` lacks
  `go.opentelemetry.io/otel` direct dep; V49-OTEL-NOT-WIRED
  (warning) when SDK is present but no `cmd/**/*.go` file
  imports `otelhttp` (mux not traced). Test files and `internal/`
  imports don't satisfy the wiring requirement.

- **V34 — go-error-wrapping** (`hooks/validators/go_error_wrapping.py`,
  ~165 LOC + 11 tests). Heuristic regex scanner over `cmd/` and
  `internal/` Go files. Flags bare `return err` / `return foo, err`
  whose preceding line isn't a wrapping call (`fmt.Errorf("…%w…")`,
  `errors.New(…)`, `connect.NewError(…)`). Skips `_test.go`,
  `gen/` directory, `*.generated.go`, and files marked
  `// Code generated`. Severity warning (heuristic, false-positive-
  prone — user can `//nolint:V34` known good cases).

- **V35 — go-context-propagation** (`hooks/validators/go_context_propagation.py`,
  ~125 LOC + 10 tests). Scans `internal/**/*.go` (non-test) for
  `context.Background()` / `context.TODO()` calls. Emits
  V35-MID-FLOW-BACKGROUND-CTX (error) per occurrence. Two
  exemptions: (a) file containing `signal.NotifyContext(`
  (likely a long-lived background daemon root), (b) package-scope
  `var bgCtx = context.Background()` declarations. `cmd/` files
  are out-of-scope (program root is the right place for Background).

- **Registry wiring**: 4 new imports + 4 instances added to
  `hooks/validators/__init__.py:get_all_validators()` under a
  Phase55 marker.

- **`run_single.py` NAME_MAP**: 11 new short aliases
  (`dependabot-config`, `dependabot`, `renovate`,
  `otel-instrumentation`, `otel`, `go-error-wrapping`,
  `error-wrapping`, `wrapcheck`, `go-context-propagation`,
  `context-propagation`).

- **`BUILTIN_GROUPS` updated**: V34, V35 → code-quality;
  V42 → security; V49 → api-rpc-data. Phase 52 invariants pass.

- **Tests**: 1230 → 1271 passing (+41 across 4 new test files).
  `test_security_group_membership` updated to expect new V42.

### Sprint 3 + long tail status

```
Phase 55 (this):  V34 V35 V42 V49                 ✅ implemented
Long tail (next): V38 V39 V44 V45 V46 V48          ⏸ specs locked, queued
```

## [0.5.0] - 2026-04-30

Fourth tagged release. Bundles Phases 49a / 50 / 51 / 52 / 53 / 54
Sprint 1+2 — the categorization → consolidation → audit-driven
expansion arc.

**Headline:**
- 25 → 32 active validators (V01-V27 + V36, V37, V40, V41, V43, V47, V50).
- 17 design specs locked (Phase 53) for V34-V50 — 7 already implemented,
  10 queued for Sprint 3 + long tail.
- New `docs/AUDITS.md` — single-source-of-truth audit history.
- New `docs/VERIFIERS-CATEGORIES.md` — 7-category map.
- New `lib/codegen_staleness.py` — shared by V02 + V03.
- New `BUILTIN_GROUPS` config + `disabled_groups:` UX.
- 1124 → 1230 tests passing (+106).

### Added (Phase54 Sprint 2 — V37 / V41 / V43 implementation)

CI hardening tier from the Phase 53 audit. Three more validators
shipped as full implementations on top of Sprint 1 (V36, V40, V47,
V50). All three target GitHub Actions workflow YAML.

- **V37 — go-test-race-coverage** (`hooks/validators/go_test_race_coverage.py`,
  ~150 LOC + 14 tests). Scans `.github/workflows/*.yml`, `Makefile`,
  `**/justfile` for `go test` invocations. Two rules:
  - V37-CI-NO-RACE (error) — `go test` without `-race` flag. The
    target project has a known concurrent invoice-number test gated
    behind `INVOICE_RACE_TEST=1` env, meaning the data race is
    documented but never CI-checked.
  - V37-CI-NO-COVERAGE-GATE (warning, workflow-only) — `go test`
    with no `-coverprofile` flag and no `actions/upload-artifact` /
    `codecov-action` step in the same job.

- **V41 — actions-permissions-block** (`hooks/validators/actions_permissions_block.py`,
  ~100 LOC + 11 tests). YAML-parses each workflow. Passes if either
  (a) top-level `permissions:` key exists (including `{}` deny-all),
  OR (b) every job declares its own `permissions:`. Otherwise emits
  V41-NO-PERMISSIONS-BLOCK (warning) — `GITHUB_TOKEN` blast radius
  undefined per least-privilege. Workflows with no jobs (composite/
  reusable) are exempt.

- **V43 — ci-image-scanning** (`hooks/validators/ci_image_scanning.py`,
  ~150 LOC + 11 tests). Identifies build jobs (steps containing
  `docker build` in `run:` or `docker/build-push-action` in `uses:`).
  For each, checks the same job OR any `needs:`-dependent downstream
  job for a recognized scanner: `aquasecurity/trivy-action`,
  `anchore/scan-action`, `snyk/actions/docker`, `docker/scout-action`,
  `grype` / `trivy` in `run:`. Missing scanner → V43-NO-IMAGE-SCAN
  (error). Production CVE flow gated.

- **Registry wiring**: 3 new validators added to
  `hooks/validators/__init__.py:get_all_validators()` with Phase54
  Sprint 2 marker.

- **`run_single.py` NAME_MAP**: 6 new aliases (`go-test-race`,
  `race-coverage`, `actions-permissions`, `permissions-block`,
  `ci-image-scan`, `image-scan`).

- **`BUILTIN_GROUPS` updated**: V37 → test-execution; V41 + V43 →
  security. Phase 52 invariants still pass.

- **Tests**: 1194 → 1230 passing (+36 across 3 test files).
  `test_security_group_membership` updated to expect new members
  V40, V41, V43.

### Added (Phase54 Sprint 1 — V36 / V40 / V47 / V50 implementation)

Top 4 medical/finance ship-blocker verifiers from the Phase 53 audit
shipped as full implementations (Python + tests + registry wiring).
The remaining 13 verifiers (V34, V35, V37, V38, V39, V41, V42, V43,
V44, V45, V46, V48, V49) keep their SKILL.md design specs from
Phase 53 and queue for subsequent Sprint phases.

- **V36 — go-http-server-hardening** (`hooks/validators/go_http_hardening.py`,
  162 lines + 11 tests). Detects `http.Server{...}` struct literals
  in `cmd/*/main.go` lacking `ReadHeaderTimeout` + `WriteTimeout` fields
  (slowloris vulnerability) and missing `signal.NotifyContext` /
  `srv.Shutdown` graceful shutdown wiring. Severity error / warning
  respectively.

- **V40 — actions-sha-pin** (`hooks/validators/actions_sha_pin.py`,
  ~140 lines + 13 tests). Line-by-line scan of `.github/workflows/*.yml`
  for `uses:` entries; flags any action ref not pinned to a 40-char SHA.
  Third-party actions (e.g. `oven-sh/setup-bun@v1`) → severity error
  (supply-chain risk). First-party `actions/*` → severity warning.
  Local actions, `docker://` refs, and comment lines exempt.

- **V47 — fk-index-discipline** (`hooks/validators/fk_index_discipline.py`,
  ~190 lines + 9 tests). Cross-file SQL parser walking
  `**/migrations/**/up.sql` chronologically. Captures FK declarations
  (inline `REFERENCES` and `ALTER TABLE … ADD CONSTRAINT FOREIGN KEY`
  forms) and matches against `CREATE INDEX` statements + composite
  primary key leftmost columns across all migrations. Emits
  V47-FK-NO-INDEX (error) per uncovered FK column.

- **V50 — health-endpoint-split** (`hooks/validators/health_endpoint_split.py`,
  ~150 lines + 9 tests). Walks `cmd/**/*.go` for HTTP route
  registrations. Aggregates routes across files; if any HTTP server
  exists but `/livez` and/or `/readyz` is missing → V50-HEALTH-NOT-SPLIT
  (error). Additionally, if `/readyz` is registered but the file lacks
  a `pgx.Ping` / `Ping(` call → V50-READYZ-NO-DB-PING (warning).
  Workers with no HTTP routes are exempt.

- **Registry wiring**: 4 new validators added to
  `hooks/validators/__init__.py:get_all_validators()`. Registry
  invariants (V-ID prefix uniqueness etc.) preserved.

- **`run_single.py` NAME_MAP**: 8 new short aliases for the 4 verifiers
  (`go-http-hardening`, `http-hardening`, `actions-sha-pin`, `sha-pin`,
  `fk-index`, `fk-index-discipline`, `health-endpoint`, `health-split`).

- **`BUILTIN_GROUPS` updated**: V36 → code-quality; V40 → security;
  V47 + V50 → api-rpc-data. The Phase52 invariants
  (`test_every_active_validator_belongs_to_a_group` and
  `test_no_group_member_is_dead`) now confirm 4 new V## are
  registered AND grouped.

- **Tests**: 1152 → 1194 passing (+42 new across 4 test files).
  V40 fix: `test_security_group_membership` updated to expect the new
  `["V08", "V18", "V40"]` membership.

### Sprint priority queue

Remaining 13 verifiers from Phase 53 audit, ranked by priority:

```
Sprint 2 (CI hardening):           V37, V41, V43
Sprint 3 (governance + obsv):       V42, V49
Long tail:                          V34, V35, V38, V39, V44, V45, V46, V48
```

### Added (Phase53 — 17 verifier design specs + audit history doc)

- **17 SKILL.md design specs** for V34-V50 covering best-practice gaps
  found in `ai-project-template`. These are **design phase only** —
  Python implementation deferred to subsequent phases (Sprint 1: V36,
  V40, V47, V50; Sprint 2: V37, V41, V43; etc.). Each SKILL.md follows
  the V22/V27 template structure: Rules / Why / Design / How-it-checks /
  Could-be-better / References / Examples.

  **Go runtime discipline (V34-V39):**
  - V34 go-error-wrapping (bare `return err` without `%w`)
  - V35 go-context-propagation (mid-flow `context.Background()`)
  - V36 go-http-server-hardening ★ (no ReadTimeout/WriteTimeout, no graceful shutdown)
  - V37 go-test-race-coverage (CI lacks `-race`)
  - V38 golangci-strictness (no `wrapcheck`, weak `nolintlint`)
  - V39 go-context-scoped-logger (global zerolog instead of `zerolog.Ctx(ctx)`)

  **CI/CD + container security (V40-V45):**
  - V40 actions-sha-pin ★ (8 third-party actions on floating tags)
  - V41 actions-permissions-block (no `permissions:` block in workflows)
  - V42 dependabot-config (no automated dependency PRs)
  - V43 ci-image-scanning ★ (no Trivy / Grype / Snyk in CI)
  - V44 dockerfile-base-digest-pin (FROM lines without `@sha256:`)
  - V45 dockerfile-healthcheck (no HEALTHCHECK in HTTP-service Dockerfiles)

  **DB / Hasura / observability (V46-V50):**
  - V46 migration-enum-rollback (`ALTER TYPE ADD VALUE` without rollback)
  - V47 fk-index-discipline ★ (5 FK columns missing indexes — production death-trap)
  - V48 hasura-permission-rationale (select-only YAML without intent doc)
  - V49 otel-instrumentation (zero OpenTelemetry SDK presence)
  - V50 health-endpoint-split ★ (single `/health` instead of `/livez` + `/readyz`)

  ★ = medical/finance ship-blocker tier (Sprint 1 priority)

- **`docs/AUDITS.md`** — single-source-of-truth for the audit history.
  Documents what "audit" means in this project, lists Phase 27 / 50 /
  51 / 52 / 53 audits with their evaluation criteria + findings +
  outcomes, explains the parallel-research-agent pattern, and proposes
  triggers for future audits. Lets new contributors answer "why was
  this verifier added / why was that rule deleted" without git blame
  archaeology.

### Added (Phase52 — Group-based validator disable)

- **`validators.disabled_groups`** config field. The 7 categories from
  `docs/VERIFIERS-CATEGORIES.md` (`code-quality`, `test-execution`,
  `env-config`, `docker`, `api-rpc-data`, `security`, `process`) are now
  operational disable scopes:

  ```yaml
  validators:
    disabled_groups: ["process"]   # disables V12, V13, V15, V16
  ```

- **`groups:` top-level config field** for user-defined groups:

  ```yaml
  groups:
    my-strict: [V08, V18, V14]
  validators:
    disabled_groups: ["my-strict"]
  ```

  User-defined groups override built-ins on key collision (lets a
  project re-scope a category name to its own taxonomy).

- **`lib/config_loader.BUILTIN_GROUPS`** constant mirroring the
  categorization document, plus `expand_disabled_groups(cfg)` helper
  that resolves group names to V-ID prefixes. Group expansion runs
  before the existing per-V-ID disable filter; the two lists union.

- **`resolve_active_validators` (in `lib/validator_registry.py`)**
  now appends expanded groups to the per-V-ID disable list before
  filtering. Backward-compatible — empty/missing `disabled_groups`
  preserves pre-Phase52 behavior exactly.

- **17 new tests in `tests/test_disabled_groups.py`** including:
  - BUILTIN_GROUPS contract pinning (7 categories, no V-ID in two
    groups, exact membership for `process`/`security`).
  - `expand_disabled_groups` skip cases, override semantics, two-
    group union, unknown-name silent drop.
  - Round-trip via `load_config` for both `disabled_groups` and
    user-defined `groups:`.
  - End-to-end via `resolve_active_validators` confirming the
    matching validators are actually dropped from the active set.
  - **Coverage invariants:** every active V## must belong to exactly
    one BUILTIN_GROUPS bucket (catches future drift between code and
    `docs/VERIFIERS-CATEGORIES.md`); inverse check that no group
    member references a non-existent V-ID (V17 deferred and V24
    removed-in-Phase46 are explicit allowed gaps).

- **`docs/VERIFIERS-CATEGORIES.md` updated** — the "Not implemented
  yet" caveat replaced with a fully documented Phase52 implementation
  section showing built-in group names, custom group definition,
  override-on-collision semantics, and the union semantics with the
  existing `disabled` list.

### Changed (Phase51 — Library extraction: codegen staleness)

- **`lib/codegen_staleness.py` extracted from V02 + V03.** The two-step
  hash-then-mtime staleness algorithm was independently reimplemented
  in `graphql_gen.py:_check_stale_generated` (V02) and
  `proto_connect.py:_check_stale_generated` (V03) — identical logic,
  identical edge cases, ~28 lines of duplication. Phase51 lifts the
  algorithm into a single `is_codegen_stale(cache, category, project,
  input_files, generated_files) -> bool` function with extensive
  module-level docstring documenting the two-step rationale (why hash
  alone false-positives on `git checkout` mtime resets; why mtime alone
  false-positives after cache wipes).

- **V02 and V03 migrated** to the shared lib. Each validator keeps
  ownership of (a) input/generated file globbing, and (b) Finding emit
  with rule-specific message + fix string. Net delta: V02 -10 lines,
  V03 -15 lines, lib +135 lines (shared docstring + tests).

- **8 new unit tests** in `tests/test_codegen_staleness.py` pin the
  contract: 4 skip-cases (empty inputs, no existing inputs, no
  generated, no existing generated), 1 hash-gate (unchanged hash short-
  circuits regardless of mtime), 1 mtime-gate (cache wipe doesn't
  false-positive when generated is newer), 2 both-gates-trip cases
  including independent category cache keys.

- **Other extraction candidates audited and deferred.** `lib/compose_loader.py`
  (V05 ↔ V26 — V26 already has a working local `_walk_compose`,
  promote when 3rd consumer arrives), `lib/proto_walker.py` (V23 ↔ V27
  — only 5-line idiom, not worth the abstraction), env-file parsing
  (V01 ↔ V22 — V22's local `_parse_env_keys` is fine), Go pattern
  matching (V25/V27/V08 — domain-specific patterns, no shared shape).
  Documented in the audit; revisit if V28+ adds a third consumer.

### Changed (Phase49a — V22 drift asymmetric)

- **V22-ROOT-SERVER-DRIFT direction collapsed to root→server only.**
  Previously bidirectional, but server is the canonical source of truth
  for the `APP_*` namespace — server-only `APP_*` vars are normal, not
  drift. After the user surfaced every `APP_DATABASE_*` / `APP_JWT_*` /
  `APP_HASURA_*` / `APP_OAUTH_*` etc. as warnings on `ai-project-template`,
  the bidirectional check was net noise. The kept direction (root has
  `APP_*` not in server) catches the genuine structural mistake of root
  unilaterally introducing an `APP_*` key. Commit `c280c98`.

### Changed (Phase50 — Verifier organization pass)

- **Categorization document.** New `docs/VERIFIERS-CATEGORIES.md`
  partitions the 25 active verifiers into 7 categories (code-quality /
  test-execution / env-config / docker / api-rpc-data / security /
  process) with explicit ownership boundary diagrams for the three
  cross-cutting domains that previously had overlap (V05↔V26 docker,
  V03↔V23↔V27 proto/RPC, V01↔V22 env).

- **V05-PROD-NO-RESOURCE-LIMITS removed.** Duplicate of
  `V26-PROD-NO-RESOURCE-LIMITS` with stale `info` severity. V26 owns
  the canonical resource-limits check at `warning` severity and
  matches the strict production filename pattern. Method
  `_check_prod_resource_limits` deleted from `docker_compose.py`;
  rule row removed from `skills/V05-docker/SKILL.md`.

- **V03-UNIMPLEMENTED-RPC removed.** Consolidated into
  `V27-UNIMPLEMENTED-RPC`, which enforces the strict Connect handler
  signature shape (`ctx context.Context, req *connect.Request[T]`,
  returns `(*connect.Response[T], error)`). V03's loose
  `func (recv) MethodName(` regex was a coarser approximation that
  could miss handlers with non-standard receivers and double-emit
  on Connect projects (where V27 also fires). Method
  `_check_handler_coverage` deleted from `proto_connect.py`; test
  class `TestCheckHandlerCoverage` deleted from
  `tests/test_proto_connect.py` (3 cases). Equivalent behavior lives
  in `tests/test_connect_handler.py::TestUnimplementedRpc` with stricter
  signature matching. Non-Connect projects no longer get this check
  from V03; if needed, rely on `buf lint` + IDE tooling.

- **V03-BREAKING removed.** Consolidated into `V23-BREAKING-<RULE>`,
  which preserves Buf's per-rule code as the finding suffix
  (`V23-BREAKING-FIELD_NO_DELETE`, etc.) enabling per-rule selective
  disabling via `validators.disabled: ["V23-BREAKING-FIELD_SAME_NAME"]`.
  V03's coarse single-rule emit was duplicated noise. Method
  `_check_breaking` deleted from `proto_connect.py`. V23 already
  uses identical worktree-aware `git rev-parse --git-common-dir`
  logic, so no functional regression.

- **V05↔V26 healthcheck layering documented.** V05-MISSING-HEALTHCHECK
  (warning, all-files) and V26-PROD-NO-HEALTHCHECK (error, prod-only)
  intentionally coexist — V05 is the early permissive nudge, V26 is
  the strict prod gate. Documented as such in both SKILL.md files.

- **V03 narrowed to proto-language scope.** `validate_project` now
  runs lint + stale-gen only. Module docstring rewritten with explicit
  pointers to V27 / V23 for the moved concerns.

- **Test count: 1130 → 1127.** Three tests for the removed V03
  handler-coverage rule are now redundant with the V27 test suite.

## [0.4.0] - 2026-04-30

Third tagged release. Closes the Phase 27 ultrathink **target-project**
audit (the `ai-project-template` companion) by shipping five new
validators (V22, V23, V25, V26, V27) — V24 (Hasura permission audit)
was deliberately cut after user review — plus a V07 boost for Vite
`import.meta.env.VITE_*` typing coverage.

The target was a real OSS template the user maintains: env / config
(Viper) / docker compose / proto / genqlient. Each new validator
crystallizes a class of breakage that the existing V01–V21 surface
silently allowed.

Test count: 1124 → 1130 (six new TestViteEnvTyped cases on top of
the per-validator suites added in Phases 42–47). Total: ~1130
passing.

### Added

- **V22 — Multi-Environment Consistency** (Phase42, Phase27 audit
  proposal A): three rules around APP_-prefixed env vars across
  `.env.example`, `server/config/*.local.yaml`, and `viper.BindEnv`
  call sites.
  - `V22-ENV-PREFIX-DRIFT`: `.env.example` keys with no APP_ prefix
    while a co-located `viper.SetEnvPrefix("APP")` exists.
  - `V22-ENV-CONFIG-DRIFT`: keys present in `.env.example` but missing
    from any `server/config/*.local.yaml` (or vice versa) — a
    classic source of "works on dev, blank on staging" bugs.
  - `V22-VIPER-MISSING-BIND`: a `config.GetX("foo.bar")` call with no
    matching `viper.BindEnv("foo.bar", ...)` — the value is read but
    will never be sourced from the environment.
- **V23 — Buf Governance** (Phase43, Phase27 audit proposal B): three
  rules over `buf.yaml`, `buf.lock`, `buf.gen.yaml`, and the
  `server/proto/` tree.
  - `V23-BUF-LOCK-DRIFT`: `buf.lock` digest mismatch versus the
    `buf.yaml` deps list (catches stale-lock PRs before they hit CI).
  - `V23-BUF-BREAKING`: invokes `buf breaking --against` against the
    base ref when `breaking:` is configured; preserves Buf's own rule
    IDs as `V23-BUF-BREAKING-<rule>`.
  - `V23-PROTOVALIDATE-MISSING`: a request message field declared
    `string` / `int32` / `repeated` without a `[(buf.validate.field)
    = ...]` annotation is flagged as a hint (not an error); zero
    annotations across the entire proto tree warns once at
    `buf.yaml`.
- **V25 — Go Multi-Binary Discipline** (Phase45, Phase27 audit
  proposal D): three rules covering the `cmd/<name>/` folder layout
  used by the target project.
  - `V25-NO-GRACEFUL-SHUTDOWN`: a `cmd/*/main.go` that calls
    `http.ListenAndServe` without a `signal.NotifyContext` /
    `srv.Shutdown` pair (= `kill -TERM` drops in-flight requests).
  - `V25-MISSING-TOOLS-GO`: any `bun run`-style invocation of a
    Go tool whose import is missing from `tools.go` (the
    underscore-import build tag pattern).
  - `V25-AIR-MAPPING-DRIFT`: a `.air.toml` whose `cmd` / `bin`
    entries don't resolve to a real `cmd/<name>/main.go`.
- **V26 — Docker Compose Production Hardening** (Phase46): four
  rules over `docker-compose.prod.yaml` (and any file matched by
  `docker.production_filename_patterns`).
  - `V26-NO-RESOURCE-LIMITS`: a service without `deploy.resources.
    limits.{cpus, memory}` (or top-level `mem_limit` / `cpus` for
    Compose v2 fallback).
  - `V26-NO-HEALTHCHECK`: a service without `healthcheck:` (or
    `healthcheck: disable: true` left in a production file).
  - `V26-INSECURE-SECRET-MOUNT`: secret env files mounted as plain
    `volumes:` instead of `secrets:` blocks (= file is world-readable
    in the container).
  - `V26-VHOST-LOCALHOST`: `VIRTUAL_HOST=localhost` (or `127.0.0.1`)
    in a production-classified compose, which silently breaks
    nginx-proxy SNI.
- **V27 — Connect-RPC Handler Completeness** (Phase47): three rules
  cross-referencing `server/proto/**/*.proto` against
  `server/internal/**/*.go` connectrpc handlers.
  - `V27-UNIMPLEMENTED-RPC`: an `rpc Foo(...)` declared in the proto
    but no `func (s *FooServiceServer) Foo(...)` Go method.
  - `V27-NO-INTERCEPTORS` / `V27-MISSING-{AUTH,LOGGING,VALIDATION}-
    INTERCEPTOR`: a `*Connect.NewXHandler(impl, ...)` call site with
    no `connect.WithInterceptors(...)` (or with one missing the
    auth / logging / validation triplet — the project's documented
    middleware set).
  - `V27-RAW-ERROR-RETURN`: a handler that returns
    `nil, err` or `nil, ErrSomething` instead of wrapping with
    `connect.NewError(connect.Code…, …)` — produces an opaque
    `unknown` gRPC code on the wire.
  - Gated on a Connect import being present anywhere in the Go
    tree, so projects that only ship gRPC (or don't use connectrpc
    at all) pay zero cost.
- **V07-VITE-ENV-TYPED** (Phase48, V07 boost from the same audit):
  every `import.meta.env.VITE_*` reference must appear as a
  `readonly VITE_*: string` declaration inside
  `web/src/vite-env.d.ts` (or `env.d.ts`). Without the typed
  declaration, TypeScript falls back to `string | undefined` /
  `any`, hiding "set in dev but missing in prod" bugs at the
  type level. The `.d.ts` itself is excluded from the scan loop
  so example comments don't self-flag. Six new tests.
- **`skills/V##-{name}/SKILL.md` for V22–V27**: every new
  validator ships with the Rules / Why / Design / How-it-checks /
  Could-be-more-effective / References / Examples template that
  Phase 41a/b/c retrofitted onto V01–V21. The target-project
  bibliography (Buf, connectrpc, Vite, Compose deploy reference)
  is cited inline.

### Changed

- **V07 SKILL.md**: V07-VITE-ENV-TYPED row added to Rules; new
  `_check_vite_env_typed` block in How-it-checks; "vite-env.d.ts
  typing strictness" item in Could-be-more-effective replaced
  with a follow-up about `.env.example` ↔ `vite-env.d.ts`
  cross-check (one rung up from this release).
- **`run_single.py` NAME_MAP**: short aliases added for the five
  new validators (`multi-env`, `buf`, `multibinary`, `docker-prod`,
  `connect-handler`, plus their fully-qualified equivalents).

### Removed

- **V24 — Hasura Permission Audit**: deliberately cut after user
  review. The original proposal C (Phase44) covered Hasura
  permission JSON drift, but the user determined that V20 (Hasura
  GraphQL Enforcement) plus Hasura's own metadata-export round-trip
  already catches the regressions V24 would have detected. The
  V24 namespace stays reserved (no V-ID reuse) so audit references
  in older commits remain stable.

## [0.3.0] - 2026-04-30

Second tagged release. Closes the entire Phase 27 ultrathink audit
(Tier S — S1/S2/S3/S4 — and Tier A — A1–A8) plus the Hermes-Curator-
inspired validator metrics layer. 1060 tests pass, all hook
infrastructure is now per-project, and the validator base API is a
single split (validate_file / validate_project) with no legacy
dispatch.

### BREAKING

- ``BaseValidator.validate(ctx, file_path, mode)`` removed
  (Phase32, S4 4/4). Subclasses now override only ``validate_file``
  and/or ``validate_project``; ``run()`` dispatches based on the
  (file_path, mode) pair. Tests previously calling
  ``validator.validate(...)`` should switch to ``validator.run(...)``.
- Per-validator metric logs moved from the verifiers source-tree
  ``logs/`` to ``<project_root>/.verifiers/state/metrics/`` (Phase33b).
  The legacy path is still used by ``log_exception()`` and as a
  back-compat read fallback in ``scripts/validator_metrics.py``.
- ``router-cache.json`` digest now binds the absolute path
  (Phase37, S3). Pre-Phase37 entries simply mismatch on first
  re-read and are replaced — no migration required.

### Added

- **Validator metrics infrastructure** (Phase33 + Phase33b): per-project
  metric logs at ``<project_root>/.verifiers/state/metrics/V##.jsonl``,
  10MB rotation per validator (single FIFO backup), and a CLI
  (``scripts/validator_metrics.py``) reporting use_count, findings,
  effectiveness, mean duration, and lifecycle state (active / quiet /
  dormant) over a configurable window. ``ProjectContext.metrics_log_dir``
  exposes the path; ``BaseValidator.run()`` rebuilds its JsonLogger
  against ``ctx.metrics_log_dir`` so cross-project mixing is gone. The
  legacy ``logs/`` location is still used by ``log_exception()`` (no
  ctx) and as a back-compat fallback for the CLI when no per-project
  metrics exist yet. Inspired by NousResearch/hermes-agent#7816 — long-
  lived self-improving agents need usage records before they can prune.
- **V21 Pytest Runner** (Phase28, S2): pytest path split out of V19 into
  a dedicated `hooks/validators/py_pytest.py` so the Tier 3 parallel
  runner sees ruff and test execution as independent units. The
  `stop.run_pytest` config key gates the new validator with three modes:
  - `"smart"` (default) — pytest runs only when this turn's working
    tree has uncommitted `.py` / `pyproject.toml` changes (heuristic:
    `git diff --name-only HEAD`). Markdown/yaml-only turns skip pytest.
  - `"always"` — legacy V19 behavior, runs on every Stop hook.
  - `"never"` — Stop never runs pytest; CI is the safety net.
  - Falls open (runs pytest) when git is unavailable so a misconfigured
    repo never silently suppresses the test gate.
  - 25 new tests (`tests/test_py_pytest.py`) cover smart trigger, mode
    gating, and pytest failure parsing. Total tests: 1027.
- **`StopConfig` dataclass** (Phase28): new `stop:` block in
  `.verifiers/config.yaml`. Invalid values fall back to `"smart"` rather
  than silently disabling pytest.
- **`Finding.kind` field** (Phase36, A4): distinguishes ordinary
  findings from sentinels (V##-CRASHED, V##-TIMEOUT) so the Tier 3
  ``_apply_exclude_filters`` can short-circuit on sentinels —
  ``exclude.paths: ["**"]`` no longer silences a crashed worker.
- **`ProjectContext.is_excluded(path)`** (Phase34, S1): centralized
  gitignore-glob exclusion check. Validators' scan loops call this
  first, the hard-coded vendor / node_modules backstops second. Closes
  the substring-exclusion regression that ``lib/exclusion`` was
  introduced to abolish.
- **`ProjectContext.metrics_log_dir`** (Phase33b): per-project metric
  output directory under ``.verifiers/state/metrics/``.
- **`lib/validator_registry.resolve_active_validators(ctx, source=...)`**
  (Phase35, A1): single helper for the
  ``get_all_validators → filter_enabled → ValueError handler →
  filter_disabled`` pipeline. router and stop_validator both call it;
  the four-step duplication is gone.
- **`lib/secret_regexes.py`** (Phase38, A3): zero-dep source of truth
  for the SECRET_REGEXES table + path classification primitives. Tier 1
  (``security_hook``) and V08 (``hooks/validators/security``) both
  import from here, closing the drift surface where Tier 1's P2-2
  password-regex fix had failed to land in V08.
- **JsonLogger size rotation** (Phase33b): per-validator JSONL files
  are renamed to ``<file>.1`` once they exceed 10 MB, single FIFO
  backup. Caps disk usage per validator at ~20 MB.
- **State directory permissions** (Phase37, A6): every state-writing
  module (``router_cache``, ``json_logger`` ×2, ``feedback_tracker``)
  now creates state dirs with mode ``0o700`` and follows up with
  ``chmod(0o700)`` so shared CI hosts don't leak project-private state.
- **Symlink refusal for `.verifiers/config.yaml`** (Phase37, A6):
  ``config_loader.load_config`` now refuses symlinked configs and
  falls back to defaults. Pre-emptive — closes a future
  info-disclosure surface if anyone later logs raw config content.

### Changed

- **V19 Python Quality** (Phase28): now ruff-only (`V19-RUFF-CHECK`,
  `V19-RUFF-FORMAT`, `V19-RUFF-ALL`). The `V19-TEST-FAIL` rule moved to
  `V21-TEST-FAIL`. Existing projects that disable V19 in
  `validators.disabled` are unaffected — V19 still owns the ruff lint
  surface; pytest control is in the new V21 namespace.
- **`docs/CONFIGURATION.md`** + **`docs/VERIFIERS-CATALOG.md`**: stop
  block documented, V21 entry added, V19 entry rewritten to drop the
  pytest claim.
- **BaseValidator API** (Phase29 → Phase32, S4): legacy single-method
  ``validate(ctx, file_path, mode)`` retired. All 20 validators
  migrated to override ``validate_file`` and/or ``validate_project``.
  ``run()`` is now the only entry point — it dispatches based on
  (file_path, mode) and adds JSON logging around the call.
  ABC inheritance dropped (no abstract method left). Net: ~150 LOC
  of mode-sniffing if-ladder gone across the validator suite, and
  ``docker_compose``'s pre-Phase31b silent project-scan-on-Tier-2
  is structurally impossible.
- **Tier 3 parallel runner** (Phase36, A2 + A7): ``ProcessPoolExecutor``
  → ``ThreadPoolExecutor``. Every heavy validator releases the GIL
  during ``subprocess.run`` (ruff / pytest / golangci / tsc / eslint),
  so threads parallelize without paying ~200 ms / Stop hook for spawn
  + ProjectContext pickling. ``DEFAULT_MAX_WORKERS`` raised from 4 to
  8 — Phase28's V19 split + the rest of the long tail now actually
  fills slots. ``pickle.PicklingError`` fallback path retired
  (threads don't pickle); ``transport-CRASHED`` outer branch retired
  (the inner ``_run_one_validator`` sentinel covers it).
- **Router cache poisoning closed** (Phase37, S3): the digest in
  ``file_content_hash`` now binds the absolute path
  (``sha256(path + b"\\0" + content)``). A pre-recorded ``router-
  cache.json`` entry can no longer collide with the bytes of a
  different file.
- **`stop.run_pytest: smart` heuristic upgraded** by ``has_uncommitted_python_changes``
  (Phase28): pytest only runs when ``git diff --name-only HEAD`` shows
  ``.py`` / ``pyproject.toml`` changes. Markdown/yaml-only turns skip.
- **Hook stdin reads capped at 1 MiB** (Phase38, A5):
  ``read_hook_input`` and ``security_hook.main`` no longer accept
  unbounded input from the documented standalone CLI surface.

## [0.2.0] - 2026-04-29

First tagged release. Bundles Phases 1–25 (P0/P1/P2 audit triage,
Tier 2 router auto-gateway, ProcessPoolExecutor parallel runner,
SecurityConfig + DockerConfig per-project tuning, classical-school
testing skill, dogfood CI, configuration documentation).

The initial entries below are scoped to this release. Future
releases will get their own dated section per Keep-a-Changelog.

### BREAKING

- **`docker.vhost_check_mode` default = `"production"`** (Phase21):
  the V05-VHOST-NO-NETWORK rule now fires only on production-classified
  compose files (matched by `dev_filename_patterns` / its built-in
  fallback). Previously every compose file was checked, producing a
  false positive whenever a local `docker-compose.yaml` set
  `VIRTUAL_HOST` for production parity but had no `nginx-proxy`
  network. Set `docker.vhost_check_mode: "all"` in
  `.verifiers/config.yaml` to restore the strict legacy behavior.
  See [docs/CONFIGURATION.md](docs/CONFIGURATION.md#1-풀-스키마).

### Added

- **CI** (P0-1): `.github/workflows/ci.yml` runs pytest + ruff + format
  check on a Python 3.11/3.12/3.13 matrix, plus a `dogfood` job that
  applies the project's own Tier 1 + Tier 3 hooks to every PR.
- **LICENSE** (P0-2): standard MIT text — backs the README's prior
  "License: MIT" claim that GitHub couldn't auto-classify.
- **V19 / V20 unit tests** (P0-3): `tests/test_py_quality.py` (V19,
  22 cases) and `tests/test_hasura_graphql_enforcement.py` (V20, 26
  cases). Both validators previously had zero coverage.
- **Structured exception logging** (P0-4): new `lib.json_logger.log_exception`
  appends to `logs/_errors.jsonl` and prints to stderr when
  `VERIFIERS_DEBUG=1`. Replaces 8 silent `except Exception: pass` sites
  in router, stop_validator, run_single, docker_compose, and
  linter_config_guard.
- **V20 Hasura GraphQL Enforcement** (P1-1): the previously orphaned
  `hasura_graphql_enforcement.py` module is now wired into
  `get_all_validators()`. Detects raw-SQL usage in Go files when Hasura
  is present; early-returns to zero cost otherwise.
- **V-ID dedup invariant** (P1-2): `_assert_registry_invariants()` runs
  at registry build time and raises `RuntimeError` on duplicate V-IDs,
  missing `V<NN>-` prefix, or prefix collision across modules.
- **Per-project config** (P1-3): `<project>/.verifiers/config.yaml`
  loaded by `lib.config_loader`. Schema covers complexity / commit /
  test-runner thresholds, exclusion globs, and validators.disabled.
  V14 (Complexity Guard) reads thresholds from this config; V12 / V09–
  V11 wiring is queued.
- **Central path exclusion** (P1-4): new `lib.exclusion.is_excluded`
  matches gitignore-style globs from `ctx.config.exclude.paths`.
  `hooks/router.py` short-circuits before invoking validators when a
  path matches. Recent "fix: skip X directory" patches no longer need
  to live inside individual validators.
- **Findings deduplication** (P1-7): `_dedup_findings` collapses
  identical `(rule, file, line, message)` tuples in `format_output`,
  preventing Tier 1 + Tier 3 from billing Claude twice for the same
  detection.
- **`.verifiers/state/` location** (P1-8): the circuit-breaker
  counter `.verifier-block-count` moved from the project root into the
  project's own `.verifiers/state/` namespace. Legacy path is read
  once for back-compat then unlinked.
- **PEP 723 inline-deps drift gate** (P1-6): `scripts/sync_inline_deps.py`
  enforces that every hook's `# dependencies = [...]` block matches
  pyproject.toml's version pins (subset semantics — empty inline lists
  are allowed). `--check` mode wired into a dedicated CI job.
- **CONTRIBUTING.md**: developer guide covering the validator-addition
  workflow, V-ID assignment, mode-dispatch boilerplate, and PR checklist.

### Changed

- **V20 namespace**: `hasura_graphql_enforcement.py` rule strings moved
  from `V15-*` to `V20-*` to free V15 for `dependency_guard.py` alone.
  V-ID ↔ module mapping is now 1:1 (relied on by `run_single.py` and
  `docs/VERIFIERS-CATALOG.md`).
- **Tier 1 password regex** (P2-2): `[^"'$\{]` → `[^"'${}]` so
  `password = "{{ env.PASSWORD }}"` Go/Helm template placeholders no
  longer produce false positives.
- **Tier 1 path exclusion** (P2-3): substring-matched `EXCLUDE_PATHS`
  replaced by a `_is_excluded_path` helper that uses `Path.parts` for
  directory components and exact-name lookups for `.env` variants.
  `mockingbird/Real.go` is no longer wrongly exempted by the literal
  "mock".
- **`.gitignore`**: adds `.verifiers/state/` (machine-generated state)
  and `.omc/` (OMC plugin state). Tracked configs like
  `.verifiers/layers.yaml` stay versioned.
- **README + docs/VERIFIERS-CATALOG.md**: corrected validator counts
  (now 19 registered validators across V01–V20, with V17 noted as not
  implemented), V20 added to the catalog, ASCII diagrams replaced with
  Markdown tables (CJK-safe rendering).
- **`run_single.py` NAME_MAP**: added missing entries for V18, V19, V20.
  V05 short id corrected from stale `V05-docker-compose` to `V05-docker`.

### Fixed

- **22 PHI logging tests**: pre-existing test inputs in
  `tests/test_security.py` used fixed-string keyword mentions, but the
  PHI regex was tightened in a prior fix to flag only data-binding
  patterns. Tests updated to use real bindings (zerolog `.Str()`,
  Sprintf with named field, bare `console.log(field)`).
- **2 complexity threshold tests**: `tests/test_complexity_guard.py`
  generated 55-line bodies but the threshold was 80; bumped to 90/160.
- **2 ruff lint debts**: `E741` ambiguous `l` and `F541` f-string-
  without-placeholder in `docker_compose.py`.

### Added (post-deferred batch)

- **V12 / V09 / V10 / V11 config wiring** (phase11, P1-3 follow-up):
  the four remaining hardcoded thresholds now read from
  `ctx.config.thresholds.commit.large_diff_files` and
  `ctx.config.thresholds.test_runner.repeated_failure_count`.
  Module-level `LARGE_DIFF_THRESHOLD` / `REPEATED_FAIL_THRESHOLD`
  constants stay as default fallbacks for back-compat.
- **Tier 3 ProcessPoolExecutor parallelization** (phase12, P1-5):
  new `lib/parallel_runner.py` farms each Stop-mode validator into its
  own worker process (4 workers default, 30s per-validator timeout).
  Crashed/timed-out validators emit `V##-CRASHED` / `V##-TIMEOUT`
  sentinel `Finding`s so the Stop hook can never silent-approve.
  `VERIFIERS_PARALLEL=0` opts out; auto-fallback to sequential on
  `pickle.PicklingError` or `OSError` during pool setup.
- **Tier 2 router auto-gateway** (phase13, P2-1): `merge_settings.py`
  now registers a third hook entry on PostToolUse so `hooks/router.py`
  fires after every Edit/Write/MultiEdit. Two prefilters keep the
  per-Edit cost low: an extension prefilter short-circuits when no
  active validator's `should_run()` matches the file, and a content-
  hash cache at `<project>/.verifiers/state/router-cache.json` (1000
  entries, FIFO eviction) skips re-runs on identical content.
- **`test-classical` skill** (phase14): codifies the Classical
  (Chicago) testing rules from
  [Atipico1/ai-testing-rules](https://github.com/Atipico1/ai-testing-rules)
  — mock only at system boundary, assert on observable state, use real
  filesystems / module-level dataclass doubles instead of
  `mock.patch`. Installed alongside the `verify-*` skills via
  `just install` / `just install-project`. New CONTRIBUTING.md
  subsection makes the style mandatory for this project.
- **V05 DockerConfig** (phase21): new `docker:` config block with six
  knobs. Beyond the BREAKING `vhost_check_mode` default change above:
  - `reverse_proxy_networks` lets Traefik / custom proxies satisfy
    V05-VHOST-NO-NETWORK (was hardcoded to `nginx-proxy`).
  - `production_filename_patterns` / `dev_filename_patterns` (fnmatch
    globs) override the built-in classification when a project uses a
    non-standard compose filename (e.g. `*-prd.yaml`, `*.local.yaml`).
  - `production_stage_names` / `dev_stage_names` reclassify Dockerfile
    multi-stage names so V05-DOCKERFILE-NO-USER and
    V05-DEV-NO-BUILD-TARGET match company conventions like `dist` /
    `develop`.
  - Each list follows SecurityConfig's "empty → built-in defaults,
    non-empty → replace" semantics.
  - 8 unit + 7 integration tests prove every knob's effect end-to-end
    against real `.verifiers/config.yaml` files. Total tests: 1003.
