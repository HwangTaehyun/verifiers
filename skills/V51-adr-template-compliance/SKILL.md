# V51 — adr-template-compliance

> **Owner**: `hooks/validators/adr_template_compliance.py`
> **Tier**: 2 (PostToolUse) + 3 (Stop)
> **File patterns**: `docs/ADR/*.md`, `docs/adr/*.md`, `docs/architecture/decisions/*.md`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V51-ADR-MISSING-SECTION` | info | An ADR file is missing one of the four required Nygard sections: `## Context`, `## Decision`, `## Consequences`, or a status indicator (`## Status` / `**Status**:` / frontmatter `status:`). |

## Why this verifier exists

Architecture Decision Records (ADRs) are only useful if they follow a consistent structure that lets future readers reconstruct *why* a decision was made, not just *what* was decided. Michael Nygard's canonical format ([published 2011-11-15](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)) defines four required sections: **Status**, **Context**, **Decision**, and **Consequences**.

In practice, ADR directories accumulate half-finished records — a title and a Decision section, but no Context explaining the constraints that made the decision non-obvious, or no Consequences section explaining what changed for the team. A future engineer reading the ADR can't evaluate whether it still applies, whether the original constraints still hold, or what trade-offs were accepted.

V51 flags every ADR file that is missing any of these four sections. The rule is **info severity** — it never blocks a commit — because the absence of a section may reflect a draft in progress. The goal is to surface incomplete ADRs so authors finish them before the draft is forgotten.

## Design rationale

- **Info severity only.** ADRs are documentation, not code. Missing sections are a quality concern, not a correctness bug. Blocking would create friction on legitimate drafts.
- **Lenient status detection.** Status is expressed in multiple conventions across teams: some use `## Status` headers, some use `**Status**: Accepted` bold lines, some use YAML frontmatter (`status: accepted`). V51 accepts all three to avoid false positives on legitimate ADR formats.
- **Skip files by name.** `template.md`, `README.md`, `index.md`, and `0000-*.md` are infrastructure files, not decisions. They are excluded unconditionally.
- **Both Tier 2 and Tier 3 delegate to the same `_check(ctx)`.** A Tier 2 hit (file just edited) and a Tier 3 sweep (all ADRs at stop) run identical logic. There is no per-file short-circuit in Tier 2 — the whole ADR directory is always re-scanned. This is intentional: renaming a section in one file can affect the overall picture.
- **Directory resolution order.** The validator tries four candidate paths in a fixed order: `docs/ADR/`, `docs/adr/`, `docs/architecture/decisions/`, `docs/decisions/`. The first existing directory wins. If none exists, the validator returns no findings (no ADRs to check).

## How it checks

Lives in `hooks/validators/adr_template_compliance.py`.

### `_find_adr_dir(root)` — Directory resolution

```python
_ADR_DIR_CANDIDATES = ("docs/ADR", "docs/adr", "docs/architecture/decisions", "docs/decisions")

def _find_adr_dir(root: Path) -> Path | None:
    for candidate in _ADR_DIR_CANDIDATES:
        d = root / candidate
        if d.is_dir():
            return d
    return None
```

### `_has_section(content, section)` — Section detection

```python
def _has_section(content: str, section: str) -> bool:
    pattern = r"^##\s+" + re.escape(section)
    if section.lower() == "decision":
        pattern = r"^##\s+decisions?"  # accept plural
    return bool(re.search(pattern, content, re.MULTILINE | re.IGNORECASE))
```

### `_has_status(content)` — Lenient status detection

```python
def _has_status(content: str) -> bool:
    # frontmatter status: field
    if re.search(r"^---\s*\n(?:.*\n)*?status\s*:", content, re.MULTILINE | re.IGNORECASE):
        return True
    # ## Status section
    if re.search(r"^##\s+status\b", content.lower(), re.MULTILINE):
        return True
    # **Status**: bold line
    if re.search(r"\*\*status\*\*\s*:", content.lower()):
        return True
    return False
```

### `_check(ctx)` — Main loop

```python
def _check(self, ctx):
    adr_dir = _find_adr_dir(ctx.project_root)
    if adr_dir is None:
        return []

    findings = []
    for adr_file in sorted(adr_dir.glob("*.md")):
        if adr_file.name in _SKIP_NAMES or adr_file.name.startswith("0000-"):
            continue
        content = adr_file.read_text(errors="replace")
        for section in ("Context", "Decision", "Consequences"):
            if not _has_section(content, section):
                findings.append(Finding(...))  # V51-ADR-MISSING-SECTION
        if not _has_status(content):
            findings.append(Finding(...))       # V51-ADR-MISSING-SECTION (Status)
    return findings
```

## Could be more effective

- **Numbered ADR ordering.** ADRs that supersede an earlier decision should reference the superseded record. V51 could check that `Supersedes ADR-NNNN` is present in the Consequences section when the status is `Superseded`.
- **Date field validation.** Most ADR formats include a date. V51 could flag ADRs that are missing a date in frontmatter or title, which would help in long-lived repositories.
- **Link checking.** ADRs frequently reference other ADRs or external documents. V51 could detect broken internal links (e.g., `[ADR-0003](0003-foo.md)` where `0003-foo.md` does not exist).
- **Stub detection.** A section that exists but contains only a placeholder like `TBD` or `TODO` is semantically absent. V51 could warn on stub content in required sections.

## References

- [Documenting Architecture Decisions (Nygard, Cognitect)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) — The original canonical Nygard ADR format defining Status, Context, Decision, Consequences — published 2011-11-15, retrieved 2026-04-30.
- [adr-tools (npryce)](https://github.com/npryce/adr-tools) — CLI tooling for managing ADR files using the Nygard format — continuously developed since 2016-01, retrieved 2026-04-30.
- [Architecture Decision Record repository (joelparkerhenderson)](https://github.com/joelparkerhenderson/architecture-decision-record) — Comprehensive collection of ADR templates and examples — continuously developed since 2017, retrieved 2026-04-30.

## Examples

### ✓ Pass — Full Nygard ADR

```markdown
# ADR-0001: Use PostgreSQL

**Status**: Accepted

## Context

We evaluated SQLite, MySQL, and PostgreSQL for the primary data store.
The project requires JSON column support, full-text search, and LISTEN/NOTIFY.

## Decision

We will use PostgreSQL 15 as the primary relational database.

## Consequences

- Developers and ops staff need PostgreSQL expertise.
- The Docker Compose stack must include a Postgres service.
- Migrations are managed with golang-migrate.
```

Also passes with frontmatter status:

```markdown
---
status: accepted
date: 2024-03-15
deciders: [alice, bob]
---

# ADR-0002: Use Kafka for async messaging

## Context

...

## Decision

...

## Consequences

...
```

### ✗ Fail — Missing `## Consequences`

```markdown
# ADR-0003: Use Redis for caching

## Status

Accepted

## Context

We need a fast in-process cache for session tokens.

## Decision

We will use Redis 7 in a single-node configuration.

<!-- Missing: ## Consequences -->
<!-- → V51-ADR-MISSING-SECTION: ADR `0003-use-redis.md` is missing the `## Consequences` section -->
```
