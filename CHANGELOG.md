# Changelog

All notable changes to verifiers are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The audit IDs cited below (P0-x, P1-x, P2-x) refer to the project-improvement
audit completed in 2026-04. They're kept here so future commits can link to
the original rationale.

## [Unreleased]

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
