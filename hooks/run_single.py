#!/usr/bin/env python3
"""Run a single validator by name.

Usage:
    echo '{"cwd": "/project"}' | uv run --script hooks/run_single.py env-config
    echo '{"cwd": "/project"}' | uv run --script hooks/run_single.py security
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks.validators import get_all_validators
from hooks.validators.base import Finding, format_output, read_hook_input, write_hook_output
from lib.json_logger import log_exception
from lib.project_context import ProjectContext

# Map of short names to validator IDs.
# Kept in sync with hooks/validators/__init__.py:get_all_validators().
# V17 (UI verifier) is intentionally absent — not yet implemented as a
# Python validator (see CATALOG §6.2).
NAME_MAP = {
    # V01 — env config
    "env-config": "V01-env-config",
    "env": "V01-env-config",
    # V02 — GraphQL/genqlient
    "graphql-gen": "V02-graphql-gen",
    "graphql": "V02-graphql-gen",
    # V03 — proto/Connect-RPC
    "proto-connect": "V03-proto-connect",
    "proto": "V03-proto-connect",
    # V04 — Hasura migration safety
    "hasura-migration": "V04-hasura-migration",
    "hasura": "V04-hasura-migration",
    # V05 — Docker compose / Dockerfile
    "docker-compose": "V05-docker",
    "docker": "V05-docker",
    # V06 — Go quality
    "go-quality": "V06-go-quality",
    "go": "V06-go-quality",
    # V07 — TS quality
    "ts-quality": "V07-ts-quality",
    "ts": "V07-ts-quality",
    # V08 — Security
    "security": "V08-security",
    # V09/V10/V11 — language test runners
    "go-test": "V09-go-test-runner",
    "go-test-runner": "V09-go-test-runner",
    "ts-test": "V10-ts-test-runner",
    "ts-test-runner": "V10-ts-test-runner",
    "py-test": "V11-py-test-runner",
    "py-test-runner": "V11-py-test-runner",
    # V12 — commit discipline
    "commit-discipline": "V12-commit-discipline",
    "commit": "V12-commit-discipline",
    # V13 — AI cheating guard
    "ai-cheating-guard": "V13-ai-cheating-guard",
    "cheating": "V13-ai-cheating-guard",
    # V14 — complexity
    "complexity-guard": "V14-complexity-guard",
    "complexity": "V14-complexity-guard",
    # V15 — dependency direction
    "dependency-guard": "V15-dependency-guard",
    "deps": "V15-dependency-guard",
    # V16 — linter config
    "linter-config-guard": "V16-linter-config-guard",
    "linter-config": "V16-linter-config-guard",
    "linter": "V16-linter-config-guard",
    # V18 — mock data guard
    "mock-data-guard": "V18-mock-data-guard",
    "mock": "V18-mock-data-guard",
    # V19 — Python quality (ruff lint / format / all)
    "py-quality": "V19-py-quality",
    "py": "V19-py-quality",
    # V20 — Hasura GraphQL enforcement (raw SQL forbidden)
    "hasura-graphql": "V20-hasura-graphql",
    "hasura-gql": "V20-hasura-graphql",
    # V21 — pytest runner (Stop, gated by stop.run_pytest config)
    "pytest": "V21-pytest",
    "py-pytest": "V21-pytest",
    # V22 — multi-environment consistency (APP_ prefix + drift + viper mapping)
    "multi-env": "V22-multi-env",
    "env-consistency": "V22-multi-env",
    # V23 — buf governance (lock drift + breaking + protovalidate)
    "buf-governance": "V23-buf-governance",
    "buf": "V23-buf-governance",
    # V25 — Go multi-binary discipline (graceful shutdown + tools.go + air mapping)
    "go-multibinary": "V25-go-multibinary",
    "multibinary": "V25-go-multibinary",
    # V26 — Docker compose production hardening (resource limits + healthcheck + secret-mount + localhost)
    "docker-prod": "V26-docker-prod",
    "docker-prod-hardening": "V26-docker-prod",
    # V27 — Connect-RPC handler completeness (handler ↔ proto + interceptors + connect.NewError)
    "connect-handler": "V27-connect-handler",
    "connect": "V27-connect-handler",
    # V36 — Go HTTP server hardening (ReadHeaderTimeout/WriteTimeout + graceful shutdown)
    "go-http-hardening": "V36-go-http-hardening",
    "http-hardening": "V36-go-http-hardening",
    # V40 — GitHub Actions SHA pinning (third-party action supply chain)
    "actions-sha-pin": "V40-actions-sha-pin",
    "sha-pin": "V40-actions-sha-pin",
    # V47 — Postgres FK index discipline (FK columns must be indexed)
    "fk-index": "V47-fk-index-discipline",
    "fk-index-discipline": "V47-fk-index-discipline",
    # V50 — health endpoint split (/livez vs /readyz)
    "health-endpoint": "V50-health-endpoint-split",
    "health-split": "V50-health-endpoint-split",
    # V37 — Go test -race + coverage gate
    "go-test-race": "V37-go-test-race-coverage",
    "race-coverage": "V37-go-test-race-coverage",
    # V41 — Actions workflow permissions: block
    "actions-permissions": "V41-actions-permissions-block",
    "permissions-block": "V41-actions-permissions-block",
    # V43 — CI image scanning (Trivy/Grype/Snyk)
    "ci-image-scan": "V43-ci-image-scanning",
    "image-scan": "V43-ci-image-scanning",
    # V42 — Dependabot/Renovate config presence
    "dependabot-config": "V42-dependabot-config",
    "dependabot": "V42-dependabot-config",
    "renovate": "V42-dependabot-config",
    # V49 — OpenTelemetry SDK + wiring
    "otel-instrumentation": "V49-otel-instrumentation",
    "otel": "V49-otel-instrumentation",
    # V34 — Go bare `return err` without %w
    "go-error-wrapping": "V34-go-error-wrapping",
    "error-wrapping": "V34-go-error-wrapping",
    "wrapcheck": "V34-go-error-wrapping",
    # V35 — Go mid-flow context.Background()
    "go-context-propagation": "V35-go-context-propagation",
    "context-propagation": "V35-go-context-propagation",
    # V38 — golangci-lint strictness config
    "golangci-strictness": "V38-golangci-strictness",
    "golangci": "V38-golangci-strictness",
    # V39 — Go context-scoped logger discipline
    "go-ctx-logger": "V39-go-context-scoped-logger",
    "scoped-logger": "V39-go-context-scoped-logger",
    # V44 — Dockerfile FROM digest pin
    "dockerfile-digest": "V44-dockerfile-base-digest-pin",
    "from-digest": "V44-dockerfile-base-digest-pin",
    # V45 — Dockerfile HEALTHCHECK presence
    "dockerfile-healthcheck": "V45-dockerfile-healthcheck",
    "healthcheck": "V45-dockerfile-healthcheck",
    # V46 — Hasura migration enum rollback (ALTER TYPE ADD VALUE)
    "migration-enum-rollback": "V46-migration-enum-rollback",
    "enum-rollback": "V46-migration-enum-rollback",
    # V48 — Hasura select-only permission intent documentation
    "hasura-permission-rationale": "V48-hasura-permission-rationale",
    "select-only": "V48-hasura-permission-rationale",
}


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: run_single.py <validator-name>", file=sys.stderr)
        print(f"Available: {', '.join(sorted(NAME_MAP.keys()))}", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1].lower().strip()
    target_id = NAME_MAP.get(name)

    if not target_id:
        # Try matching by ID directly
        target_id = name

    input_data = read_hook_input()
    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)

    # Find the matching validator
    validator = None
    for v in get_all_validators():
        if v.id == target_id or v.id.lower() == target_id.lower():
            validator = v
            break

    if not validator:
        print(f"Error: validator '{name}' not found", file=sys.stderr)
        print(f"Available: {', '.join(sorted(NAME_MAP.keys()))}", file=sys.stderr)
        sys.exit(1)

    # Run the validator in stop mode (comprehensive)
    all_findings: list[Finding] = []
    try:
        result = validator.run(ctx, file_path=None, mode="stop")
        all_findings.extend(result.findings)
    except Exception as exc:
        # Print user-facing message to stderr AND record for post-mortem.
        print(f"Error running {validator.id}: {exc}", file=sys.stderr)
        log_exception(
            source=f"run_single/{validator.id}",
            error=exc,
            context={"cwd": cwd, "mode": "stop"},
        )

    output = format_output(all_findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
