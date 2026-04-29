"""Tests for lib/exclusion.py — central path exclusion + disabled filter (P1-4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from lib.exclusion import filter_disabled_validators, is_excluded


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
