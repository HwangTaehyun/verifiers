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
    """Validator activation list.

    ``enabled`` is a strict allowlist (empty = "all enabled" default).
    Non-empty + zero matches raises ``VERIFIERS-CONFIG-EMPTY-ALLOWLIST``.

    ``disabled`` is a denylist by V-ID or V-ID prefix
    (``"V14-complexity-guard"`` or just ``"V14"``).

    ``disabled_groups`` (Phase52) is a denylist by group name. Each name
    must resolve via ``BUILTIN_GROUPS`` (in this module) or
    ``VerifiersConfig.groups`` (user-defined). Group expansion runs
    BEFORE the per-V-ID denylist is applied; the two are unioned, so
    ``disabled_groups: ["process"]`` plus ``disabled: ["V07"]`` disables
    V12/V13/V15/V16 + V07 in one config.
    """

    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    disabled_groups: list[str] = field(default_factory=list)


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
class TimeoutsConfig:
    """Phase62-N2: per-validator timeout overrides for parallel_runner.

    Each entry is a V## prefix → seconds mapping. The default applies
    when a validator's V## prefix is not in ``per_validator``. The
    parallel runner uses ``max(default, max(per_validator.values()))``
    as the global ``as_completed`` safety-net timeout, while individual
    validators may consult ``ctx.config.timeouts.per_validator[V##]``
    to clamp their internal subprocess.run calls.

    Examples in ``.verifiers/config.yaml``:

        timeouts:
          default: 30
          per_validator:
            V19: 5      # ruff is fast — fail fast if it hangs
            V21: 180    # pytest may legitimately need 3 min
            V06: 240    # go-quality stage 2 (lint + test parallel)
    """

    default: int = 30
    per_validator: dict[str, int] = field(default_factory=dict)


@dataclass
class TierCacheConfig:
    """Phase63: Tier 3 (Stop hook) PASS-state cache.

    Caches a validator's PASS state (zero findings) keyed by a hash of
    its file inputs. On the next Stop invocation, if the inputs haven't
    changed AND the entry is fresh, the validator is skipped entirely.
    Cuts the dominant cost of "edit one .ts file → stop → re-run all 49
    validators including the 30s go-quality block".

    ``enabled``: master switch. False disables the cache and forces
        every validator to run on every Stop hook (legacy behavior).

    ``max_age_seconds``: time-to-live for a cached PASS. Even when the
        file-state hash matches, an entry older than this is treated as
        a miss. Caps stale-cache risk for non-determinism the hash
        doesn't catch (e.g. clock skew, NFS mtime weirdness, system
        package upgrades that change tool output without touching
        project files). Default 5 minutes.

    Example::

        tier_cache:
          enabled: true
          max_age_seconds: 300

    Escape hatch: ``VERIFIERS_NO_TIER_CACHE=1`` env var disables the
    mechanism even when ``enabled: true`` in config — useful for one-off
    debugging without editing config.
    """

    enabled: bool = True
    max_age_seconds: int = 300


@dataclass
class StopConfig:
    """Stop-hook (Tier 3) tuning.

    ``run_pytest`` controls whether V21-pytest runs the full pytest suite
    at the end of every Claude Code turn:

      "always" — run unconditionally (legacy V19 behavior pre-Phase28).
      "never"  — skip in Stop; rely on CI to catch regressions.
      "smart"  — run only when this turn's working tree has uncommitted
                 .py / pyproject.toml changes (default). Heuristic uses
                 ``git diff --name-only HEAD``; falls open (runs pytest)
                 if git is not available or the command fails.

    The default "smart" balances feedback-time-to-Claude (don't suppress
    test failures right before the agent says "done") against the per-
    turn 5–8s pytest cost on idle/markdown-only turns.
    """

    run_pytest: str = "smart"  # "always" | "never" | "smart"


@dataclass
class VerifiersConfig:
    thresholds: Thresholds = field(default_factory=Thresholds)
    exclude: ExcludeConfig = field(default_factory=ExcludeConfig)
    validators: ValidatorsConfig = field(default_factory=ValidatorsConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    stop: StopConfig = field(default_factory=StopConfig)
    # Phase62-N2: per-validator timeout overrides.
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    # Phase63: Tier 3 PASS-state cache (file-hash keyed, TTL bounded).
    tier_cache: TierCacheConfig = field(default_factory=TierCacheConfig)
    # Phase52: user-defined validator groups. Keys are group names
    # (lowercase, kebab-case); values are lists of V-IDs or V-ID
    # prefixes. User entries override / add to BUILTIN_GROUPS for
    # ``validators.disabled_groups`` resolution.
    groups: dict[str, list[str]] = field(default_factory=dict)


# ── Built-in validator groups (Phase52) ──────────────────────────────
#
# Mirrors the 7-category map in docs/VERIFIERS-CATEGORIES.md so users
# can ``disabled_groups: ["process"]`` without ever defining the
# group themselves. User-supplied ``groups:`` in config takes
# precedence on key collision (lets a project re-scope a category
# name to its own preference).
#
# Keys are kebab-case to match the doc; values are V-ID prefixes
# (the same form ``filter_disabled_validators`` accepts).

BUILTIN_GROUPS: dict[str, list[str]] = {
    # Phase54-56: V34, V35, V36, V38, V39 added.
    "code-quality": ["V06", "V07", "V14", "V19", "V34", "V35", "V36", "V38", "V39"],
    "test-execution": ["V09", "V10", "V11", "V21", "V37"],
    "env-config": ["V01", "V22"],
    # Phase56-58: V44, V45 + V58 (reproducible build markers).
    "docker": ["V05", "V25", "V26", "V44", "V45", "V58"],
    # Phase54-58: V46, V47, V48, V49, V50, V56.
    "api-rpc-data": ["V02", "V03", "V04", "V20", "V23", "V27", "V46", "V47", "V48", "V49", "V50", "V56"],
    # Phase54-58: V40, V41, V42, V43 + V57 (SBOM CI). V55 cut by user.
    "security": ["V08", "V18", "V40", "V41", "V42", "V43", "V57"],
    # Phase58: V53, V54 + V51 (ADR), V52 (README badges) — all docs/governance.
    "process": ["V12", "V13", "V15", "V16", "V51", "V52", "V53", "V54"],
}


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

    # Phase37 (A6 audit): refuse symlinks. ``yaml.safe_load`` already
    # blocks arbitrary-object construction, so today the worst a
    # ``.verifiers/config.yaml -> /etc/passwd`` symlink can do is read
    # the target into memory and fall back to defaults. But if a future
    # change ever logs the parsed-or-raw content for debugging, that
    # symlink becomes an information-disclosure primitive. Cheaper to
    # block it once than to remember the constraint forever.
    if cfg_file.is_symlink():
        log_exception(
            source="config_loader/load_config",
            error=ValueError("Refusing symlinked .verifiers/config.yaml"),
            context={"file": str(cfg_file), "target": str(cfg_file.resolve())},
        )
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
        # Phase52: group-based disable.
        cfg.validators.disabled_groups = _coerce_str_list(val_raw.get("disabled_groups"))

    # Phase52: user-defined validator groups (top-level ``groups:``).
    groups_raw = raw.get("groups")
    if isinstance(groups_raw, dict):
        groups: dict[str, list[str]] = {}
        for name, members in groups_raw.items():
            if not isinstance(name, str):
                continue
            member_list = _coerce_str_list(members)
            if member_list:
                groups[name] = member_list
        cfg.groups = groups

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

    stop_raw = raw.get("stop")
    if isinstance(stop_raw, dict):
        run_pytest = stop_raw.get("run_pytest")
        if isinstance(run_pytest, str) and run_pytest in ("always", "never", "smart"):
            cfg.stop.run_pytest = run_pytest

    # Phase62-N2: per-validator timeouts.
    timeouts_raw = raw.get("timeouts")
    if isinstance(timeouts_raw, dict):
        default = timeouts_raw.get("default")
        if isinstance(default, int) and default > 0:
            cfg.timeouts.default = default
        per_v = timeouts_raw.get("per_validator")
        if isinstance(per_v, dict):
            cfg.timeouts.per_validator = {str(k): int(v) for k, v in per_v.items() if isinstance(v, int) and v > 0}

    # Phase63: Tier 3 PASS-state cache.
    tier_cache_raw = raw.get("tier_cache")
    if isinstance(tier_cache_raw, dict):
        if "enabled" in tier_cache_raw and isinstance(tier_cache_raw["enabled"], bool):
            cfg.tier_cache.enabled = tier_cache_raw["enabled"]
        max_age = tier_cache_raw.get("max_age_seconds")
        # bool is a subclass of int — reject explicitly.
        if isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0:
            cfg.tier_cache.max_age_seconds = max_age

    return cfg


# ── Group expansion (Phase52) ────────────────────────────────────────


def expand_disabled_groups(cfg: VerifiersConfig) -> list[str]:
    """Resolve ``cfg.validators.disabled_groups`` to a flat V-ID list.

    Resolution order per group name:
      1. User-defined ``cfg.groups`` (lets users override or extend).
      2. ``BUILTIN_GROUPS`` (the 7 categories from VERIFIERS-CATEGORIES.md).
      3. Unknown group → silently dropped (logged for debugging).

    Returns the union of all resolved members. Caller appends to
    ``cfg.validators.disabled`` before passing to
    ``filter_disabled_validators``.

    Empty input returns an empty list — callers are unaffected when
    the user hasn't configured ``disabled_groups``.
    """
    if not cfg.validators.disabled_groups:
        return []

    expanded: list[str] = []
    seen: set[str] = set()

    for group_name in cfg.validators.disabled_groups:
        members = cfg.groups.get(group_name) or BUILTIN_GROUPS.get(group_name)
        if not members:
            log_exception(
                source="config_loader/expand_disabled_groups",
                error=ValueError(f"Unknown group '{group_name}' in validators.disabled_groups"),
                context={"available_builtin": list(BUILTIN_GROUPS.keys()), "user_defined": list(cfg.groups.keys())},
            )
            continue
        for member in members:
            if member not in seen:
                expanded.append(member)
                seen.add(member)

    return expanded
