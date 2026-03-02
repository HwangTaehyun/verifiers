"""V15: Dependency Direction Guard — Clean Architecture layer enforcement.

Robert C. Martin (Uncle Bob): "Source code dependencies can only point inwards.
Nothing in an inner circle can know about anything in an outer circle."

Checks:
  V15-WRONG-DEPENDENCY: Import violates layer direction rules
  V15-CIRCULAR-IMPORT: Circular dependency between packages/modules
  V15-LAYER-SKIP: Dependency skips intermediate layer (warning only)
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext

# ── Default layer definitions ───────────────────────────────────────────────
# Lower number = inner layer (more stable, less dependent)
# Higher number = outer layer (more volatile, more dependent)
# Inner layers MUST NOT import from outer layers.

DEFAULT_GO_LAYERS: dict[str, int] = {
    "domain": 0,
    "model": 0,
    "models": 0,
    "entity": 0,
    "entities": 0,
    "repository": 1,
    "repo": 1,
    "service": 2,
    "usecase": 2,
    "handler": 3,
    "controller": 3,
    "api": 3,
    "middleware": 3,
    "cmd": 4,
    "main": 4,
}

DEFAULT_TS_LAYERS: dict[str, int] = {
    "types": 0,
    "interfaces": 0,
    "constants": 0,
    "utils": 1,
    "lib": 1,
    "helpers": 1,
    "hooks": 2,
    "services": 2,
    "store": 2,
    "components": 3,
    "features": 3,
    "pages": 4,
    "views": 4,
    "app": 5,
}

DEFAULT_PYTHON_LAYERS: dict[str, int] = {
    "models": 0,
    "domain": 0,
    "schemas": 0,
    "repositories": 1,
    "services": 2,
    "views": 3,
    "handlers": 3,
    "api": 3,
    "routes": 3,
    "cli": 4,
    "main": 4,
}


def _load_custom_layers(project_root: Path) -> dict[str, dict[str, int]] | None:
    """Load custom layer definitions from .verifiers/layers.yaml if present."""
    layers_file = project_root / ".verifiers" / "layers.yaml"
    if not layers_file.exists():
        return None

    if yaml is None:
        return None

    try:
        with layers_file.open() as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            return None

        result: dict[str, dict[str, int]] = {}
        for lang_key in ("go", "typescript", "python"):
            if lang_key in data and isinstance(data[lang_key], dict):
                layers = data[lang_key].get("layers", {})
                if isinstance(layers, dict):
                    result[lang_key] = {str(k): int(v) for k, v in layers.items()}

        return result if result else None
    except (OSError, yaml.YAMLError, ValueError):
        return None


def _get_layer(
    segment: str,
    lang: str,
    custom_layers: dict[str, dict[str, int]] | None,
) -> int | None:
    """Get the layer number for a path segment in the given language."""
    segment_lower = segment.lower()

    # Try custom layers first
    if custom_layers:
        lang_layers = custom_layers.get(lang, {})
        if segment_lower in lang_layers:
            return lang_layers[segment_lower]

    # Fall back to defaults
    defaults: dict[str, int] = {}
    if lang == "go":
        defaults = DEFAULT_GO_LAYERS
    elif lang == "typescript":
        defaults = DEFAULT_TS_LAYERS
    elif lang == "python":
        defaults = DEFAULT_PYTHON_LAYERS

    return defaults.get(segment_lower)


def _extract_layer_from_path(
    file_path: str,
    project_root: str,
    lang: str,
    custom_layers: dict[str, dict[str, int]] | None,
) -> tuple[str | None, int | None]:
    """Extract the layer name and number from a file path.

    Returns (layer_name, layer_number) or (None, None) if no layer detected.
    """
    rel = Path(file_path).resolve()
    root = Path(project_root).resolve()

    try:
        rel_path = rel.relative_to(root)
    except ValueError:
        return None, None

    # Walk path segments to find a matching layer
    for part in rel_path.parts:
        layer = _get_layer(part, lang, custom_layers)
        if layer is not None:
            return part, layer

    return None, None


# ── Go import analysis ──────────────────────────────────────────────────────

GO_IMPORT_SINGLE_RE = re.compile(r'^import\s+"([^"]+)"', re.MULTILINE)
GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
GO_IMPORT_LINE_RE = re.compile(r'"([^"]+)"')


def _extract_go_imports(content: str) -> list[str]:
    """Extract import paths from Go source code."""
    imports: list[str] = []

    # Block imports
    for block_match in GO_IMPORT_BLOCK_RE.finditer(content):
        block = block_match.group(1)
        for line_match in GO_IMPORT_LINE_RE.finditer(block):
            imports.append(line_match.group(1))

    # Single imports
    for match in GO_IMPORT_SINGLE_RE.finditer(content):
        imports.append(match.group(1))

    return imports


def _go_import_to_layer(
    import_path: str,
    project_module: str,
    lang: str,
    custom_layers: dict[str, dict[str, int]] | None,
) -> tuple[str | None, int | None]:
    """Map a Go import path to a layer.

    Only analyzes internal imports (same project module).
    """
    if not project_module or not import_path.startswith(project_module):
        return None, None

    # Get the part after the module path
    rel = import_path[len(project_module) :].strip("/")
    for segment in rel.split("/"):
        layer = _get_layer(segment, lang, custom_layers)
        if layer is not None:
            return segment, layer

    return None, None


def _detect_go_module(project_root: Path) -> str:
    """Detect Go module path from go.mod."""
    go_mod = project_root / "go.mod"
    if not go_mod.exists():
        # Try server/go.mod
        go_mod = project_root / "server" / "go.mod"
    if not go_mod.exists():
        return ""

    try:
        content = go_mod.read_text()
        match = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return ""


# ── Python import analysis ──────────────────────────────────────────────────


def _extract_python_imports(content: str) -> list[str]:
    """Extract import module names from Python source code using AST."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    return imports


# ── TypeScript import analysis ──────────────────────────────────────────────

TS_IMPORT_RE = re.compile(
    r"""import\s+(?:"""
    r"""(?:[\w{},\s*]+)\s+from\s+"""
    r"""|)"""
    r"""['"]([^'"]+)['"]""",
    re.MULTILINE,
)

TS_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def _extract_ts_imports(content: str) -> list[str]:
    """Extract import paths from TypeScript/JavaScript source code."""
    imports: list[str] = []

    for match in TS_IMPORT_RE.finditer(content):
        imports.append(match.group(1))

    for match in TS_REQUIRE_RE.finditer(content):
        imports.append(match.group(1))

    return imports


def _ts_import_to_layer(
    import_path: str,
    file_path: str,
    lang: str,
    custom_layers: dict[str, dict[str, int]] | None,
) -> tuple[str | None, int | None]:
    """Map a TS import path to a layer.

    Only analyzes relative imports (./  ../) — npm packages are ignored.
    """
    if not import_path.startswith("."):
        return None, None

    # Resolve relative to file directory
    file_dir = Path(file_path).parent
    resolved = (file_dir / import_path).resolve()

    for part in resolved.parts:
        layer = _get_layer(part, lang, custom_layers)
        if layer is not None:
            return part, layer

    return None, None


# ── Main validator class ───────────────────────────────────────────────────


class DependencyGuardValidator(BaseValidator):
    """V15: Dependency Direction Guard — Clean Architecture layer enforcement."""

    id = "V15-dependency-guard"
    name = "Dependency Direction Guard"
    file_patterns: list[str] = ["**/*.go", "**/*.py", "**/*.ts", "**/*.tsx"]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []
        custom_layers = _load_custom_layers(ctx.project_root)

        if file_path:
            findings.extend(self._check_file(file_path, ctx, custom_layers))
        elif mode == "stop":
            findings.extend(self._check_project(ctx, custom_layers))

        return ValidationResult(validator_id=self.id, findings=findings)

    def _check_file(
        self,
        file_path: str,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Check a single file for dependency direction violations."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        if file_path.endswith(".go"):
            return self._check_go_file(file_path, content, ctx, custom_layers)
        elif file_path.endswith(".py"):
            return self._check_python_file(file_path, content, ctx, custom_layers)
        elif file_path.endswith((".ts", ".tsx")):
            return self._check_ts_file(file_path, content, ctx, custom_layers)

        return []

    def _check_go_file(
        self,
        file_path: str,
        content: str,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Check Go imports for layer violations."""
        findings: list[Finding] = []
        go_module = _detect_go_module(ctx.project_root)
        if not go_module:
            return findings

        source_layer_name, source_layer = _extract_layer_from_path(
            file_path, str(ctx.project_root), "go", custom_layers
        )
        if source_layer is None:
            return findings

        imports = _extract_go_imports(content)

        for imp in imports:
            target_layer_name, target_layer = _go_import_to_layer(
                imp, go_module, "go", custom_layers
            )
            if target_layer is None:
                continue

            # Inner layer importing outer layer → violation
            if source_layer < target_layer:
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V15-WRONG-DEPENDENCY",
                        message=(
                            f"Layer '{source_layer_name}' (level {source_layer}) imports "
                            f"'{target_layer_name}' (level {target_layer}). "
                            "Inner layers must not depend on outer layers."
                        ),
                        fix=(
                            f"Remove the import of '{imp}' from {file_path}. "
                            f"Use dependency injection or an interface in '{source_layer_name}' "
                            f"that '{target_layer_name}' implements."
                        ),
                    )
                )

        return findings

    def _check_python_file(
        self,
        file_path: str,
        content: str,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Check Python imports for layer violations."""
        findings: list[Finding] = []

        source_layer_name, source_layer = _extract_layer_from_path(
            file_path, str(ctx.project_root), "python", custom_layers
        )
        if source_layer is None:
            return findings

        imports = _extract_python_imports(content)

        for imp in imports:
            # Check each segment of the import path
            for segment in imp.split("."):
                target_layer = _get_layer(segment, "python", custom_layers)
                if target_layer is not None:
                    if source_layer < target_layer:
                        findings.append(
                            Finding(
                                severity="error",
                                file=file_path,
                                rule="V15-WRONG-DEPENDENCY",
                                message=(
                                    f"Layer '{source_layer_name}' (level {source_layer}) imports "
                                    f"'{segment}' (level {target_layer}). "
                                    "Inner layers must not depend on outer layers."
                                ),
                                fix=(
                                    f"Remove the import of '{imp}' from {file_path}. "
                                    f"Use dependency injection or abstract the dependency."
                                ),
                            )
                        )
                    break  # Only check first matching segment

        return findings

    def _check_ts_file(
        self,
        file_path: str,
        content: str,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Check TypeScript imports for layer violations."""
        findings: list[Finding] = []

        source_layer_name, source_layer = _extract_layer_from_path(
            file_path, str(ctx.project_root), "typescript", custom_layers
        )
        if source_layer is None:
            return findings

        imports = _extract_ts_imports(content)

        for imp in imports:
            target_layer_name, target_layer = _ts_import_to_layer(
                imp, file_path, "typescript", custom_layers
            )
            if target_layer is None:
                continue

            if source_layer < target_layer:
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V15-WRONG-DEPENDENCY",
                        message=(
                            f"Layer '{source_layer_name}' (level {source_layer}) imports "
                            f"'{target_layer_name}' (level {target_layer}). "
                            "Inner layers must not depend on outer layers."
                        ),
                        fix=(
                            f"Remove the import of '{imp}' from {file_path}. "
                            f"Define an interface in '{source_layer_name}' instead."
                        ),
                    )
                )

        return findings

    def _check_project(
        self,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Check entire project for dependency violations (Stop mode)."""
        findings: list[Finding] = []
        findings.extend(self._scan_lang_files(ctx.server_dir, ["*.go"], self._check_go_file, ctx, custom_layers))
        findings.extend(self._scan_lang_files(ctx.web_dir, ["*.ts", "*.tsx"], self._check_ts_file, ctx, custom_layers))
        findings.extend(self._scan_lang_files(ctx.project_root, ["*.py"], self._check_python_file, ctx, custom_layers))
        return findings

    def _scan_lang_files(
        self,
        directory: Path | None,
        globs: list[str],
        checker: Callable,
        ctx: ProjectContext,
        custom_layers: dict[str, dict[str, int]] | None,
    ) -> list[Finding]:
        """Scan files in a directory and run a language-specific checker."""
        findings: list[Finding] = []
        if not (directory and directory.exists()):
            return findings
        for glob_pattern in globs:
            for src_file in directory.rglob(glob_pattern):
                fp = str(src_file)
                if self._should_skip(fp):
                    continue
                try:
                    content = src_file.read_text(errors="replace")
                except OSError:
                    continue
                findings.extend(checker(fp, content, ctx, custom_layers))
        return findings

    def _should_skip(self, file_path: str) -> bool:
        """Skip generated/vendor files."""
        skip_patterns = [
            "vendor/",
            "node_modules/",
            ".gen.",
            "generated",
            "gen/",
            "__pycache__",
            ".venv/",
        ]
        return any(p in file_path for p in skip_patterns)


# ── Standalone execution (for skill frontmatter hooks) ───────────────────────


def main() -> None:
    """Run as standalone PostToolUse hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    tool_name = input_data.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        write_hook_output({})
        return

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    cwd = input_data.get("cwd", ".")

    if not file_path:
        write_hook_output({})
        return

    ctx = ProjectContext(cwd)
    validator = DependencyGuardValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")
    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
