"""Workflow YAML loader — shared by V37, V40, V41, V42, V43, V57, V58.

Phase60 extracted the duplicated `.github/workflows/*.yml` walker
that 6 of those 7 validators reimplemented identically. The 7th
(V42 dependabot-config) reads a single fixed path and shares only
the safe-parse helper, not the directory walker — so it consumes
``parse_workflow`` but not ``walk_workflows``.

## What was duplicated

Each consumer had this same boilerplate:

```python
workflows_dir = Path(ctx.project_root) / ".github" / "workflows"
if not workflows_dir.is_dir():
    return []
findings: list[Finding] = []
for pattern in ("*.yml", "*.yaml"):
    for wf_file in sorted(workflows_dir.glob(pattern)):
        try:
            data = yaml.safe_load(wf_file.read_text(errors="replace"))
        except (yaml.YAMLError, OSError):
            continue
        if not data or not isinstance(data, dict):
            continue
        # ... per-validator logic ...
```

12-15 lines × 6 consumers = ~80 lines of true duplication. Phase 51
pattern says: extract when 3+ consumers; we now have 7.

## Two shared helpers

``walk_workflows(project_root)`` — generator yielding
``(path, data)`` tuples for every parseable workflow YAML under
``.github/workflows/``. Skips unreadable files, malformed YAML, and
non-dict roots silently — callers see only valid (path, dict) pairs.
No directory or file I/O surfaces an exception out.

``parse_workflow(path)`` — single-file safe-load returning
``dict | None``. Used by V42 (which reads a single dependabot.yml
path, not the workflows directory) and exposed for symmetry.

## Why generator, not list

Several callers (V40 SHA-pin, V43 image-scan) do early-bail on the
first interesting hit — a generator lets them stop walking as soon
as found. List-returning would force every workflow to be loaded
even when the answer is settled after the first.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterator


def walk_workflow_paths(project_root: Path | str) -> "Iterator[Path]":
    """Yield workflow file paths only (no YAML parsing).

    Used by text-scan validators (e.g. V40 actions-sha-pin scans
    line-by-line for `uses:` refs without needing the parsed YAML
    tree). Walks ``<project_root>/.github/workflows/*.{yml,yaml}``
    in sorted order, deduped by resolved path.

    Yields nothing if ``.github/workflows/`` doesn't exist.
    """
    workflows_dir = Path(project_root) / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return

    seen: set[Path] = set()
    for pattern in ("*.yml", "*.yaml"):
        for wf_file in sorted(workflows_dir.glob(pattern)):
            # Case-insensitive filesystems: dedup by resolved path.
            resolved = wf_file.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield wf_file


def walk_workflows(project_root: Path | str) -> "Iterator[tuple[Path, dict]]":
    """Yield ``(file_path, parsed_data)`` for every workflow YAML.

    Walks ``<project_root>/.github/workflows/*.{yml,yaml}`` in sorted
    order. Silently skips:
      - missing ``.github/workflows/`` directory (yields nothing)
      - unreadable files (OSError)
      - malformed YAML (yaml.YAMLError)
      - non-dict roots (e.g. empty file, list-only YAML)

    Callers receive only valid ``(Path, dict)`` pairs and can focus
    on validation logic. Iteration is lazy — early break stops the
    walk before all files are loaded.

    Args:
        project_root: Repository root (string or Path). Resolved
            relative to nothing — the caller has already determined
            the project boundary via ``ProjectContext``.

    Yields:
        ``(workflow_path, workflow_data)`` for each parseable workflow.
    """
    for wf_file in walk_workflow_paths(project_root):
        data = parse_workflow(wf_file)
        if data is None:
            continue
        yield wf_file, data


def parse_workflow(path: Path) -> dict | None:
    """Safe-parse a single workflow YAML.

    Returns the parsed dict, or ``None`` for any failure mode:
      - File doesn't exist or can't be read (OSError)
      - YAML is malformed (yaml.YAMLError)
      - Root is not a dict (empty file, list-only, scalar)

    Used by ``walk_workflows`` internally and exposed for callers
    (e.g. V42 dependabot-config) that read a single fixed path.
    """
    try:
        content = path.read_text(errors="replace")
        data = yaml.safe_load(content)
    except (yaml.YAMLError, OSError):
        return None

    if not isinstance(data, dict):
        return None
    return data
