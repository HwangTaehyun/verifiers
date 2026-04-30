"""Tests for Phase52 — group-based validator disable.

The 7-category map in ``docs/VERIFIERS-CATEGORIES.md`` becomes
operational here: users can ``disabled_groups: ["process"]`` to
disable V12 + V13 + V15 + V16 in one line, instead of listing each.

Coverage:
  - BUILTIN_GROUPS resolves to the documented V-IDs (anchor against
    silent drift between code and the categorization doc).
  - Custom ``groups:`` in config takes precedence on key collision.
  - Unknown group names are silently dropped (logged), not an error.
  - Group-disabled and individually-disabled V-IDs union (idempotent
    on duplicates).
  - End-to-end: ``resolve_active_validators`` with a
    ``disabled_groups`` config actually drops the matching validators
    from the active set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lib.config_loader import (
    BUILTIN_GROUPS,
    VerifiersConfig,
    expand_disabled_groups,
    load_config,
)


# ── BUILTIN_GROUPS contract ──────────────────────────────────────────────


class TestBuiltinGroups:
    """Pin the 7-category contract from docs/VERIFIERS-CATEGORIES.md.

    These tests exist primarily to break loudly if someone edits the
    BUILTIN_GROUPS dict without updating the categorization doc (and
    vice versa). The two surfaces must move together.
    """

    def test_seven_categories_present(self) -> None:
        expected = {
            "code-quality",
            "test-execution",
            "env-config",
            "docker",
            "api-rpc-data",
            "security",
            "process",
        }
        assert set(BUILTIN_GROUPS.keys()) == expected

    def test_process_group_membership(self) -> None:
        assert BUILTIN_GROUPS["process"] == ["V12", "V13", "V15", "V16"]

    def test_security_group_membership(self) -> None:
        assert BUILTIN_GROUPS["security"] == ["V08", "V18"]

    def test_no_v_id_appears_in_two_groups(self) -> None:
        # If any V-ID is in two groups, group-disable becomes ambiguous
        # ("disabled_groups: [security, code-quality]" should not
        # double-disable the same validator).
        seen: dict[str, str] = {}
        for group_name, members in BUILTIN_GROUPS.items():
            for member in members:
                assert member not in seen, f"{member} is in both {seen.get(member)} and {group_name} groups"
                seen[member] = group_name


# ── expand_disabled_groups ───────────────────────────────────────────────


class TestExpandDisabledGroups:
    def test_empty_disabled_groups_returns_empty(self) -> None:
        cfg = VerifiersConfig()
        assert expand_disabled_groups(cfg) == []

    def test_builtin_group_expands(self) -> None:
        cfg = VerifiersConfig()
        cfg.validators.disabled_groups = ["process"]
        assert expand_disabled_groups(cfg) == ["V12", "V13", "V15", "V16"]

    def test_two_groups_concatenate_uniquely(self) -> None:
        cfg = VerifiersConfig()
        cfg.validators.disabled_groups = ["process", "security"]
        result = expand_disabled_groups(cfg)
        assert "V12" in result
        assert "V08" in result
        # No duplicates even if the same V-ID hypothetically appeared in both.
        assert len(result) == len(set(result))

    def test_unknown_group_silently_dropped(self) -> None:
        cfg = VerifiersConfig()
        cfg.validators.disabled_groups = ["nonexistent", "process"]
        # The unknown name is dropped; the known one expands.
        result = expand_disabled_groups(cfg)
        assert result == ["V12", "V13", "V15", "V16"]

    def test_user_groups_override_builtin_on_collision(self) -> None:
        cfg = VerifiersConfig()
        cfg.groups = {"process": ["V99"]}  # override BUILTIN_GROUPS["process"]
        cfg.validators.disabled_groups = ["process"]
        # User wins.
        assert expand_disabled_groups(cfg) == ["V99"]

    def test_user_groups_extend_with_new_names(self) -> None:
        cfg = VerifiersConfig()
        cfg.groups = {"my-strict": ["V08", "V18", "V14"]}
        cfg.validators.disabled_groups = ["my-strict"]
        result = expand_disabled_groups(cfg)
        assert result == ["V08", "V18", "V14"]


# ── End-to-end via load_config ───────────────────────────────────────────


def _write_config(tmp_path: Path, content: str) -> Path:
    cfg_dir = tmp_path / ".verifiers"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(content)
    return cfg_file


class TestLoadConfigRoundTrip:
    def test_disabled_groups_loaded_from_yaml(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            yaml.safe_dump(
                {
                    "validators": {
                        "disabled_groups": ["process", "security"],
                        "disabled": ["V07"],
                    },
                }
            ),
        )
        cfg = load_config(tmp_path)
        assert cfg.validators.disabled_groups == ["process", "security"]
        assert cfg.validators.disabled == ["V07"]

    def test_user_groups_loaded_from_yaml(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            yaml.safe_dump(
                {
                    "groups": {
                        "my-team": ["V08", "V14"],
                    },
                    "validators": {"disabled_groups": ["my-team"]},
                }
            ),
        )
        cfg = load_config(tmp_path)
        assert cfg.groups == {"my-team": ["V08", "V14"]}
        assert expand_disabled_groups(cfg) == ["V08", "V14"]

    def test_missing_groups_section_is_default_empty_dict(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            yaml.safe_dump({"validators": {"disabled": ["V07"]}}),
        )
        cfg = load_config(tmp_path)
        assert cfg.groups == {}
        assert cfg.validators.disabled_groups == []


# ── End-to-end via resolve_active_validators ────────────────────────────


class TestResolveActiveValidators:
    """Full integration: a ``disabled_groups`` entry actually removes
    the matching validators from the active set."""

    def test_disabled_groups_filters_active_set(self, tmp_path: Path) -> None:
        from lib.project_context import ProjectContext
        from lib.validator_registry import resolve_active_validators

        _write_config(
            tmp_path,
            yaml.safe_dump({"validators": {"disabled_groups": ["process"]}}),
        )
        # Need a git root marker for ProjectContext.
        (tmp_path / ".git").mkdir()

        ctx = ProjectContext(tmp_path)
        active, error = resolve_active_validators(ctx, source="test")

        assert error is None
        active_ids = {v.id for v in active}
        # Process group: V12, V13, V15, V16 — none should be active.
        for prefix in ("V12-", "V13-", "V15-", "V16-"):
            assert not any(vid.startswith(prefix) for vid in active_ids), (
                f"{prefix} should be disabled by group expansion"
            )
        # Other validators still active (sample check).
        assert any(vid.startswith("V08-") for vid in active_ids)

    def test_disabled_and_disabled_groups_union(self, tmp_path: Path) -> None:
        from lib.project_context import ProjectContext
        from lib.validator_registry import resolve_active_validators

        _write_config(
            tmp_path,
            yaml.safe_dump(
                {
                    "validators": {
                        "disabled_groups": ["security"],
                        "disabled": ["V07"],
                    }
                }
            ),
        )
        (tmp_path / ".git").mkdir()

        ctx = ProjectContext(tmp_path)
        active, error = resolve_active_validators(ctx, source="test")

        assert error is None
        active_ids = {v.id for v in active}
        # Security group: V08, V18.
        assert not any(vid.startswith("V08-") for vid in active_ids)
        assert not any(vid.startswith("V18-") for vid in active_ids)
        # Plus V07 from explicit disabled.
        assert not any(vid.startswith("V07-") for vid in active_ids)
        # V01 still present.
        assert any(vid.startswith("V01-") for vid in active_ids)


# ── Anchor: BUILTIN_GROUPS coverage of the registry ─────────────────────


class TestBuiltinCoverage:
    """Every registered V## must appear in exactly one BUILTIN_GROUPS
    bucket (skipping V17 which is deferred and V24 which was removed in
    Phase46). This is the strongest invariant the categorization can
    offer: nothing falls out of the category map silently."""

    def test_every_active_validator_belongs_to_a_group(self) -> None:
        from hooks.validators import get_all_validators

        active_prefixes = {v.id.split("-", 1)[0] for v in get_all_validators()}
        all_in_groups = {member for members in BUILTIN_GROUPS.values() for member in members}
        unmapped = active_prefixes - all_in_groups
        assert not unmapped, (
            f"Validators with no BUILTIN_GROUPS bucket: {sorted(unmapped)}. "
            f"Add them to docs/VERIFIERS-CATEGORIES.md and lib/config_loader.py BUILTIN_GROUPS."
        )

    def test_no_group_member_is_dead(self) -> None:
        """Inverse: every group member must correspond to a real V-ID
        in the registry. Catches stale references when a validator is
        deleted but the group dict isn't updated."""
        from hooks.validators import get_all_validators

        active_prefixes = {v.id.split("-", 1)[0] for v in get_all_validators()}
        all_in_groups = {member for members in BUILTIN_GROUPS.values() for member in members}
        dead = all_in_groups - active_prefixes
        # V17 (UI verifier deferred) and V24 (Hasura permission cut in Phase46)
        # are intentional gaps. Anything else means BUILTIN_GROUPS is stale.
        unexplained_dead = dead - {"V17", "V24"}
        assert not unexplained_dead, (
            f"Group members with no registered V-ID: {sorted(unexplained_dead)}. "
            f"Either add the validator or remove it from BUILTIN_GROUPS."
        )


# ── Pytest collection marker (V21 self-test compatibility) ──────────────


@pytest.fixture
def _placeholder() -> None:
    """Placeholder fixture; the test file uses no shared fixtures so
    pytest's auto-discovery doesn't need anything; this keeps the
    file consistent with conftest.py expectations on imports."""
    return None
