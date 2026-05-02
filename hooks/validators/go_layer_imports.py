"""V60: Go layer imports — directional architecture enforcement.

Layered architectures (handler → service → repo) collapse silently at
scale because Go's import system has no notion of "this layer can only
import these other layers". One PR that imports a repo straight from a
handler bypasses the service-layer's transaction/auth/validation logic;
subsequent PRs mimic the pattern; layer is gone.

V60 enforces a user-configured layer graph. Each layer is identified by
a path-segment suffix (e.g. ``internal/handlers``). A .go file is
classified into the layer whose suffix matches its path segments. An
``import`` line is classified the same way. If the imported layer is
not in ``allowed_imports[file_layer]`` (and is not the same layer or
external), the import is flagged.

Rules:
  - V60-LAYER-SKIP — file in layer A imports a path in layer B not in
    ``allowed_imports[A]``.

Configuration (``.verifiers/config.yaml``)::

    go:
      layers:
        handlers: "internal/handlers"
        services: "internal/services"
        repos:    "internal/repos"
      allowed_imports:
        handlers: [services]
        services: [repos]
        repos:    []

Empty ``go.layers`` → V60 silently no-ops, so projects without a layered
architecture aren't punished.

Escape hatch: same-line ``// verifier:layer-skip-ok REASON`` comment on
the offending import line.

Reference: [arch-go](https://github.com/arch-go/arch-go) (continuously
developed since 2021), [go-arch-lint](https://github.com/fe3dback/go-arch-lint)
(continuously developed since 2020) — V60 is a verifier-integrated
implementation of the same yaml-driven layer-rule pattern.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Match a single-line import: `import "path"` or `import alias "path"`.
RE_IMPORT_SINGLE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"\s*(?://.*)?$')

# Match the start of a block import: `import (`.
RE_IMPORT_BLOCK_START = re.compile(r'^\s*import\s*\(\s*(?://.*)?$')

# Match a path inside an import block — supports alias / blank / dot prefixes.
RE_IMPORT_BLOCK_PATH = re.compile(r'^\s*(?:(?:\w+|_|\.)\s+)?"([^"]+)"\s*(?://.*)?$')

# Same-line escape hatch.
RE_VERIFIER_OK = re.compile(r"//\s*verifier:layer-skip-ok\b")

_SKIP_FILE_SUFFIX = "_test.go"


def _path_has_segment(path: str, segment: str) -> bool:
    """Return True if ``segment`` appears as consecutive path segments in ``path``.

    Examples::

        _path_has_segment("myapp/internal/handlers/user", "internal/handlers") → True
        _path_has_segment("myapp/internal/handlerstats", "internal/handlers")  → False
        _path_has_segment("internal/handlers", "internal/handlers")            → True
    """
    parts = [p for p in path.split("/") if p]
    seg_parts = [p for p in segment.split("/") if p]
    if not seg_parts:
        return False
    n = len(seg_parts)
    for i in range(len(parts) - n + 1):
        if parts[i : i + n] == seg_parts:
            return True
    return False


def _classify_path(path: str, layers: dict[str, str]) -> str | None:
    """Return the layer name for ``path`` or None if no layer matches."""
    for layer_name, segment in layers.items():
        if _path_has_segment(path, segment):
            return layer_name
    return None


def _parse_imports(src: str) -> list[tuple[str, int]]:
    """Return list of (import_path, line_no) for every import in ``src``."""
    out: list[tuple[str, int]] = []
    in_block = False
    for line_no, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if not in_block:
            m = RE_IMPORT_SINGLE.match(stripped)
            if m:
                out.append((m.group(1), line_no))
                continue
            if RE_IMPORT_BLOCK_START.match(stripped):
                in_block = True
                continue
        else:
            if stripped.startswith(")"):
                in_block = False
                continue
            if not stripped or stripped.startswith("//"):
                continue
            m = RE_IMPORT_BLOCK_PATH.match(stripped)
            if m:
                out.append((m.group(1), line_no))
    return out


class GoLayerImportsValidator(BaseValidator):
    """V60: enforce directional Go layer imports per project config."""

    id = "V60-go-layer-imports"
    name = "Go Layer Imports"
    file_patterns: list[str] = ["**/*.go"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        if not ctx.config.go.layers:
            return []  # No layer config → no-op (zero cost)
        path = Path(file_path)
        if not path.is_file() or file_path.endswith(_SKIP_FILE_SUFFIX):
            return []
        return self._scan_file(path, ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        if not ctx.config.go.layers:
            return []
        if not (ctx.server_dir and ctx.server_dir.exists()):
            return []
        server_resolved = ctx.server_dir.resolve()
        findings: list[Finding] = []
        for go_file in ctx.file_index.find_by_pattern("*.go"):
            try:
                go_file.resolve().relative_to(server_resolved)
            except (ValueError, OSError):
                continue
            if str(go_file).endswith(_SKIP_FILE_SUFFIX):
                continue
            findings.extend(self._scan_file(go_file, ctx))
        return findings

    def _scan_file(self, file_path: Path, ctx: ProjectContext) -> list[Finding]:
        layers = ctx.config.go.layers
        allowed = ctx.config.go.allowed_imports
        # Use a path that contains the layer segments. project_root-relative
        # is most stable (works with any module name in go.mod).
        try:
            rel = file_path.resolve().relative_to(ctx.project_root.resolve())
        except (ValueError, OSError):
            rel = file_path
        rel_str = str(rel).replace("\\", "/")
        file_layer = _classify_path(rel_str, layers)
        if file_layer is None:
            return []  # File doesn't belong to any tracked layer.

        layer_allowed = set(allowed.get(file_layer, []))
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []

        lines = src.splitlines()
        findings: list[Finding] = []
        for import_path, line_no in _parse_imports(src):
            imp_layer = _classify_path(import_path, layers)
            if imp_layer is None or imp_layer == file_layer:
                continue  # External or same-layer imports are unconstrained.
            if imp_layer in layer_allowed:
                continue
            # Escape hatch on the import line.
            line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            if RE_VERIFIER_OK.search(line_text):
                continue
            findings.append(
                Finding(
                    severity="error",
                    file=str(file_path),
                    line=line_no,
                    rule="V60-LAYER-SKIP",
                    message=(
                        f"Layer '{file_layer}' may not import layer '{imp_layer}' "
                        f"(allowed: {sorted(layer_allowed) or 'none'}). "
                        f"Imported path: {import_path}"
                    ),
                    fix=(
                        f"Route through an intermediate layer. If '{file_layer}' "
                        f"genuinely needs '{imp_layer}', either: (1) widen "
                        f"`.verifiers/config.yaml` `go.allowed_imports.{file_layer}` "
                        f"to include '{imp_layer}', or (2) add "
                        f"`// verifier:layer-skip-ok REASON` on this import line."
                    ),
                )
            )
        return findings


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = GoLayerImportsValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
