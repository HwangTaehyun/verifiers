"""Validator registry — all available validators.

Registration invariants (P1-2 dedup) enforced at runtime by
``_assert_registry_invariants`` so misregistration surfaces immediately
at import time rather than producing silently overlapping findings:

  1. Every validator id must be unique across the registry.
  2. Every validator id must start with a 'V<NN>-' prefix (e.g. V01-, V20-).
  3. The mapping V-ID prefix → validator module is 1:1; ``run_single.py``
     and ``docs/VERIFIERS-CATALOG.md`` rely on this guarantee.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseValidator


_VID_PREFIX_RE = re.compile(r"^(V\d{2})-")

# Phase64.2: extract a "primary extension" from a file_patterns entry so
# we can build an O(1) ext → [validators] lookup at registry import time.
# Patterns that look like ``**/*.go`` or ``*.go`` fall into the ext bucket;
# patterns like ``go.mod``, ``Dockerfile*``, ``buf.yaml`` go into a
# residual list that still needs the per-validator ``should_run`` regex
# to match. Patterns containing a leading ``.`` followed by alnum at end
# of string count as the extension form.
_EXT_RE = re.compile(r"\*\.([A-Za-z0-9]+)$")


def _assert_registry_invariants(validators: "list[BaseValidator]") -> None:
    """Enforce the registry invariants documented above.

    Raises ``RuntimeError`` if any invariant is violated. Caught by the
    Tier 3 entry point's outer try/except, but kept loud (not silent) so
    test fixtures and CI surface the problem immediately.
    """
    ids: set[str] = set()
    prefixes: dict[str, str] = {}
    for v in validators:
        if not v.id:
            raise RuntimeError(f"Validator {type(v).__name__} has empty id")

        match = _VID_PREFIX_RE.match(v.id)
        if not match:
            raise RuntimeError(f"Validator id '{v.id}' must start with V<NN>- prefix (e.g. 'V01-...')")

        if v.id in ids:
            raise RuntimeError(f"Duplicate validator id '{v.id}' in registry")
        ids.add(v.id)

        prefix = match.group(1)
        if prefix in prefixes and prefixes[prefix] != v.id:
            raise RuntimeError(
                f"V-ID prefix collision: '{prefix}' used by both "
                f"'{prefixes[prefix]}' and '{v.id}' — assign a fresh V<NN>"
            )
        prefixes[prefix] = v.id


@functools.lru_cache(maxsize=1)
def get_all_validators() -> list[BaseValidator]:
    """Return instances of all registered validators.

    Phase62-N4: cached via ``functools.lru_cache`` so repeated calls
    within the same process (e.g. router → stop_validator chain) reuse
    the imported modules + validator instances rather than re-importing
    49 modules on every invocation. Cuts ~200ms off Tier-2/Tier-3
    sequential invocations and preserves per-validator state (e.g.
    ``ProtoConnectValidator.hash_cache``) across calls.

    The cache is process-local; tests that need a fresh registry can
    call ``get_all_validators.cache_clear()``.
    """
    from .actions_permissions_block import ActionsPermissionsBlockValidator
    from .actions_sha_pin import ActionsSHAPinValidator
    from .adr_template_compliance import AdrTemplateComplianceValidator
    from .ai_cheating_guard import AiCheatingGuardValidator
    from .buf_governance import BufGovernanceValidator
    from .ci_image_scanning import CiImageScanningValidator
    from .commit_discipline import CommitDisciplineValidator
    from .commitlint_gate import CommitlintGateValidator
    from .complexity_guard import ComplexityGuardValidator
    from .connect_handler import ConnectHandlerValidator
    from .dependabot_config import DependabotConfigValidator
    from .dependency_guard import DependencyGuardValidator
    from .docker_compose import DockerValidator as DockerComposeValidator
    from .docker_prod_hardening import DockerProdHardeningValidator
    from .dockerfile_base_digest import DockerfileBaseDigestValidator
    from .dockerfile_healthcheck import DockerfileHealthcheckValidator

    # from .docker_prod_deploy import DockerProdDeployValidator  # TODO: not yet implemented
    from .env_config import EnvConfigValidator
    from .fk_index_discipline import FkIndexDisciplineValidator
    from .go_context_propagation import GoContextPropagationValidator
    from .go_context_scoped_logger import GoContextScopedLoggerValidator
    from .go_error_wrapping import GoErrorWrappingValidator
    from .go_http_hardening import GoHttpHardeningValidator
    from .go_multibinary import GoMultiBinaryValidator
    from .go_quality import GoQualityValidator
    from .go_test_race_coverage import GoTestRaceCoverageValidator
    from .github_community_files import GithubCommunityFilesValidator
    from .go_layer_imports import GoLayerImportsValidator
    from .go_sql_parameterization import GoSqlParameterizationValidator
    from .go_test_runner import GoTestRunnerValidator
    from .golangci_strictness import GolangciStrictnessValidator
    from .graphql_gen import GraphqlGenValidator
    from .hasura_graphql_enforcement import HasuraGraphQLEnforcementValidator
    from .hasura_migration import HasuraMigrationValidator
    from .hasura_permission_rationale import HasuraPermissionRationaleValidator
    from .health_endpoint_split import HealthEndpointSplitValidator
    from .linter_config_guard import LinterConfigGuardValidator
    from .migration_enum_rollback import MigrationEnumRollbackValidator
    from .mock_data_guard import MockDataGuardValidator
    from .multi_env import MultiEnvConsistencyValidator
    from .otel_instrumentation import OtelInstrumentationValidator
    from .prometheus_metrics_endpoint import PrometheusMetricsEndpointValidator
    from .proto_connect import ProtoConnectValidator
    from .py_pytest import PyPytestValidator
    from .py_quality import PyQualityValidator
    from .py_test_runner import PyTestRunnerValidator
    from .readme_badges import ReadmeBadgesValidator
    from .reproducible_build_markers import ReproducibleBuildMarkersValidator
    from .rhf_zod_schema_sync import RhfZodSchemaSyncValidator
    from .sbom_ci_step import SbomCiStepValidator
    from .security import SecurityValidator
    from .ts_any_budget import TsAnyBudgetValidator
    from .ts_layer_imports import TsLayerImportsValidator
    from .ts_quality import TsQualityValidator
    from .ts_test_runner import TsTestRunnerValidator

    validators: list[BaseValidator] = [
        SecurityValidator(),  # V08 — highest priority, lightest
        AiCheatingGuardValidator(),  # V13 — AI cheating detection
        EnvConfigValidator(),  # V01
        GraphqlGenValidator(),  # V02
        ProtoConnectValidator(),  # V03
        HasuraMigrationValidator(),  # V04
        DockerComposeValidator(),  # V05
        # DockerProdDeployValidator(),  # V17 — not yet implemented
        GoQualityValidator(),  # V06
        TsQualityValidator(),  # V07
        GoTestRunnerValidator(),  # V09
        TsTestRunnerValidator(),  # V10
        PyTestRunnerValidator(),  # V11
        PyQualityValidator(),  # V19 — Python ruff (lint/format/all)
        PyPytestValidator(),  # V21 — Python pytest (Stop, gated by stop.run_pytest)
        CommitDisciplineValidator(),  # V12 — commit hygiene (stop mode only)
        ComplexityGuardValidator(),  # V14 — complexity metrics
        DependencyGuardValidator(),  # V15 — layer direction enforcement
        LinterConfigGuardValidator(),  # V16 — linter config enforcement (stop mode only)
        MockDataGuardValidator(),  # V18 — mock data detection in frontend hooks
        HasuraGraphQLEnforcementValidator(),  # V20 — raw SQL forbidden when Hasura present
        MultiEnvConsistencyValidator(),  # V22 — APP_ prefix + root/server drift + viper key↔env
        BufGovernanceValidator(),  # V23 — buf.lock drift + breaking + protovalidate
        GoMultiBinaryValidator(),  # V25 — graceful shutdown + tools.go + air mapping
        DockerProdHardeningValidator(),  # V26 — production compose hardening
        ConnectHandlerValidator(),  # V27 — connect-rpc handler completeness
        # Phase54 Sprint 1 (medical/finance ship-blockers from Phase53 audit):
        GoHttpHardeningValidator(),  # V36 — HTTP server timeouts + graceful shutdown
        ActionsSHAPinValidator(),  # V40 — third-party Action SHA pinning
        FkIndexDisciplineValidator(),  # V47 — Postgres FK columns must be indexed
        HealthEndpointSplitValidator(),  # V50 — /livez vs /readyz split
        # Phase54 Sprint 2 (CI hardening from Phase53 audit):
        GoTestRaceCoverageValidator(),  # V37 — go test -race + coverage gate
        ActionsPermissionsBlockValidator(),  # V41 — workflow permissions: block
        CiImageScanningValidator(),  # V43 — Trivy/Grype scan in CI
        # Phase55 (Sprint 3 governance + observability + Long tail batch 1):
        DependabotConfigValidator(),  # V42 — Dependabot/Renovate config presence
        OtelInstrumentationValidator(),  # V49 — OpenTelemetry SDK presence + wiring
        GoErrorWrappingValidator(),  # V34 — bare `return err` without %w
        GoContextPropagationValidator(),  # V35 — mid-flow context.Background()
        # Phase56 (Long tail batch 2):
        GolangciStrictnessValidator(),  # V38 — wrapcheck + nolintlint config
        GoContextScopedLoggerValidator(),  # V39 — zerolog.Ctx(ctx) discipline
        DockerfileBaseDigestValidator(),  # V44 — FROM lines need @sha256 digest
        DockerfileHealthcheckValidator(),  # V45 — HEALTHCHECK in HTTP-service stages
        # Phase57 (Long tail batch 3 — final batch from Phase 53 audit):
        MigrationEnumRollbackValidator(),  # V46 — ALTER TYPE ADD VALUE rollback
        HasuraPermissionRationaleValidator(),  # V48 — select-only intent doc
        # Phase58 Sprint A (docs/observability/supply-chain audit, Sprint A):
        # V55 (error-tracking-sdk / Sentry) cut by user decision —
        # too opinionated for the template; teams pick their own
        # tracking stack (Sentry / GlitchTip / Datadog / Honeybadger /
        # none). V55 namespace stays reserved (no V-ID reuse) so older
        # commits + audit history references remain stable.
        GithubCommunityFilesValidator(),  # V53 — PR/ISSUE templates + CODEOWNERS
        CommitlintGateValidator(),  # V54 — Conventional Commits enforcement gate
        PrometheusMetricsEndpointValidator(),  # V56 — /metrics endpoint + SDK
        # Phase58 Sprint B (docs + supply chain tail, completing Phase 58 audit):
        AdrTemplateComplianceValidator(),  # V51 — ADR Nygard format compliance
        ReadmeBadgesValidator(),  # V52 — README CI + license badges
        SbomCiStepValidator(),  # V57 — SBOM generation in CI
        ReproducibleBuildMarkersValidator(),  # V58 — SOURCE_DATE_EPOCH for reproducible builds
        # Phase 72 (Tier A from end-of-session review): architecture +
        # security + type-safety ratchet at 1M-LOC scale.
        GoLayerImportsValidator(),  # V60 — handler→service→repo direction enforcement
        GoSqlParameterizationValidator(),  # V61 — SQL string concat / fmt.Sprintf injection
        TsLayerImportsValidator(),  # V64 — dependency-cruiser config presence (detection)
        TsAnyBudgetValidator(),  # V65 — `: any` / @ts-expect-error ratchet
        RhfZodSchemaSyncValidator(),  # V76 — useForm<T> ↔ z.infer<typeof S> sync
    ]
    _assert_registry_invariants(validators)
    return validators


# ── Phase64.2: file-extension bucketed dispatch index ────────────────────


def _classify_pattern(pattern: str) -> str | None:
    """Return the dot-extension this pattern targets, or None when the
    pattern is a non-extension match (filename glob, exact filename).

    Examples:
        "**/*.go"               → ".go"
        "*.tsx"                 → ".tsx"
        "**/pyproject.toml"     → ".toml"  (last *.<ext> still wins)
        "go.mod"                → None     (exact filename)
        "Dockerfile*"           → None     (no ext)
        "**/__generated__/**"   → None     (catch-all)
    """
    m = _EXT_RE.search(pattern)
    if not m:
        return None
    return f".{m.group(1).lower()}"


@functools.lru_cache(maxsize=1)
def _build_dispatch_index() -> tuple[dict[str, list["BaseValidator"]], list["BaseValidator"], list["BaseValidator"]]:
    """Phase64.2 — bucket the 49-validator registry by file extension.

    Returns ``(ext_index, residual, wildcard)`` where:
      - ``ext_index``: ``{".go": [V06, V09, ...], ".ts": [V07, V10, ...]}``
        Validators whose file_patterns include at least one ``*.<ext>``
        glob land here under EVERY extension they declare.
      - ``residual``: validators whose file_patterns include at least
        one non-extension pattern (e.g. ``go.mod``, ``Dockerfile*``,
        ``buf.yaml``). These still need ``v.should_run(file)`` to
        confirm — the index can't pre-resolve filename-glob hits.
      - ``wildcard``: validators with empty file_patterns (e.g. V08
        security, V12 commit-discipline). They run on every edit per
        their existing ``should_run`` semantics.

    The split lets a router lookup do ``ext_index[suffix] + residual``
    instead of iterating all 49 validators per Edit. ``wildcard`` is
    appended too because ``BaseValidator.should_run`` returns True for
    them regardless. Validators are de-duplicated in the caller.

    Cached via ``functools.lru_cache`` so the registry walk happens once
    per process; ``cache_clear()`` for tests that swap the registry.
    """
    ext_index: dict[str, list[BaseValidator]] = {}
    residual: list[BaseValidator] = []
    wildcard: list[BaseValidator] = []
    for v in get_all_validators():
        patterns = v.file_patterns or []
        if not patterns:
            wildcard.append(v)
            continue
        has_ext = False
        has_non_ext = False
        seen_ext_for_v: set[str] = set()
        for pat in patterns:
            ext = _classify_pattern(pat)
            if ext:
                has_ext = True
                if ext not in seen_ext_for_v:
                    ext_index.setdefault(ext, []).append(v)
                    seen_ext_for_v.add(ext)
            else:
                has_non_ext = True
        # A validator with both ``**/*.go`` AND ``go.mod`` patterns lands
        # in BOTH ext_index[".go"] AND residual — caller dedupes.
        if has_non_ext:
            residual.append(v)
        # Defensive: a validator whose every pattern was unclassifiable
        # still needs to be reachable. Treat as residual so should_run
        # gets the chance to match.
        if not has_ext and not has_non_ext:
            residual.append(v)  # pragma: no cover — defensive
    return ext_index, residual, wildcard


def get_matching_validators(file_path: str, active: "list[BaseValidator]") -> "list[BaseValidator]":
    """Phase64.2 — fast dispatch: return active validators whose
    ``should_run(file_path)`` is True, skipping the per-Edit O(N=49)
    regex scan when the file's suffix is enough to narrow the field.

    Algorithm:
      1. Use ``_build_dispatch_index`` to get the registry buckets.
      2. Compute candidates = ext_index[suffix] ∪ residual ∪ wildcard.
      3. Intersect with the caller-supplied ``active`` list (reflects
         enabled/disabled filtering already done upstream).
      4. Run ``v.should_run(file)`` on candidates only — Phase62 N3
         pre-compiled regex still applies.

    Falls open: if the suffix isn't recognized, candidates degrade to
    ``residual + wildcard`` which still has to do regex matching, but
    that path only fires for unusual extensions.
    """
    ext_index, residual, wildcard = _build_dispatch_index()
    suffix = Path(file_path).suffix.lower()
    bucket = ext_index.get(suffix, [])

    # Build candidate set by identity (validator instances are hashable
    # by default since they're plain objects). De-dup across buckets.
    seen_ids: set[int] = set()
    candidates: list[BaseValidator] = []
    for v in (*bucket, *residual, *wildcard):
        if id(v) in seen_ids:
            continue
        seen_ids.add(id(v))
        candidates.append(v)

    # Restrict to the active set (preserves caller's enabled/disabled
    # filtering and the order it provided).
    active_ids = {id(v) for v in active}
    narrowed = [v for v in candidates if id(v) in active_ids]

    # Final regex match — required for residual (filename globs like
    # ``go.mod``) and the rare ext_index member that has additional
    # constraints (e.g. ``**/__tests__/**/*.ts``).
    return [v for v in narrowed if v.should_run(file_path)]
