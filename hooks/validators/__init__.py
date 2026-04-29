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
    from .ai_cheating_guard import AiCheatingGuardValidator
    from .commit_discipline import CommitDisciplineValidator
    from .complexity_guard import ComplexityGuardValidator
    from .dependency_guard import DependencyGuardValidator
    from .docker_compose import DockerValidator as DockerComposeValidator

    # from .docker_prod_deploy import DockerProdDeployValidator  # TODO: not yet implemented
    from .env_config import EnvConfigValidator
    from .go_quality import GoQualityValidator
    from .go_test_runner import GoTestRunnerValidator
    from .graphql_gen import GraphqlGenValidator
    from .hasura_graphql_enforcement import HasuraGraphQLEnforcementValidator
    from .hasura_migration import HasuraMigrationValidator
    from .linter_config_guard import LinterConfigGuardValidator
    from .mock_data_guard import MockDataGuardValidator
    from .proto_connect import ProtoConnectValidator
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
        PyQualityValidator(),  # V19 — Python ruff + pytest
        CommitDisciplineValidator(),  # V12 — commit hygiene (stop mode only)
        ComplexityGuardValidator(),  # V14 — complexity metrics
        DependencyGuardValidator(),  # V15 — layer direction enforcement
        LinterConfigGuardValidator(),  # V16 — linter config enforcement (stop mode only)
        MockDataGuardValidator(),  # V18 — mock data detection in frontend hooks
        HasuraGraphQLEnforcementValidator(),  # V20 — raw SQL forbidden when Hasura present
    ]
    _assert_registry_invariants(validators)
    return validators
