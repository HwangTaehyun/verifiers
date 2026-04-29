"""Auto-detect project context from working directory.

Supports image-query and nowclear projects by scanning for:
- git root directory
- server/config/*.yaml patterns to identify project name
- server/, web/, hasura/, proto/ directory locations
- build tool detection (just vs make)
- per-project verifiers config (.verifiers/config.yaml)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lib.config_loader import VerifiersConfig, load_config


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
