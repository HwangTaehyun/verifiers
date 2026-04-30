"""Tests for V52: README Badges Validator.

Covers:
  - both badges present → no findings
  - no CI badge → V52-NO-CI-BADGE
  - no license badge → V52-NO-LICENSE-BADGE
  - no badges at all → both findings
  - shields.io CI URL satisfies CI check
  - shields.io badge/license- satisfies license check
  - no README file → empty findings
  - lowercase readme.md picked up
  - validate_file delegates to full check
  - codecov badge does NOT satisfy CI check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.readme_badges import ReadmeBadgesValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> ReadmeBadgesValidator:
    return ReadmeBadgesValidator()


# ---------------------------------------------------------------------------
# README content helpers
# ---------------------------------------------------------------------------

_CI_BADGE = "[![CI](https://github.com/owner/repo/actions/workflows/ci.yml/badge.svg)](https://github.com/owner/repo/actions/workflows/ci.yml)"
_LICENSE_BADGE = "[![License](https://img.shields.io/github/license/owner/repo.svg)](LICENSE)"

_BOTH_BADGES = f"{_CI_BADGE}\n{_LICENSE_BADGE}\n\n# My Project\n"
_NO_CI = f"{_LICENSE_BADGE}\n\n# My Project\n"
_NO_LICENSE = f"{_CI_BADGE}\n\n# My Project\n"
_NO_BADGES = "# My Project\n\nNo badges here.\n"
_CODECOV_BADGE = (
    "[![codecov](https://codecov.io/gh/owner/repo/branch/main/graph/badge.svg)](https://codecov.io/gh/owner/repo)\n"
    "# My Project\n"
)


# ---------------------------------------------------------------------------
# 1. Both badges present → no findings
# ---------------------------------------------------------------------------


def test_both_badges_present_passes(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    (tmp_project / "README.md").write_text(_BOTH_BADGES)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 2. No CI badge → V52-NO-CI-BADGE only
# ---------------------------------------------------------------------------


def test_no_ci_badge_warns(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    (tmp_project / "README.md").write_text(_NO_CI)

    result = validator.run(project_ctx, mode="stop")

    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" in rules
    assert "V52-NO-LICENSE-BADGE" not in rules
    assert result.findings[0].severity == "info"


# ---------------------------------------------------------------------------
# 3. No license badge → V52-NO-LICENSE-BADGE only
# ---------------------------------------------------------------------------


def test_no_license_badge_warns(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    (tmp_project / "README.md").write_text(_NO_LICENSE)

    result = validator.run(project_ctx, mode="stop")

    rules = [f.rule for f in result.findings]
    assert "V52-NO-LICENSE-BADGE" in rules
    assert "V52-NO-CI-BADGE" not in rules


# ---------------------------------------------------------------------------
# 4. No badges at all → both findings
# ---------------------------------------------------------------------------


def test_no_badges_at_all_emits_both(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    (tmp_project / "README.md").write_text(_NO_BADGES)

    result = validator.run(project_ctx, mode="stop")

    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" in rules
    assert "V52-NO-LICENSE-BADGE" in rules
    assert len(result.findings) == 2


# ---------------------------------------------------------------------------
# 5. shields.io github/actions CI URL satisfies CI check
# ---------------------------------------------------------------------------


def test_shields_io_ci_satisfies(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    content = "![CI](https://img.shields.io/github/actions/workflow/status/owner/repo/ci.yml)\n" + _LICENSE_BADGE
    (tmp_project / "README.md").write_text(content)

    result = validator.run(project_ctx, mode="stop")
    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" not in rules


# ---------------------------------------------------------------------------
# 6. shields.io badge/license- satisfies license check
# ---------------------------------------------------------------------------


def test_shields_io_license_satisfies(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    content = _CI_BADGE + "\n![License](https://img.shields.io/badge/license-MIT-blue.svg)\n"
    (tmp_project / "README.md").write_text(content)

    result = validator.run(project_ctx, mode="stop")
    rules = [f.rule for f in result.findings]
    assert "V52-NO-LICENSE-BADGE" not in rules


# ---------------------------------------------------------------------------
# 7. No README file → empty findings
# ---------------------------------------------------------------------------


def test_no_readme_returns_empty(
    validator: ReadmeBadgesValidator,
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    ctx = ProjectContext(tmp_path)

    result = validator.run(ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 8. Lowercase readme.md picked up
# ---------------------------------------------------------------------------


def test_readme_lowercase_filename_picked_up(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    # Write lowercase readme.md (no README.md)
    (tmp_project / "readme.md").write_text(_NO_BADGES)

    result = validator.run(project_ctx, mode="stop")

    # Should detect missing badges (not return empty)
    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" in rules
    assert "V52-NO-LICENSE-BADGE" in rules


# ---------------------------------------------------------------------------
# 9. validate_file delegates to full check
# ---------------------------------------------------------------------------


def test_validate_file_runs_full_check(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    readme = tmp_project / "README.md"
    readme.write_text(_NO_BADGES)

    result = validator.run(project_ctx, file_path=str(readme), mode="post_tool_use")

    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" in rules
    assert "V52-NO-LICENSE-BADGE" in rules


# ---------------------------------------------------------------------------
# 10. Codecov badge does NOT satisfy CI check (coverage ≠ CI status)
# ---------------------------------------------------------------------------


def test_codecov_badge_does_not_satisfy_ci(
    validator: ReadmeBadgesValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """A codecov coverage badge is not a CI status badge — still flags V52-NO-CI-BADGE."""
    content = _CODECOV_BADGE + _LICENSE_BADGE
    (tmp_project / "README.md").write_text(content)

    result = validator.run(project_ctx, mode="stop")

    rules = [f.rule for f in result.findings]
    assert "V52-NO-CI-BADGE" in rules
    assert "V52-NO-LICENSE-BADGE" not in rules
