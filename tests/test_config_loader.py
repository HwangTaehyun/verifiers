"""Tests for lib/config_loader.py — per-project verifiers config (P1-3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.config_loader import (
    ComplexityThresholds,
    VerifiersConfig,
    config_path_for,
    load_config,
)


# ---------------------------------------------------------------------------
# 1. Defaults — missing file
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path)
        assert isinstance(cfg, VerifiersConfig)
        assert cfg.thresholds.complexity == ComplexityThresholds()
        assert cfg.thresholds.commit.large_diff_files == 15
        assert cfg.thresholds.test_runner.repeated_failure_count == 3
        assert cfg.exclude.paths == []
        assert cfg.validators.enabled == []
        assert cfg.validators.disabled == []

    def test_canonical_path(self, tmp_path: Path) -> None:
        assert config_path_for(tmp_path) == tmp_path / ".verifiers" / "config.yaml"


# ---------------------------------------------------------------------------
# 2. Partial overrides
# ---------------------------------------------------------------------------


def _write_config(root: Path, body: str) -> None:
    cfg = root / ".verifiers" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)


class TestPartialOverrides:
    def test_complexity_threshold_override(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "thresholds:\n  complexity:\n    cyclomatic_warn: 25\n    cyclomatic_error: 50\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.thresholds.complexity.cyclomatic_warn == 25
        assert cfg.thresholds.complexity.cyclomatic_error == 50
        # Untouched fields still default
        assert cfg.thresholds.complexity.function_lines_warn == 80

    def test_commit_threshold_override(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "thresholds:\n  commit:\n    large_diff_files: 50\n")
        cfg = load_config(tmp_path)
        assert cfg.thresholds.commit.large_diff_files == 50

    def test_test_runner_override(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "thresholds:\n  test_runner:\n    repeated_failure_count: 5\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.thresholds.test_runner.repeated_failure_count == 5

    def test_exclude_paths(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            'exclude:\n  paths:\n    - "vendor/**"\n    - "**/__generated__/**"\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.exclude.paths == ["vendor/**", "**/__generated__/**"]

    def test_validators_enabled_disabled(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "validators:\n  enabled:\n    - V01\n    - V08\n  disabled:\n    - V04\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.validators.enabled == ["V01", "V08"]
        assert cfg.validators.disabled == ["V04"]

    def test_per_validator_exclude(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "exclude:\n"
            "  per_validator:\n"
            "    V14:\n"
            '      - "legacy/**"\n'
            '      - "scripts/**"\n'
            "    V08-security:\n"
            '      - "test-fixtures/**"\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.exclude.per_validator == {
            "V14": ["legacy/**", "scripts/**"],
            "V08-security": ["test-fixtures/**"],
        }

    def test_per_validator_drops_invalid_entries(self, tmp_path: Path) -> None:
        # Non-string keys ignored, empty pattern lists ignored.
        _write_config(
            tmp_path,
            'exclude:\n  per_validator:\n    V14: ["legacy/**"]\n    V99: []\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.exclude.per_validator == {"V14": ["legacy/**"]}

    def test_security_phi_check_disabled(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "security:\n  phi_check_enabled: false\n")
        cfg = load_config(tmp_path)
        assert cfg.security.phi_check_enabled is False

    def test_security_phi_fields_override(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "security:\n  phi_fields:\n    - patient_id\n    - doctor_name\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.security.phi_fields == ["patient_id", "doctor_name"]

    def test_security_required_gitignore_override(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            'security:\n  required_gitignore:\n    - ".env"\n    - "secrets.json"\n',
        )
        cfg = load_config(tmp_path)
        assert cfg.security.required_gitignore == [".env", "secrets.json"]

    def test_security_phi_check_non_bool_falls_back(self, tmp_path: Path) -> None:
        # YAML parses "yes"/"no" as bool, but plain strings or numbers
        # should be rejected → default (True) preserved.
        _write_config(tmp_path, 'security:\n  phi_check_enabled: "maybe"\n')
        cfg = load_config(tmp_path)
        assert cfg.security.phi_check_enabled is True

    def test_security_defaults_when_section_missing(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "thresholds:\n  commit:\n    large_diff_files: 5\n")
        cfg = load_config(tmp_path)
        # security section absent → all defaults
        assert cfg.security.phi_check_enabled is True
        assert cfg.security.phi_fields == []
        assert cfg.security.required_gitignore == []


# ---------------------------------------------------------------------------
# 3. Robustness — malformed input never crashes
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_empty_file(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "")
        assert load_config(tmp_path) == VerifiersConfig()

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "thresholds:\n  complexity:\n  - this isn't a dict")
        # Should not raise — returns defaults.
        cfg = load_config(tmp_path)
        assert cfg.thresholds.complexity == ComplexityThresholds()

    def test_top_level_list_returns_defaults(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "- 1\n- 2\n")
        assert load_config(tmp_path) == VerifiersConfig()

    @pytest.mark.parametrize(
        "value",
        ["20", "twenty", "true", "[1, 2]"],  # YAML-parsed as str/bool/list — not int
    )
    def test_non_int_threshold_falls_back(self, tmp_path: Path, value: str) -> None:
        _write_config(
            tmp_path,
            f"thresholds:\n  complexity:\n    cyclomatic_warn: {value}\n",
        )
        cfg = load_config(tmp_path)
        # Non-int values are rejected; default preserved.
        if value == "20":
            # YAML "20" is the integer 20 actually — accepts.
            assert cfg.thresholds.complexity.cyclomatic_warn == 20
        else:
            assert cfg.thresholds.complexity.cyclomatic_warn == 10  # default

    def test_non_string_in_list_filtered(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            "exclude:\n  paths:\n    - 'good/**'\n    - 42\n    - 'also-good/'\n",
        )
        cfg = load_config(tmp_path)
        assert cfg.exclude.paths == ["good/**", "also-good/"]

    def test_unreadable_file(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / ".verifiers" / "config.yaml"
        cfg_file.parent.mkdir(parents=True)
        cfg_file.write_text("thresholds: {}\n")
        # Make file unreadable. On systems where chmod doesn't actually
        # restrict access (e.g. running as root), the test still validates
        # that the loader returns *something* — never raises.
        cfg_file.chmod(0o000)
        try:
            cfg = load_config(tmp_path)
            assert isinstance(cfg, VerifiersConfig)
        finally:
            cfg_file.chmod(0o644)
