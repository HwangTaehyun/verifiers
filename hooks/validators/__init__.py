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

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseValidator


_VID_PREFIX_RE = re.compile(r"^(V\d{2})-")


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


def get_all_validators() -> list[BaseValidator]:
    """Return instances of all registered validators."""
    from .actions_permissions_block import ActionsPermissionsBlockValidator
    from .actions_sha_pin import ActionsSHAPinValidator
    from .ai_cheating_guard import AiCheatingGuardValidator
    from .buf_governance import BufGovernanceValidator
    from .ci_image_scanning import CiImageScanningValidator
    from .commit_discipline import CommitDisciplineValidator
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
    from .proto_connect import ProtoConnectValidator
    from .py_pytest import PyPytestValidator
    from .py_quality import PyQualityValidator
    from .py_test_runner import PyTestRunnerValidator
    from .security import SecurityValidator
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
    ]
    _assert_registry_invariants(validators)
    return validators
