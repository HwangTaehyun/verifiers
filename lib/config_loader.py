"""Per-project configuration loader for verifiers (P1-3).

Reads ``<project_root>/.verifiers/config.yaml`` if present and yields a
typed ``VerifiersConfig`` dataclass tree. All keys are optional — when a
key is missing, the field falls back to the same hard-coded default the
validators previously embedded inline. This means existing projects keep
working unchanged after this loader is wired in.

Schema (all keys optional)::

    thresholds:
      complexity:
        cyclomatic_warn: 10
        cyclomatic_error: 20
        cognitive_warn: 15
        cognitive_error: 30
        function_lines_warn: 80
        function_lines_error: 150
        nesting_warn: 4
        params_warn: 5
      commit:
        large_diff_files: 15
      test_runner:
        repeated_failure_count: 3

    exclude:
      paths:
        - "vendor/**"
        - "node_modules/**"
        - "**/__generated__/**"

    validators:
      enabled:  []   # empty list = all enabled (default)
      disabled: []   # explicit opt-out by V-ID

Validators read this via ``ProjectContext.config``. Failure to parse the
file is non-fatal: the loader logs through ``lib.json_logger.log_exception``
and returns defaults, so a malformed config never breaks the hook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lib.json_logger import log_exception


# ── Threshold dataclasses ───────────────────────────────────────────────


@dataclass
class ComplexityThresholds:
    cyclomatic_warn: int = 10
    cyclomatic_error: int = 20
    cognitive_warn: int = 15
    cognitive_error: int = 30
    function_lines_warn: int = 80
    function_lines_error: int = 150
    nesting_warn: int = 4
    params_warn: int = 5


@dataclass
class CommitThresholds:
    large_diff_files: int = 15


@dataclass
class TestRunnerThresholds:
    repeated_failure_count: int = 3


@dataclass
class Thresholds:
    complexity: ComplexityThresholds = field(default_factory=ComplexityThresholds)
    commit: CommitThresholds = field(default_factory=CommitThresholds)
    test_runner: TestRunnerThresholds = field(default_factory=TestRunnerThresholds)


@dataclass
class ExcludeConfig:
    """Glob patterns (gitignore-style) relative to project root.

    ``paths`` is the global exclusion list — files matching any pattern
    are skipped before any validator runs.

    ``per_validator`` is a {validator-id-or-prefix: [globs]} map: a file
    matching one of those globs is skipped only for that validator.
    Other validators still see the file. The key may be a full id
    (``"V14-complexity-guard"``) or just the V-ID prefix (``"V14"``);
    both are accepted so users can write the shorter form in YAML.
    """

    paths: list[str] = field(default_factory=list)
    per_validator: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ValidatorsConfig:
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)


@dataclass
class SecurityConfig:
    """V08 (Security validator) overrides.

    The validator's default ``PHI_FIELDS`` / ``REQUIRED_GITIGNORE`` lists
    are tuned for medical-data projects. Non-medical SaaS projects can
    either replace the lists with their own or disable PHI scanning
    entirely.

    Each override list follows "empty = use defaults, non-empty = use
    only these" semantics (no implicit merging) — explicit replacement
    is easier to reason about than additive overrides.
    """

    phi_check_enabled: bool = True
    phi_fields: list[str] = field(default_factory=list)  # empty → validator defaults
    required_gitignore: list[str] = field(default_factory=list)  # empty → validator defaults


@dataclass
class DockerConfig:
    """V05 (Docker / docker-compose validator) overrides.

    The default ``vhost_check_mode`` is ``"production"`` — V05-VHOST-NO-NETWORK
    only fires on compose files classified as production. Local dev compose
    files (``docker-compose.yaml``, ``*override*``) skip the check, fixing
    the false-positive that prior versions produced when a dev setup didn't
    use a reverse proxy.

    BREAKING CHANGE relative to pre-Phase21: the previous default behavior
    was effectively ``"all"`` (every compose file was checked). Set
    ``vhost_check_mode: "all"`` to restore the old strictness.

    Each list follows the "empty = use validator defaults, non-empty =
    use only these" semantics from ``SecurityConfig``.
    """

    # When V05-VHOST-NO-NETWORK fires:
    #   "production" — only on prod-classified compose files (default)
    #   "all"        — every compose file (legacy behavior)
    #   "off"        — never (escape hatch for projects with custom proxies)
    vhost_check_mode: str = "production"

    # Network names accepted as a reverse proxy. Any one of these on a
    # service's ``networks:`` list satisfies the VHOST check.
    reverse_proxy_networks: list[str] = field(default_factory=lambda: ["nginx-proxy"])

    # Compose-file classification. Filename globs (lowercase, full name).
    # Empty → use the validator's built-in built-in patterns.
    production_filename_patterns: list[str] = field(default_factory=list)
    dev_filename_patterns: list[str] = field(default_factory=list)

    # Dockerfile multi-stage classification. Stage names declared via
    # ``FROM ... AS <name>``. Empty → use the validator's built-in defaults.
    production_stage_names: list[str] = field(default_factory=list)
    dev_stage_names: list[str] = field(default_factory=list)


@dataclass
class VerifiersConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)
    exclude: ExcludeConfig = field(default_factory=ExcludeConfig)
    validators: ValidatorsConfig = field(default_factory=ValidatorsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)


# ── Loader ──────────────────────────────────────────────────────────────


_CONFIG_RELATIVE_PATH = Path(".verifiers") / "config.yaml"


def config_path_for(project_root: Path) -> Path:
    """Return the canonical config path under ``project_root``."""
    return project_root / _CONFIG_RELATIVE_PATH


def load_config(project_root: Path) -> VerifiersConfig:
    """Load ``.verifiers/config.yaml`` from ``project_root``.

    Always returns a usable ``VerifiersConfig`` — missing file, malformed
    YAML, or unexpected schema all collapse to defaults (logged for
    debugging via ``log_exception``).
    """
    cfg_file = config_path_for(project_root)
    if not cfg_file.exists():
        return VerifiersConfig()

    try:
        raw = yaml.safe_load(cfg_file.read_text(errors="replace")) or {}
    except (yaml.YAMLError, OSError) as exc:
        log_exception(
            source="config_loader/load_config",
            error=exc,
            context={"file": str(cfg_file)},
        )
        return VerifiersConfig()

    if not isinstance(raw, dict):
        return VerifiersConfig()

    return _build_config(raw)


def _coerce_int(value: Any, fallback: int) -> int:
    """Return ``value`` if it's a non-bool int, else fallback (logging-friendly)."""
    if isinstance(value, bool):
        return fallback  # bool is a subclass of int — reject explicitly
    if isinstance(value, int):
        return value
    return fallback


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _build_config(raw: dict[str, Any]) -> VerifiersConfig:
    cfg = VerifiersConfig()

    th_raw = raw.get("thresholds")
    if isinstance(th_raw, dict):
        comp_raw = th_raw.get("complexity")
        if isinstance(comp_raw, dict):
            comp = cfg.thresholds.complexity
            for field_name in (
                "cyclomatic_warn",
                "cyclomatic_error",
                "cognitive_warn",
                "cognitive_error",
                "function_lines_warn",
                "function_lines_error",
                "nesting_warn",
                "params_warn",
            ):
                if field_name in comp_raw:
                    setattr(
                        comp,
                        field_name,
                        _coerce_int(comp_raw[field_name], getattr(comp, field_name)),
                    )

        commit_raw = th_raw.get("commit")
        if isinstance(commit_raw, dict) and "large_diff_files" in commit_raw:
            cfg.thresholds.commit.large_diff_files = _coerce_int(
                commit_raw["large_diff_files"], cfg.thresholds.commit.large_diff_files
            )

        test_raw = th_raw.get("test_runner")
        if isinstance(test_raw, dict) and "repeated_failure_count" in test_raw:
            cfg.thresholds.test_runner.repeated_failure_count = _coerce_int(
                test_raw["repeated_failure_count"],
                cfg.thresholds.test_runner.repeated_failure_count,
            )

    excl_raw = raw.get("exclude")
    if isinstance(excl_raw, dict):
        cfg.exclude.paths = _coerce_str_list(excl_raw.get("paths"))
        per_validator_raw = excl_raw.get("per_validator")
        if isinstance(per_validator_raw, dict):
            per_v: dict[str, list[str]] = {}
            for key, value in per_validator_raw.items():
                if not isinstance(key, str):
                    continue
                patterns = _coerce_str_list(value)
                if patterns:
                    per_v[key] = patterns
            cfg.exclude.per_validator = per_v

    val_raw = raw.get("validators")
    if isinstance(val_raw, dict):
        cfg.validators.enabled = _coerce_str_list(val_raw.get("enabled"))
        cfg.validators.disabled = _coerce_str_list(val_raw.get("disabled"))

    sec_raw = raw.get("security")
    if isinstance(sec_raw, dict):
        if "phi_check_enabled" in sec_raw and isinstance(sec_raw["phi_check_enabled"], bool):
            cfg.security.phi_check_enabled = sec_raw["phi_check_enabled"]
        cfg.security.phi_fields = _coerce_str_list(sec_raw.get("phi_fields"))
        cfg.security.required_gitignore = _coerce_str_list(sec_raw.get("required_gitignore"))

    docker_raw = raw.get("docker")
    if isinstance(docker_raw, dict):
        mode = docker_raw.get("vhost_check_mode")
        if isinstance(mode, str) and mode in ("production", "all", "off"):
            cfg.docker.vhost_check_mode = mode
        # reverse_proxy_networks: empty list explicitly disables the
        # default ["nginx-proxy"] sentinel so we can't use the
        # "_coerce_str_list returns []" idiom; only override when the
        # YAML key is present at all.
        if "reverse_proxy_networks" in docker_raw:
            cfg.docker.reverse_proxy_networks = _coerce_str_list(docker_raw.get("reverse_proxy_networks"))
        cfg.docker.production_filename_patterns = _coerce_str_list(docker_raw.get("production_filename_patterns"))
        cfg.docker.dev_filename_patterns = _coerce_str_list(docker_raw.get("dev_filename_patterns"))
        cfg.docker.production_stage_names = _coerce_str_list(docker_raw.get("production_stage_names"))
        cfg.docker.dev_stage_names = _coerce_str_list(docker_raw.get("dev_stage_names"))

    return cfg
