"""V25: Go multi-binary discipline.

For projects with multiple ``cmd/<name>/main.go`` entry points, V25
enforces three production-readiness patterns:

  1. **Graceful shutdown.** Each main.go must register a SIGTERM-aware
     context (``signal.NotifyContext`` or ``signal.Notify`` + manual
     ctx) so a docker / k8s SIGTERM doesn't kill in-flight work.

  2. **`tools.go` with `//go:build tools` tag.** Dev-only deps
     (buf, golangci-lint, mockgen, genqlient) get pinned in go.mod
     via blank-imports under a build tag so production builds skip
     them and ``go mod tidy`` doesn't garbage-collect.

  3. **`.air.<name>.toml` ↔ `cmd/<name>/` mapping.** Air hot-reload
     configs each point at one cmd binary. V25 ensures both sides
     stay in sync — orphaned configs after a rename, or new cmd
     entries with no air config.

V25 fires only when ``cmd/`` is detected (in server/ or root).
Projects with a single binary at root pay zero cost.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Patterns that indicate the main.go *handles* SIGTERM. Either is enough.
_SIGNAL_PATTERNS = (
    re.compile(r"\bsignal\.NotifyContext\s*\("),
    re.compile(r"\bsignal\.Notify\s*\([^)]*SIGTERM"),
)

# Common .air.toml extraction: the binary path and/or the cmd that
# builds it. Match either ``bin = "./tmp/<name>"`` or
# ``cmd = "go build ... ./cmd/<name>"``.
_AIR_BIN_LINE = re.compile(
    r"""^\s*bin\s*=\s*["']\s*\.?/?(?:tmp|bin)/(?P<name>[\w\-]+)\s*["']""",
    re.MULTILINE,
)
_AIR_CMD_LINE = re.compile(
    r"""\.\/cmd\/(?P<name>[\w\-]+)""",
    re.MULTILINE,
)


def _go_root(ctx: ProjectContext) -> Path | None:
    """Locate the directory that owns the Go cmd tree.

    Most repositories have ``server/cmd/`` (the user's monorepo
    layout). A repo where the Go module is at root has ``cmd/``
    directly under project_root. V25 prefers the server-side one.
    """
    candidates: list[Path] = []
    if ctx.server_dir is not None:
        candidates.append(ctx.server_dir)
    candidates.append(ctx.project_root)
    for d in candidates:
        if (d / "cmd").is_dir():
            return d
    return None


def _enumerate_cmd_dirs(go_root: Path) -> list[Path]:
    """Each subdir of cmd/ that has a main.go is a binary entry point."""
    cmd_root = go_root / "cmd"
    if not cmd_root.is_dir():
        return []
    return [d for d in sorted(cmd_root.iterdir()) if d.is_dir() and (d / "main.go").is_file()]


def _enumerate_air_configs(go_root: Path) -> list[Path]:
    """Air toml files at the root of go_root: .air.toml + .air.<name>.toml."""
    out: list[Path] = []
    for path in go_root.iterdir():
        if path.is_file() and path.name.startswith(".air") and path.suffix == ".toml":
            out.append(path)
    return sorted(out)


class GoMultiBinaryValidator(BaseValidator):
    """V25: Go multi-binary discipline."""

    id = "V25-go-multibinary"
    name = "Go Multi-binary Discipline"
    file_patterns: list[str] = [
        "**/cmd/**/main.go",
        "**/tools.go",
        "**/.air*.toml",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: any cmd / tools / air edit retriggers the project sweep."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        return self._all_checks(ctx)

    # ── Internals ────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        go_root = _go_root(ctx)
        if go_root is None:
            return []
        findings: list[Finding] = []
        cmd_dirs = _enumerate_cmd_dirs(go_root)
        if not cmd_dirs:
            return []
        findings.extend(self._check_graceful_shutdown(cmd_dirs))
        findings.extend(self._check_tools_go(go_root))
        findings.extend(self._check_air_mapping(go_root, cmd_dirs))
        return findings

    # ── (a) graceful shutdown ───────────────────────────────────────

    def _check_graceful_shutdown(self, cmd_dirs: list[Path]) -> list[Finding]:
        findings: list[Finding] = []
        for cmd_dir in cmd_dirs:
            main_go = cmd_dir / "main.go"
            try:
                src = main_go.read_text(errors="replace")
            except OSError:
                continue
            if any(p.search(src) for p in _SIGNAL_PATTERNS):
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(main_go),
                    rule="V25-NO-GRACEFUL-SHUTDOWN",
                    message=(
                        f"cmd/{cmd_dir.name}/main.go does not register SIGTERM/SIGINT. "
                        "Container shutdowns will kill in-flight work mid-transaction."
                    ),
                    fix=(
                        "Add `ctx, cancel := signal.NotifyContext(context.Background(), "
                        "syscall.SIGINT, syscall.SIGTERM); defer cancel()` near the top "
                        "of main(), and pass `ctx` to long-running services."
                    ),
                )
            )
        return findings

    # ── (b) tools.go with build tag ─────────────────────────────────

    def _check_tools_go(self, go_root: Path) -> list[Finding]:
        tools_path = go_root / "tools.go"
        findings: list[Finding] = []

        if not tools_path.is_file():
            findings.append(
                Finding(
                    severity="warning",
                    file=str(go_root / "tools.go"),
                    rule="V25-NO-TOOLS-FILE",
                    message=(
                        "Project has multiple cmd binaries but no tools.go. "
                        "Dev tools (buf, golangci-lint, mockgen, ...) end up "
                        "with per-developer versions; reproducible setup breaks."
                    ),
                    fix=(
                        "Create tools.go with the //go:build tools tag and "
                        "blank-import every dev tool. See "
                        "https://github.com/golang/go/wiki/Modules"
                        "#how-can-i-track-tool-dependencies-for-a-module."
                    ),
                )
            )
            return findings

        try:
            src = tools_path.read_text(errors="replace")
        except OSError:
            return findings

        # Build-tag must be on one of the first two lines (Go convention).
        first_lines = src.splitlines()[:3]
        has_new_tag = any("//go:build tools" in line for line in first_lines)
        has_legacy_tag = any("// +build tools" in line for line in first_lines)

        if not (has_new_tag or has_legacy_tag):
            findings.append(
                Finding(
                    severity="warning",
                    file=str(tools_path),
                    rule="V25-TOOLS-NO-BUILD-TAG",
                    message=(
                        "tools.go has no `//go:build tools` build constraint. "
                        "Without the tag, dev tool deps leak into production builds "
                        "and `go mod tidy` may garbage-collect them."
                    ),
                    fix=(
                        "Add `//go:build tools` (and `// +build tools` for Go ≤ 1.16 "
                        "compatibility) to the first two lines of tools.go before the "
                        "package declaration."
                    ),
                )
            )
        return findings

    # ── (c) .air.<name>.toml ↔ cmd/<name>/ mapping ──────────────────

    def _check_air_mapping(self, go_root: Path, cmd_dirs: list[Path]) -> list[Finding]:
        findings: list[Finding] = []
        cmd_names = {d.name for d in cmd_dirs}
        air_configs = _enumerate_air_configs(go_root)
        if not air_configs:
            return []

        air_to_cmd: dict[Path, str | None] = {}
        for air in air_configs:
            try:
                content = air.read_text(errors="replace")
            except OSError:
                continue
            # Try cmd path first (more specific), fall back to bin path.
            cmd_match = _AIR_CMD_LINE.search(content)
            bin_match = _AIR_BIN_LINE.search(content)
            referenced = cmd_match.group("name") if cmd_match else (bin_match.group("name") if bin_match else None)
            air_to_cmd[air] = referenced

        # Flag air configs pointing at non-existent cmd
        for air, ref in air_to_cmd.items():
            if ref is None:
                continue
            if ref not in cmd_names:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(air),
                        rule="V25-AIR-DEAD-PATH",
                        message=(f"Air config {air.name} references cmd/{ref}/ but that directory does not exist."),
                        fix=(
                            f"Either remove {air.name} or update its cmd/bin path to "
                            "match an existing entry under cmd/."
                        ),
                    )
                )

        # Flag cmd dirs that don't have a corresponding air config.
        # (Skip only if the project doesn't use Air at all, i.e. zero air
        # configs — handled above.)
        referenced_cmds = {ref for ref in air_to_cmd.values() if ref}
        for cmd in cmd_dirs:
            if cmd.name in referenced_cmds:
                continue
            # Allow the bare ``.air.toml`` to cover the canonical "server"
            # binary without forcing a `.air.server.toml` rename.
            if cmd.name == "server" and any(a.name == ".air.toml" for a in air_configs):
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(cmd / "main.go"),
                    rule="V25-CMD-NO-AIR-CONFIG",
                    message=(
                        f"cmd/{cmd.name}/ has no matching .air.<name>.toml. "
                        "Hot-reload during development won't work for this binary."
                    ),
                    fix=(
                        f"Create .air.{cmd.name}.toml at the Go root and set "
                        f'cmd = "go build -o ./tmp/{cmd.name} ./cmd/{cmd.name}".'
                    ),
                )
            )
        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoMultiBinaryValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
