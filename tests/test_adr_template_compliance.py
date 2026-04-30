"""Tests for V51: ADR Template Compliance Validator.

Covers:
  - Full Nygard ADR (Status, Context, Decision, Consequences) passes
  - Missing Consequences warns (V51-ADR-MISSING-SECTION)
  - Missing Decision warns
  - Missing Context warns
  - Status in frontmatter satisfies status check
  - Status as **Status**: bold line satisfies status check
  - template.md is skipped
  - README.md is skipped
  - 0000-*.md placeholder is skipped
  - No ADR directory returns empty
  - Alternative directory layout (docs/adr/ lowercase) is picked up
  - validate_file delegates to _check (runs full check)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hooks.validators.adr_template_compliance import AdrTemplateComplianceValidator
from lib.project_context import ProjectContext


@pytest.fixture
def validator() -> AdrTemplateComplianceValidator:
    return AdrTemplateComplianceValidator()


@pytest.fixture
def adr_dir(tmp_project: Path) -> Path:
    """Create docs/ADR/ under the tmp_project and return its path."""
    d = tmp_project / "docs" / "ADR"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_NYGARD_ADR = """\
# ADR-0001: Use PostgreSQL

**Status**: Accepted

## Context

We need a relational database for the project.

## Decision

We will use PostgreSQL 15.

## Consequences

The team must have PostgreSQL expertise.
"""

_MISSING_CONSEQUENCES = """\
# ADR-0002: Use Redis

## Status

Accepted

## Context

We need a cache layer.

## Decision

We will use Redis.
"""

_MISSING_DECISION = """\
# ADR-0003: Use gRPC

## Status

Proposed

## Context

We need inter-service communication.

## Consequences

All services must use generated stubs.
"""

_MISSING_CONTEXT = """\
# ADR-0004: Use Docker

## Status

Accepted

## Decision

We will containerise all services.

## Consequences

Developers need Docker installed.
"""


# ---------------------------------------------------------------------------
# 1. Full Nygard ADR passes
# ---------------------------------------------------------------------------


def test_full_nygard_adr_passes(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """ADR with Status, Context, Decision, Consequences → no findings."""
    (adr_dir / "0001-use-postgresql.md").write_text(_FULL_NYGARD_ADR)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 2. Missing Consequences warns
# ---------------------------------------------------------------------------


def test_missing_consequences_warns(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """ADR missing ## Consequences → 1 V51-ADR-MISSING-SECTION finding."""
    (adr_dir / "0002-use-redis.md").write_text(_MISSING_CONSEQUENCES)

    result = validator.run(project_ctx, mode="stop")
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.rule == "V51-ADR-MISSING-SECTION"
    assert f.severity == "info"
    assert "Consequences" in f.message


# ---------------------------------------------------------------------------
# 3. Missing Decision warns
# ---------------------------------------------------------------------------


def test_missing_decision_warns(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """ADR missing ## Decision → 1 V51-ADR-MISSING-SECTION finding."""
    (adr_dir / "0003-use-grpc.md").write_text(_MISSING_DECISION)

    result = validator.run(project_ctx, mode="stop")
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.rule == "V51-ADR-MISSING-SECTION"
    assert "Decision" in f.message


# ---------------------------------------------------------------------------
# 4. Missing Context warns
# ---------------------------------------------------------------------------


def test_missing_context_warns(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """ADR missing ## Context → 1 V51-ADR-MISSING-SECTION finding."""
    (adr_dir / "0004-use-docker.md").write_text(_MISSING_CONTEXT)

    result = validator.run(project_ctx, mode="stop")
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.rule == "V51-ADR-MISSING-SECTION"
    assert "Context" in f.message


# ---------------------------------------------------------------------------
# 5. Status in frontmatter satisfies status check
# ---------------------------------------------------------------------------


def test_status_in_frontmatter_satisfies(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """frontmatter ``status: accepted`` counts as status — no Status finding."""
    content = """\
---
status: accepted
date: 2024-01-01
---

# ADR-0005: Use Kafka

## Context

We need async messaging.

## Decision

We will use Kafka.

## Consequences

Ops team must manage Kafka clusters.
"""
    (adr_dir / "0005-use-kafka.md").write_text(content)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 6. Status as **Status**: bold line satisfies
# ---------------------------------------------------------------------------


def test_status_in_bold_satisfies(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """``**Status**: Accepted`` bold line counts as status — no Status finding."""
    content = """\
# ADR-0006: Use Elasticsearch

**Status**: Accepted

## Context

We need full-text search.

## Decision

We will use Elasticsearch.

## Consequences

Index mapping must be managed carefully.
"""
    (adr_dir / "0006-use-elasticsearch.md").write_text(content)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 7. template.md is skipped
# ---------------------------------------------------------------------------


def test_template_md_skipped(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """template.md in ADR dir is never flagged regardless of content."""
    (adr_dir / "template.md").write_text("# Template\n\nNo sections here.\n")

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 8. README.md is skipped
# ---------------------------------------------------------------------------


def test_readme_md_skipped(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """README.md in ADR dir is never flagged."""
    (adr_dir / "README.md").write_text("# ADR Index\n\nList of decisions.\n")

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 9. 0000-*.md placeholder is skipped
# ---------------------------------------------------------------------------


def test_zero_zero_zero_zero_skipped(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """0000-*.md numbered placeholder is never flagged."""
    (adr_dir / "0000-record-architecture-decisions.md").write_text(
        "# 0000: Record Architecture Decisions\n\nPlaceholder.\n"
    )

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 10. No ADR directory returns empty
# ---------------------------------------------------------------------------


def test_no_adr_dir_returns_empty(
    validator: AdrTemplateComplianceValidator,
    tmp_path: Path,
) -> None:
    """Project with no ADR directory returns no findings."""
    (tmp_path / ".git").mkdir()
    ctx = ProjectContext(tmp_path)

    result = validator.run(ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 11. Alternative directory layout (docs/adr/ lowercase)
# ---------------------------------------------------------------------------


def test_alternative_dir_layouts(
    validator: AdrTemplateComplianceValidator,
    tmp_project: Path,
    project_ctx: ProjectContext,
) -> None:
    """docs/adr/ (lowercase) is also picked up."""
    adr_dir = tmp_project / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgresql.md").write_text(_FULL_NYGARD_ADR)

    result = validator.run(project_ctx, mode="stop")
    assert result.findings == []


# ---------------------------------------------------------------------------
# 12. validate_file delegates to _check (runs full check)
# ---------------------------------------------------------------------------


def test_validate_file_runs_full_check(
    validator: AdrTemplateComplianceValidator,
    adr_dir: Path,
    project_ctx: ProjectContext,
) -> None:
    """validate_file (Tier 2) runs the same full ADR check as validate_project."""
    adr_file = adr_dir / "0001-incomplete.md"
    adr_file.write_text(_MISSING_CONSEQUENCES)

    result = validator.run(project_ctx, file_path=str(adr_file), mode="post_tool_use")

    assert len(result.findings) == 1
    assert result.findings[0].rule == "V51-ADR-MISSING-SECTION"
    assert "Consequences" in result.findings[0].message
