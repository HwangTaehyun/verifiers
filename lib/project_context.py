"""Auto-detect project context from working directory.

Supports image-query and nowclear projects by scanning for:
- git root directory
- server/config/*.yaml patterns to identify project name
- server/, web/, hasura/, proto/ directory locations
- build tool detection (just vs make)
- per-project verifiers config (.verifiers/config.yaml)
"""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from lib.config_loader import VerifiersConfig, load_config
from lib.exclusion import is_excluded as _is_excluded_glob

if TYPE_CHECKING:
    from lib.file_index import ProjectFileIndex


class ProjectContext:
    """Holds detected project paths and metadata."""

    def __init__(self, cwd: str | Path):
        self.cwd = Path(cwd).resolve()
        self.project_root = self._find_git_root()
        self.project_name = self._detect_project_name()
        self.server_dir = self._find_dir("server")
        self.web_dir = self._find_dir("web")
        self.hasura_dir = self._find_dir("server/hasura") or self._find_dir("hasura")
        self.graph_dir = self._find_dir("server/graph") or self._find_dir("graph")
        self.proto_dir = self._find_dir("server/proto") or self._find_dir("proto")
        self.build_tool = self._detect_build_tool()
        # Per-project verifiers config (P1-3). Always present — load_config
        # returns defaults when .verifiers/config.yaml is missing.
        self.config: VerifiersConfig = load_config(self.project_root)

    def _find_git_root(self) -> Path:
        """Walk up from cwd to find the git root."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        # Fallback: walk up looking for .git
        current = self.cwd
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return self.cwd

    def _detect_project_name(self) -> str:
        """Detect project name from config file patterns or directory name."""
        # Check server/config/*.yaml for project-specific config files
        config_dir = self.project_root / "server" / "config"
        if config_dir.exists():
            for f in config_dir.glob("*.local.yaml"):
                # e.g., nowclear.local.yaml → "nowclear"
                name = f.stem.replace(".local", "")
                if name and name not in (".", "config"):
                    return name
            for f in config_dir.glob("*.docker.yaml"):
                name = f.stem.replace(".docker", "")
                if name and name not in (".", "config"):
                    return name

        # Fallback: check Makefile for PACKAGE variable
        makefile = self.project_root / "server" / "Makefile"
        if makefile.exists():
            for line in makefile.read_text().splitlines():
                if line.startswith("PACKAGE"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip()

        # Fallback: project root directory name
        return self.project_root.name

    def _find_dir(self, relative: str) -> Path | None:
        """Find a directory relative to project root."""
        candidate = self.project_root / relative
        if candidate.is_dir():
            return candidate
        return None

    def _detect_build_tool(self) -> str:
        """Detect whether the project uses 'just' or 'make'."""
        if self.server_dir:
            if (self.server_dir / "justfile").exists():
                return "just"
            if (self.server_dir / "Makefile").exists():
                return "make"
        if (self.project_root / "justfile").exists():
            return "just"
        if (self.project_root / "Makefile").exists():
            return "make"
        return "make"  # default

    def is_excluded(self, file_path: str) -> bool:
        """Return True if ``file_path`` matches the project's exclude.paths config.

        Phase34 (S1 audit): centralizes the gitignore-style glob check
        so every validator's scan loop can short-circuit with one call,
        instead of re-implementing substring exclusions like
        ``"vendor" in path`` (which is exactly the bug ``lib/exclusion``
        was created to abolish).

        Returns False if no patterns are configured. Per-validator
        overrides (``exclude.per_validator``) are NOT applied here —
        they need the validator id, so the router still calls
        ``is_excluded_for_validator`` separately at registration time.
        """
        return _is_excluded_glob(file_path, self.project_root, self.config.exclude.paths)

    @functools.cached_property
    def file_index(self) -> ProjectFileIndex:
        """Phase 65: single-walk project file index, lazily built.

        Built once on first access, memoized for the lifetime of this
        ``ctx``. The Stop hook gets a fresh ``ctx`` per invocation so
        the index automatically refreshes between runs — no stale-cache
        risk across hook calls.

        The walk respects:
          1. ``DEFAULT_PRUNE_NAMES`` in ``lib.file_index`` — built-in
             noise dirs (``.git``, ``node_modules``, ``vendor``, ...)
             pruned at directory level.
          2. ``self.config.exclude.paths`` — user-configured project
             exclusions (e.g. ``server/gen/**``, ``web/build/**``).

        Validators that previously called ``Path.glob("**/...")`` should
        now go through ``ctx.file_index.find_by_pattern(...)`` to share
        the single walk and avoid the GIL+IO contention measured in
        the Phase 65 benchmark.

        Tier 2 router does NOT use this index — it operates on a
        single edited file and the build cost would dominate. The
        index is a Tier 3 (Stop hook) optimization.
        """
        # Local import — avoid module-cycle risk and keep the index
        # code path lazy (tests that don't touch file_index don't pay
        # for the import).
        from lib.file_index import ProjectFileIndex

        return ProjectFileIndex.build(
            self.project_root,
            exclude_globs=tuple(self.config.exclude.paths),
        )

    @property
    def metrics_log_dir(self) -> Path:
        """Directory where per-validator metric logs (JSONL) live for this project.

        Phase33b moved logger output from the verifiers source-tree
        ``logs/`` directory into the project's own
        ``.verifiers/state/metrics/`` namespace so:

          1. Multiple projects using the same verifiers install no
             longer share a single ``logs/`` (no cross-project mixing,
             no race on shared files in CI).
          2. The verifiers install can be read-only — write target now
             lives under the project tree the user already owns.
          3. ``rm -rf .verifiers/state/`` cleans up metrics with the
             project, no orphan accumulation.

        See ``lib/json_logger.py`` for the file format and rotation
        behavior, and ``scripts/validator_metrics.py`` for the read-side
        CLI.
        """
        return self.project_root / ".verifiers" / "state" / "metrics"

    def __repr__(self) -> str:
        return (
            f"ProjectContext(name={self.project_name!r}, root={self.project_root}, "
            f"server={self.server_dir}, web={self.web_dir})"
        )
