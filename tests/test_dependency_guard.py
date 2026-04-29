"""Tests for V15: DependencyGuardValidator — Clean Architecture layer enforcement.

Covers:
  - _extract_layer_from_path: layer detection from file paths
  - _extract_go_imports: Go import parsing
  - _extract_python_imports: Python import parsing (AST)
  - _extract_ts_imports: TypeScript import parsing
  - Go layer violation detection
  - Python layer violation detection
  - TypeScript layer violation detection
  - Custom layers from .verifiers/layers.yaml
  - validate: integration tests
  - main(): standalone execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.dependency_guard import (
    DependencyGuardValidator,
    _detect_go_module,
    _extract_go_imports,
    _extract_layer_from_path,
    _extract_python_imports,
    _extract_ts_imports,
    _load_custom_layers,
)
from lib.project_context import ProjectContext


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> DependencyGuardValidator:
    return DependencyGuardValidator()


def _write_file(base: Path, name: str, content: str) -> str:
    fp = base / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    return str(fp)


def _make_go_project(tmp_path: Path) -> Path:
    """Create a Go project with module and layer structure."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "go.mod").write_text("module github.com/test/project\n\ngo 1.21\n")
    (tmp_path / "internal" / "domain").mkdir(parents=True)
    (tmp_path / "internal" / "handler").mkdir(parents=True)
    (tmp_path / "internal" / "service").mkdir(parents=True)
    (tmp_path / "internal" / "repository").mkdir(parents=True)
    return tmp_path


# ============================================================================
# 1. Import extraction
# ============================================================================


class TestExtractGoImports:
    def test_single_import(self) -> None:
        content = 'import "fmt"\n'
        assert _extract_go_imports(content) == ["fmt"]

    def test_block_imports(self) -> None:
        content = 'import (\n\t"fmt"\n\t"os"\n)\n'
        imports = _extract_go_imports(content)
        assert "fmt" in imports
        assert "os" in imports

    def test_mixed_imports(self) -> None:
        content = 'import "fmt"\nimport (\n\t"os"\n\t"path"\n)\n'
        imports = _extract_go_imports(content)
        assert len(imports) == 3

    def test_aliased_import(self) -> None:
        content = 'import (\n\tf "fmt"\n)\n'
        imports = _extract_go_imports(content)
        assert "fmt" in imports


class TestExtractPythonImports:
    def test_import_statement(self) -> None:
        content = "import os\nimport json\n"
        imports = _extract_python_imports(content)
        assert "os" in imports
        assert "json" in imports

    def test_from_import(self) -> None:
        content = "from pathlib import Path\n"
        imports = _extract_python_imports(content)
        assert "pathlib" in imports

    def test_relative_import(self) -> None:
        content = "from .models import User\n"
        imports = _extract_python_imports(content)
        # AST sets node.module = "models" for relative imports
        assert "models" in imports


class TestExtractTsImports:
    def test_default_import(self) -> None:
        content = "import React from 'react';\n"
        imports = _extract_ts_imports(content)
        assert "react" in imports

    def test_named_import(self) -> None:
        content = "import { useState } from 'react';\n"
        imports = _extract_ts_imports(content)
        assert "react" in imports

    def test_relative_import(self) -> None:
        content = "import { handler } from './handler';\n"
        imports = _extract_ts_imports(content)
        assert "./handler" in imports

    def test_require(self) -> None:
        content = "const fs = require('fs');\n"
        imports = _extract_ts_imports(content)
        assert "fs" in imports


# ============================================================================
# 2. Layer extraction
# ============================================================================


class TestExtractLayerFromPath:
    def test_go_domain_layer(self) -> None:
        name, level = _extract_layer_from_path("/project/internal/domain/user.go", "/project", "go", None)
        assert name == "domain"
        assert level == 0

    def test_go_handler_layer(self) -> None:
        name, level = _extract_layer_from_path("/project/internal/handler/user.go", "/project", "go", None)
        assert name == "handler"
        assert level == 3

    def test_ts_components_layer(self) -> None:
        name, level = _extract_layer_from_path("/project/src/components/Button.tsx", "/project", "typescript", None)
        assert name == "components"
        assert level == 3

    def test_python_models_layer(self) -> None:
        name, level = _extract_layer_from_path("/project/app/models/user.py", "/project", "python", None)
        assert name == "models"
        assert level == 0

    def test_unknown_layer(self) -> None:
        name, level = _extract_layer_from_path("/project/random/file.go", "/project", "go", None)
        assert name is None
        assert level is None


# ============================================================================
# 3. Go layer violations
# ============================================================================


class TestGoLayerViolations:
    def test_domain_importing_handler_violation(self, tmp_path: Path) -> None:
        project = _make_go_project(tmp_path)
        content = 'package domain\n\nimport "github.com/test/project/internal/handler"\n\nfunc NewUser() {}\n'
        fp = _write_file(project / "internal" / "domain", "user.go", content)
        ctx = ProjectContext(project)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)

    def test_handler_importing_domain_ok(self, tmp_path: Path) -> None:
        project = _make_go_project(tmp_path)
        content = 'package handler\n\nimport "github.com/test/project/internal/domain"\n\nfunc GetUser() {}\n'
        fp = _write_file(project / "internal" / "handler", "user.go", content)
        ctx = ProjectContext(project)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)

    def test_domain_importing_stdlib_ok(self, tmp_path: Path) -> None:
        project = _make_go_project(tmp_path)
        content = 'package domain\n\nimport "fmt"\n\nfunc NewUser() {}\n'
        fp = _write_file(project / "internal" / "domain", "user.go", content)
        ctx = ProjectContext(project)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)


# ============================================================================
# 4. Python layer violations
# ============================================================================


class TestPythonLayerViolations:
    def test_models_importing_views_violation(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "models").mkdir()
        content = "from views import handler\n"
        fp = _write_file(tmp_path / "models", "user.py", content)
        ctx = ProjectContext(tmp_path)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)

    def test_views_importing_models_ok(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "views").mkdir()
        content = "from models import User\n"
        fp = _write_file(tmp_path / "views", "handler.py", content)
        ctx = ProjectContext(tmp_path)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)


# ============================================================================
# 5. TypeScript layer violations
# ============================================================================


class TestTsLayerViolations:
    def test_utils_importing_pages_violation(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "src" / "utils").mkdir(parents=True)
        (tmp_path / "src" / "pages").mkdir(parents=True)
        content = "import { Home } from '../pages/Home';\n"
        fp = _write_file(tmp_path / "src" / "utils", "helper.ts", content)
        ctx = ProjectContext(tmp_path)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)

    def test_pages_importing_utils_ok(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "src" / "pages").mkdir(parents=True)
        (tmp_path / "src" / "utils").mkdir(parents=True)
        content = "import { format } from '../utils/format';\n"
        fp = _write_file(tmp_path / "src" / "pages", "Home.ts", content)
        ctx = ProjectContext(tmp_path)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)

    def test_npm_package_ignored(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "src" / "utils").mkdir(parents=True)
        content = "import React from 'react';\n"
        fp = _write_file(tmp_path / "src" / "utils", "helper.ts", content)
        ctx = ProjectContext(tmp_path)
        validator = DependencyGuardValidator()
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V15-WRONG-DEPENDENCY" for f in result.findings)


# ============================================================================
# 6. Custom layers
# ============================================================================


class TestCustomLayers:
    def test_load_custom_layers(self, tmp_path: Path) -> None:
        layers_dir = tmp_path / ".verifiers"
        layers_dir.mkdir()
        (layers_dir / "layers.yaml").write_text("go:\n  layers:\n    domain: 0\n    infra: 1\n    api: 2\n")
        result = _load_custom_layers(tmp_path)
        assert result is not None
        assert result["go"]["domain"] == 0
        assert result["go"]["api"] == 2

    def test_no_layers_file_returns_none(self, tmp_path: Path) -> None:
        result = _load_custom_layers(tmp_path)
        assert result is None


# ============================================================================
# 7. Go module detection
# ============================================================================


class TestDetectGoModule:
    def test_detect_from_root(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module github.com/test/project\n")
        assert _detect_go_module(tmp_path) == "github.com/test/project"

    def test_detect_from_server(self, tmp_path: Path) -> None:
        (tmp_path / "server").mkdir()
        (tmp_path / "server" / "go.mod").write_text("module github.com/test/server\n")
        assert _detect_go_module(tmp_path) == "github.com/test/server"

    def test_no_go_mod(self, tmp_path: Path) -> None:
        assert _detect_go_module(tmp_path) == ""


# ============================================================================
# 8. should_run
# ============================================================================


class TestShouldRun:
    def test_go_file(self, validator: DependencyGuardValidator) -> None:
        assert validator.should_run("/project/handler.go") is True

    def test_python_file(self, validator: DependencyGuardValidator) -> None:
        assert validator.should_run("/project/handler.py") is True

    def test_ts_file(self, validator: DependencyGuardValidator) -> None:
        assert validator.should_run("/project/handler.ts") is True

    def test_yaml_excluded(self, validator: DependencyGuardValidator) -> None:
        assert validator.should_run("/project/config.yaml") is False


# ============================================================================
# 9. Standalone main()
# ============================================================================


class TestMain:
    def test_main_with_violation(self, tmp_path: Path) -> None:
        project = _make_go_project(tmp_path)
        content = 'package domain\n\nimport "github.com/test/project/internal/handler"\n\nfunc NewUser() {}\n'
        fp = _write_file(project / "internal" / "domain", "user.go", content)

        input_data = {
            "tool_name": "Write",
            "tool_input": {"file_path": fp},
            "cwd": str(project),
        }
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert "additionalContext" in output
        assert "V15-WRONG-DEPENDENCY" in output["additionalContext"]

    def test_main_non_edit_tool(self) -> None:
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.go"},
            "cwd": ".",
        }
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert output == {}

    def test_main_empty_input(self) -> None:
        stdout = _run_main(None)
        output = json.loads(stdout)
        assert output == {}


# ── Module-level helpers ─────────────────────────────────────────────────────


def _run_main(input_data: dict | None) -> str:
    from hooks.validators.dependency_guard import main

    stdin_data = json.dumps(input_data) if input_data else ""
    captured: list[str] = []

    with mock.patch("sys.stdin", mock.Mock(read=mock.Mock(return_value=stdin_data))):
        with mock.patch(
            "builtins.print",
            side_effect=lambda *args, **kwargs: captured.append(
                " ".join(str(a) for a in args) + kwargs.get("end", "\n"),
            ),
        ):
            main()

    return "".join(captured).strip()
