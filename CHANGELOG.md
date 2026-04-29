# Changelog

All notable changes to verifiers are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The audit IDs cited below (P0-x, P1-x, P2-x) refer to the project-improvement
audit completed in 2026-04. They're kept here so future commits can link to
the original rationale.

## [Unreleased]

### Added

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

### Changed

- **V19 Python Quality** (Phase28): now ruff-only (`V19-RUFF-CHECK`,
  `V19-RUFF-FORMAT`, `V19-RUFF-ALL`). The `V19-TEST-FAIL` rule moved to
  `V21-TEST-FAIL`. Existing projects that disable V19 in
  `validators.disabled` are unaffected — V19 still owns the ruff lint
  surface; pytest control is in the new V21 namespace.
- **`docs/CONFIGURATION.md`** + **`docs/VERIFIERS-CATALOG.md`**: stop
  block documented, V21 entry added, V19 entry rewritten to drop the
  pytest claim.

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
