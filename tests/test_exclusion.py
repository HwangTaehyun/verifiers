"""Tests for lib/exclusion.py — central path exclusion + disabled filter (P1-4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lib.exclusion import (
    filter_disabled_validators,
    filter_enabled_validators,
    is_excluded,
    is_excluded_for_validator,
)


# ---------------------------------------------------------------------------
# 1. is_excluded — gitignore-style globs relative to project_root
# ---------------------------------------------------------------------------


class TestIsExcluded:
    def test_empty_patterns_returns_false(self, tmp_path: Path) -> None:
        # No project config → nothing is excluded.
        assert is_excluded(str(tmp_path / "src" / "x.go"), tmp_path, []) is False

    def test_directory_glob_matches(self, tmp_path: Path) -> None:
        target = tmp_path / "vendor" / "third.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        assert is_excluded(str(target), tmp_path, ["vendor/**"]) is True

    def test_directory_glob_no_match(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "main.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        assert is_excluded(str(target), tmp_path, ["vendor/**"]) is False

    def test_double_star_anywhere(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "deep" / "__generated__" / "foo.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        assert is_excluded(str(target), tmp_path, ["**/__generated__/**"]) is True

    def test_multiple_patterns_any_match(self, tmp_path: Path) -> None:
        target = tmp_path / "node_modules" / "lib.ts"
        target.parent.mkdir(parents=True)
        target.write_text("")
        assert is_excluded(str(target), tmp_path, ["vendor/**", "node_modules/**"]) is True

    def test_outside_project_root_falls_back_to_raw_path(self, tmp_path: Path) -> None:
        # Path outside the project — relativize fails, raw fnmatch is used.
        # The pattern won't generally match an absolute path, so result is False.
        outside = "/tmp/some/other/project/main.go"
        assert is_excluded(outside, tmp_path, ["src/**"]) is False


# ---------------------------------------------------------------------------
# 2. filter_disabled_validators — V-ID prefix and full-id matching
# ---------------------------------------------------------------------------


@dataclass
class _FakeValidator:
    id: str


class TestFilterDisabledValidators:
    @pytest.fixture
    def validators(self) -> list[_FakeValidator]:
        return [
            _FakeValidator(id="V01-env-config"),
            _FakeValidator(id="V08-security"),
            _FakeValidator(id="V14-complexity-guard"),
            _FakeValidator(id="V20-hasura-graphql"),
        ]

    def test_empty_disabled_returns_all(self, validators: list[_FakeValidator]) -> None:
        assert filter_disabled_validators(validators, []) == validators

    def test_v_id_prefix_disables(self, validators: list[_FakeValidator]) -> None:
        out = filter_disabled_validators(validators, ["V14"])
        assert [v.id for v in out] == ["V01-env-config", "V08-security", "V20-hasura-graphql"]

    def test_full_id_disables(self, validators: list[_FakeValidator]) -> None:
        out = filter_disabled_validators(validators, ["V14-complexity-guard"])
        assert "V14-complexity-guard" not in [v.id for v in out]

    def test_multiple_disables(self, validators: list[_FakeValidator]) -> None:
        out = filter_disabled_validators(validators, ["V01", "V20"])
        assert [v.id for v in out] == ["V08-security", "V14-complexity-guard"]

    def test_unknown_disabled_id_is_silent_noop(self, validators: list[_FakeValidator]) -> None:
        # User typo or removed validator — don't crash, just don't filter.
        out = filter_disabled_validators(validators, ["V99-not-real"])
        assert out == validators

    def test_returns_new_list(self, validators: list[_FakeValidator]) -> None:
        # Ensure caller can mutate the result without affecting the registry.
        out = filter_disabled_validators(validators, [])
        assert out is not validators
        out.pop()
        assert len(validators) == 4


# ---------------------------------------------------------------------------
# 3. is_excluded_for_validator — per-validator file ignores (Phase15)
# ---------------------------------------------------------------------------


class TestIsExcludedForValidator:
    def test_empty_map_returns_false(self, tmp_path: Path) -> None:
        # No per-validator config → never excluded.
        assert is_excluded_for_validator(str(tmp_path / "src/main.go"), tmp_path, {}, "V14-complexity-guard") is False

    def test_full_id_match(self, tmp_path: Path) -> None:
        target = tmp_path / "legacy" / "old.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        per_v = {"V14-complexity-guard": ["legacy/**"]}
        assert is_excluded_for_validator(str(target), tmp_path, per_v, "V14-complexity-guard") is True

    def test_v_id_prefix_match(self, tmp_path: Path) -> None:
        # Users can write the shorter prefix in YAML.
        target = tmp_path / "legacy" / "old.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        per_v = {"V14": ["legacy/**"]}
        assert is_excluded_for_validator(str(target), tmp_path, per_v, "V14-complexity-guard") is True

    def test_other_validator_not_affected(self, tmp_path: Path) -> None:
        target = tmp_path / "legacy" / "old.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        per_v = {"V14": ["legacy/**"]}
        # V08 isn't mentioned in per_v — must still see the file.
        assert is_excluded_for_validator(str(target), tmp_path, per_v, "V08-security") is False

    def test_no_match_returns_false(self, tmp_path: Path) -> None:
        target = tmp_path / "src" / "main.go"
        target.parent.mkdir(parents=True)
        target.write_text("")
        per_v = {"V14": ["legacy/**"]}
        assert is_excluded_for_validator(str(target), tmp_path, per_v, "V14-complexity-guard") is False

    def test_full_id_and_prefix_buckets_merged(self, tmp_path: Path) -> None:
        # The user wrote both the prefix AND the full id with different
        # patterns. Both should apply for V14-complexity-guard.
        prefix_target = tmp_path / "legacy" / "a.go"
        full_target = tmp_path / "scripts" / "b.go"
        prefix_target.parent.mkdir(parents=True)
        full_target.parent.mkdir(parents=True)
        prefix_target.write_text("")
        full_target.write_text("")
        per_v = {
            "V14": ["legacy/**"],
            "V14-complexity-guard": ["scripts/**"],
        }
        assert is_excluded_for_validator(str(prefix_target), tmp_path, per_v, "V14-complexity-guard") is True
        assert is_excluded_for_validator(str(full_target), tmp_path, per_v, "V14-complexity-guard") is True

    def test_outside_project_root_falls_back_to_raw_path(self, tmp_path: Path) -> None:
        per_v = {"V14": ["legacy/**"]}
        # Path outside root — relativize fails, raw fnmatch is used.
        assert is_excluded_for_validator("/tmp/elsewhere/legacy/x.go", tmp_path, per_v, "V14-complexity-guard") is False


# ---------------------------------------------------------------------------
# 4. filter_enabled_validators — allowlist semantics (Phase16 A)
# ---------------------------------------------------------------------------


class TestFilterEnabledValidators:
    @pytest.fixture
    def validators(self) -> list[_FakeValidator]:
        return [
            _FakeValidator(id="V01-env-config"),
            _FakeValidator(id="V08-security"),
            _FakeValidator(id="V14-complexity-guard"),
            _FakeValidator(id="V20-hasura-graphql"),
        ]

    def test_empty_allowlist_keeps_all(self, validators: list[_FakeValidator]) -> None:
        # Empty list = no allowlist filter (matches README defaults).
        assert filter_enabled_validators(validators, []) == validators

    def test_v_id_prefix_keeps_only_listed(self, validators: list[_FakeValidator]) -> None:
        out = filter_enabled_validators(validators, ["V14"])
        assert [v.id for v in out] == ["V14-complexity-guard"]

    def test_full_id_keeps_only_listed(self, validators: list[_FakeValidator]) -> None:
        out = filter_enabled_validators(validators, ["V08-security"])
        assert [v.id for v in out] == ["V08-security"]

    def test_multiple_entries(self, validators: list[_FakeValidator]) -> None:
        out = filter_enabled_validators(validators, ["V01", "V20"])
        assert [v.id for v in out] == ["V01-env-config", "V20-hasura-graphql"]

    def test_unknown_id_silently_filters_to_empty(self, validators: list[_FakeValidator]) -> None:
        # User typo or removed validator — return empty rather than crash.
        out = filter_enabled_validators(validators, ["V99-not-real"])
        assert out == []

    def test_combined_with_disabled_disabled_wins(self, validators: list[_FakeValidator]) -> None:
        # Same precedence rule the router applies: enabled THEN disabled,
        # so disabled subtracts from the allowlist.
        allowed = filter_enabled_validators(validators, ["V01", "V08", "V14"])
        final = filter_disabled_validators(allowed, ["V14"])
        assert [v.id for v in final] == ["V01-env-config", "V08-security"]

    def test_returns_new_list(self, validators: list[_FakeValidator]) -> None:
        out = filter_enabled_validators(validators, [])
        assert out is not validators
