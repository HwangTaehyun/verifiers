"""V22: Multi-environment consistency validator.

Enforces three rules over the project's env / config / docker layering:

  1. **APP_ prefix on project-owned env vars.** Server-side variables
     this project defines must start with ``APP_`` (or any user-listed
     allowed prefix). Detects naming inconsistencies that would later
     break Viper's ``automatic env`` binding.

  2. **root vs server `.env.example` drift (asymmetric).** When both
     files exist, every ``APP_*`` declared in the *root* example must
     also exist in the server example. The check is **one-directional**:
     server is the canonical source of truth for ``APP_*`` vars
     (Viper's namespace), while root is for compose-orchestration vars
     (``DOMAIN``, ``*_PORT``, ``AIRFLOW_*``, etc.). A server-only
     ``APP_*`` is normal — server defines, root simply doesn't need to
     mirror it. A root-only ``APP_*`` is a structural mistake — root
     is not authorized to introduce ``APP_*`` keys unilaterally.

  3. **Viper config-key ↔ env-var mapping.** Each YAML key under
     ``server/config/*.yaml`` should have a corresponding env var
     declared in ``server/.env.example`` following Viper's standard
     convention (``database.password`` → ``APP_DATABASE_PASSWORD``).

The validator is **opinionated** about external-tool prefixes — common
non-project prefixes (``AIRFLOW_``, ``POSTGRES_``, ``HASURA_``,
``SF_``, etc.) are exempt by default. Projects can extend the
allow-list via ``.verifiers/config.yaml``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Import resolution mirrors the other validators (see V01 / V08).
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml  # noqa: E402

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# ── Defaults ──────────────────────────────────────────────────────────


# Prefixes that are NOT V22-NON-APP-PREFIX violations even though they
# don't start with APP_. Each is an external tool's standard env var
# convention; forcing them under APP_ would break tool integration.
DEFAULT_ALLOWED_PREFIXES: tuple[str, ...] = (
    "APP_",  # the project itself
    "AIRFLOW_",  # Apache Airflow standard
    "_AIRFLOW_",  # Airflow www-user setup vars
    "POSTGRES_",  # Postgres docker image standard
    "PG_",  # libpq env convention
    "HASURA_",  # Hasura GraphQL Engine standard
    "SF_",  # Salesforce dlt source convention
)

# Bare names (no prefix) that are also exempt — typically domain /
# top-level pointers used by docker-compose itself.
DEFAULT_ALLOWED_BARE: frozenset[str] = frozenset(
    {
        "DOMAIN",
        "API_DOMAIN",
        "STORAGE_DOMAIN",
        "PIPELINES_DLT_PATH",
    }
)

# Single .env.example line shape: KEY=VALUE  (allow trailing comment)
_ENV_LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=")


def _parse_env_keys(env_file: Path) -> set[str]:
    """Return the set of declared variable names from a .env-style file."""
    keys: set[str] = set()
    try:
        for line in env_file.read_text(errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if m := _ENV_LINE.match(stripped):
                keys.add(m.group(1))
    except OSError:
        pass
    return keys


def _flatten_yaml(data: object, prefix: str = "") -> list[str]:
    """Flatten nested dict keys to dotted paths (``database.password`` …)."""
    out: list[str] = []
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_flatten_yaml(value, path))
        else:
            out.append(path)
    return out


def _viper_env_name(yaml_key: str) -> str:
    """Translate a dotted YAML key to Viper's expected env-var name.

    Viper's ``AutomaticEnv`` + ``SetEnvKeyReplacer(".", "_")`` pattern
    binds ``database.password`` → ``APP_DATABASE_PASSWORD`` when the
    project sets ``SetEnvPrefix("APP")``. V22 enforces that mapping.
    """
    return "APP_" + yaml_key.upper().replace(".", "_").replace("-", "_")


# ── Validator ─────────────────────────────────────────────────────────


class MultiEnvConsistencyValidator(BaseValidator):
    """V22: Multi-environment consistency."""

    id = "V22-multi-env"
    name = "Multi-environment Consistency"
    file_patterns: list[str] = [
        "**/.env*",
        "**/config/*.yaml",
        "**/config/*.yml",
        "**/docker-compose*.yaml",
        "**/docker-compose*.yml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: any .env / config / compose edit retriggers the
        whole consistency sweep — there is no useful per-file
        optimization because every check is project-level."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: project-wide consistency sweep."""
        return self._all_checks(ctx)

    # ── Internals ─────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._check_app_prefix(ctx))
        findings.extend(self._check_drift_root_vs_server(ctx))
        findings.extend(self._check_viper_mapping(ctx))
        return findings

    def _allowed_prefixes(self, ctx: ProjectContext) -> tuple[str, ...]:
        # User-extension via .verifiers/config.yaml. We use the raw
        # config dict because there's no first-class field yet.
        extra = getattr(ctx.config, "multi_env_allowed_prefixes", None) or ()
        return tuple(set(DEFAULT_ALLOWED_PREFIXES) | set(extra))

    def _allowed_bare(self, ctx: ProjectContext) -> frozenset[str]:
        extra = getattr(ctx.config, "multi_env_allowed_bare", None) or ()
        return DEFAULT_ALLOWED_BARE | frozenset(extra)

    def _server_env_path(self, ctx: ProjectContext) -> Path | None:
        if ctx.server_dir:
            for name in (".env.example", "env.example"):
                p = ctx.server_dir / name
                if p.is_file():
                    return p
        return None

    def _root_env_path(self, ctx: ProjectContext) -> Path | None:
        for name in (".env.example", "env.example"):
            p = ctx.project_root / name
            if p.is_file():
                return p
        return None

    # ── Rule (a) — APP_ prefix on server env vars ─────────────────────

    def _check_app_prefix(self, ctx: ProjectContext) -> list[Finding]:
        env_file = self._server_env_path(ctx)
        if env_file is None:
            return []

        allowed_prefixes = self._allowed_prefixes(ctx)
        allowed_bare = self._allowed_bare(ctx)
        findings: list[Finding] = []

        for line_no, line in enumerate(env_file.read_text(errors="replace").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _ENV_LINE.match(stripped)
            if not m:
                continue
            key = m.group(1)
            if any(key.startswith(p) for p in allowed_prefixes):
                continue
            if key in allowed_bare:
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(env_file),
                    line=line_no,
                    rule="V22-NON-APP-PREFIX",
                    message=(
                        f"Env var '{key}' has no APP_* prefix and is not in the "
                        "allowed-prefix list. Server-owned variables should use "
                        "APP_* so Viper's AutomaticEnv binding works."
                    ),
                    fix=(
                        f"Rename to APP_{key} in server/.env.example and the "
                        "matching consumer (Go os.Getenv / Viper key). If this "
                        "is an external tool's required env var, add its prefix "
                        "to multi_env.allowed_prefixes in .verifiers/config.yaml."
                    ),
                )
            )
        return findings

    # ── Rule (b) — root vs server .env.example drift ──────────────────

    def _check_drift_root_vs_server(self, ctx: ProjectContext) -> list[Finding]:
        root_env = self._root_env_path(ctx)
        server_env = self._server_env_path(ctx)
        if root_env is None or server_env is None:
            return []

        root_keys = _parse_env_keys(root_env)
        server_keys = _parse_env_keys(server_env)

        # Only compare APP_* vars — the other prefixes legitimately
        # appear on one side and not the other (e.g. AIRFLOW_* only
        # makes sense on the server).
        root_app = {k for k in root_keys if k.startswith("APP_")}
        server_app = {k for k in server_keys if k.startswith("APP_")}

        findings: list[Finding] = []
        # Asymmetric check: only flag root-only APP_* vars (= root
        # introducing APP_* unilaterally, which is a structural
        # mistake because server owns the APP_* namespace).
        #
        # Server-only APP_* vars are legitimate and intentionally
        # NOT flagged — server is the canonical source for APP_*
        # and there's no requirement that root mirror it. Root
        # exists for compose-orchestration vars (DOMAIN, *_PORT,
        # AIRFLOW_*) which legitimately diverge from the server
        # example.
        for missing in sorted(root_app - server_app):
            findings.append(
                Finding(
                    severity="warning",
                    file=str(server_env),
                    rule="V22-ROOT-SERVER-DRIFT",
                    message=(
                        f"APP_ var '{missing}' is in root/.env.example but "
                        "missing from server/.env.example (the canonical source)."
                    ),
                    fix=f"Add a definition for {missing} to {server_env}.",
                )
            )
        return findings

    # ── Rule (c) — Viper config-key ↔ env-var mapping ────────────────

    def _check_viper_mapping(self, ctx: ProjectContext) -> list[Finding]:
        env_file = self._server_env_path(ctx)
        if env_file is None or not ctx.server_dir:
            return []
        config_dir = ctx.server_dir / "config"
        if not config_dir.is_dir():
            return []

        env_keys = _parse_env_keys(env_file)

        # We only check the canonical (non-variant) config files so a
        # local-override key not present in production isn't flagged.
        # Heuristic: pick the file whose stem has no suffix part (e.g.,
        # ax-finance.yaml, not ax-finance.local.yaml).
        canonical_files: list[Path] = []
        for path in sorted(config_dir.glob("*.yaml")) + sorted(config_dir.glob("*.yml")):
            parts = path.stem.split(".")
            if len(parts) == 1:
                canonical_files.append(path)
        if not canonical_files:
            return []

        findings: list[Finding] = []
        for cfg_file in canonical_files:
            try:
                data = yaml.safe_load(cfg_file.read_text(errors="replace"))
            except (yaml.YAMLError, OSError):
                continue
            for yaml_key in _flatten_yaml(data):
                expected = _viper_env_name(yaml_key)
                if expected in env_keys:
                    continue
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(env_file),
                        rule="V22-VIPER-KEY-NO-ENV",
                        message=(
                            f"Config key '{yaml_key}' in {cfg_file.name} expects env "
                            f"var '{expected}' (Viper convention) but it's not in "
                            "server/.env.example."
                        ),
                        fix=(
                            f"Add '{expected}=' to server/.env.example, OR confirm "
                            "the key is intentionally hardcoded (no env override). "
                            "If hardcoded, document the choice with a comment."
                        ),
                    )
                )
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = MultiEnvConsistencyValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
