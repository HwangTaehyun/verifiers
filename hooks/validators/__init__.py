"""Validator registry — all available validators."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseValidator


def get_all_validators() -> list[BaseValidator]:
    """Return instances of all registered validators."""
    from .ai_cheating_guard import AiCheatingGuardValidator
    from .commit_discipline import CommitDisciplineValidator
    from .complexity_guard import ComplexityGuardValidator
    from .dependency_guard import DependencyGuardValidator
    from .docker_compose import DockerComposeValidator
    from .env_config import EnvConfigValidator
    from .go_quality import GoQualityValidator
    from .go_test_runner import GoTestRunnerValidator
    from .graphql_gen import GraphqlGenValidator
    from .hasura_migration import HasuraMigrationValidator
    from .linter_config_guard import LinterConfigGuardValidator
    from .proto_connect import ProtoConnectValidator
    from .py_test_runner import PyTestRunnerValidator
    from .security import SecurityValidator
    from .ts_quality import TsQualityValidator
    from .ts_test_runner import TsTestRunnerValidator

    return [
        SecurityValidator(),  # V08 — highest priority, lightest
        AiCheatingGuardValidator(),  # V13 — AI cheating detection
        EnvConfigValidator(),  # V01
        GraphqlGenValidator(),  # V02
        ProtoConnectValidator(),  # V03
        HasuraMigrationValidator(),  # V04
        DockerComposeValidator(),  # V05
        GoQualityValidator(),  # V06
        TsQualityValidator(),  # V07
        GoTestRunnerValidator(),  # V09
        TsTestRunnerValidator(),  # V10
        PyTestRunnerValidator(),  # V11
        CommitDisciplineValidator(),  # V12 — commit hygiene (stop mode only)
        ComplexityGuardValidator(),  # V14 — complexity metrics
        DependencyGuardValidator(),  # V15 — layer direction enforcement
        LinterConfigGuardValidator(),  # V16 — linter config enforcement (stop mode only)
    ]
