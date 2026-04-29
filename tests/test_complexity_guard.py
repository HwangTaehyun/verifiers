"""Tests for V14: ComplexityGuardValidator — cyclomatic complexity, length, nesting, params.

Covers:
  - Python analysis: complexity, length, nesting, params via AST
  - Go analysis: complexity, length, nesting, params via regex
  - TypeScript analysis: complexity, length, nesting, params via regex
  - Thresholds: warning vs error severity levels
  - validate: integration tests for PostToolUse and Stop modes
  - main(): standalone execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.complexity_guard import (
    ComplexityGuardValidator,
    _analyze_go_file,
    _analyze_python_file,
    _analyze_ts_file,
    _python_cognitive_complexity,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> ComplexityGuardValidator:
    return ComplexityGuardValidator()


def _write_file(base: Path, name: str, content: str) -> str:
    fp = base / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    return str(fp)


# ============================================================================
# 1. Python analysis
# ============================================================================


class TestPythonComplexity:
    def test_simple_function_no_finding(self, tmp_path: Path) -> None:
        content = "def add(a, b):\n    return a + b\n"
        fp = _write_file(tmp_path, "math.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-HIGH-COMPLEXITY" for f in findings)

    def test_high_complexity_warning(self, tmp_path: Path) -> None:
        # Create function with complexity > 10
        branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
        content = f"def complex_func(x):\n{branches}\n    return 0\n"
        fp = _write_file(tmp_path, "complex.py", content)
        findings = _analyze_python_file(fp)
        complex_findings = [f for f in findings if f.rule == "V14-HIGH-COMPLEXITY"]
        assert len(complex_findings) >= 1
        assert complex_findings[0].severity == "warning"

    def test_very_high_complexity_error(self, tmp_path: Path) -> None:
        # Create function with complexity > 20
        branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(22))
        content = f"def very_complex(x):\n{branches}\n    return 0\n"
        fp = _write_file(tmp_path, "very_complex.py", content)
        findings = _analyze_python_file(fp)
        complex_findings = [f for f in findings if f.rule == "V14-HIGH-COMPLEXITY"]
        assert len(complex_findings) >= 1
        assert complex_findings[0].severity == "error"


class TestPythonLength:
    def test_short_function_no_finding(self, tmp_path: Path) -> None:
        content = "def short():\n    return 1\n"
        fp = _write_file(tmp_path, "short.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-LONG-FUNCTION" for f in findings)

    def test_long_function_warning(self, tmp_path: Path) -> None:
        # LENGTH_WARN=80 / LENGTH_ERROR=150 — generate 90 body lines to trip warn but not error.
        lines = "\n".join(f"    x = {i}" for i in range(90))
        content = f"def long_func():\n{lines}\n    return x\n"
        fp = _write_file(tmp_path, "long.py", content)
        findings = _analyze_python_file(fp)
        long_findings = [f for f in findings if f.rule == "V14-LONG-FUNCTION"]
        assert len(long_findings) >= 1
        assert long_findings[0].severity == "warning"

    def test_very_long_function_error(self, tmp_path: Path) -> None:
        # LENGTH_ERROR=150 — generate 160 body lines to trip error tier.
        lines = "\n".join(f"    x = {i}" for i in range(160))
        content = f"def very_long():\n{lines}\n    return x\n"
        fp = _write_file(tmp_path, "very_long.py", content)
        findings = _analyze_python_file(fp)
        long_findings = [f for f in findings if f.rule == "V14-LONG-FUNCTION"]
        assert len(long_findings) >= 1
        assert long_findings[0].severity == "error"


class TestPythonNesting:
    def test_shallow_nesting_no_finding(self, tmp_path: Path) -> None:
        content = "def func():\n    if True:\n        for x in range(10):\n            pass\n"
        fp = _write_file(tmp_path, "shallow.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-DEEP-NESTING" for f in findings)

    def test_deep_nesting_warning(self, tmp_path: Path) -> None:
        # 6 levels of nesting → depth 5 after subtracting function body → > 4 threshold
        content = (
            "def deep():\n"
            "    if True:\n"
            "        if True:\n"
            "            if True:\n"
            "                if True:\n"
            "                    if True:\n"
            "                        if True:\n"
            "                            pass\n"
        )
        fp = _write_file(tmp_path, "deep.py", content)
        findings = _analyze_python_file(fp)
        assert any(f.rule == "V14-DEEP-NESTING" for f in findings)


class TestPythonParams:
    def test_few_params_no_finding(self, tmp_path: Path) -> None:
        content = "def func(a, b, c):\n    pass\n"
        fp = _write_file(tmp_path, "few.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)

    def test_many_params_warning(self, tmp_path: Path) -> None:
        content = "def func(a, b, c, d, e, f, g):\n    pass\n"
        fp = _write_file(tmp_path, "many.py", content)
        findings = _analyze_python_file(fp)
        assert any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)

    def test_self_excluded(self, tmp_path: Path) -> None:
        content = "class A:\n    def method(self, a, b, c, d, e):\n        pass\n"
        fp = _write_file(tmp_path, "cls.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)


# ============================================================================
# 1b. Python cognitive complexity
# ============================================================================


class TestPythonCognitiveComplexity:
    def test_simple_function_low_cognitive(self, tmp_path: Path) -> None:
        content = "def add(a, b):\n    return a + b\n"
        fp = _write_file(tmp_path, "math.py", content)
        findings = _analyze_python_file(fp)
        assert not any(f.rule == "V14-COGNITIVE-COMPLEXITY" for f in findings)

    def test_nested_ifs_high_cognitive(self, tmp_path: Path) -> None:
        # Nested ifs penalize more in cognitive complexity
        content = (
            "def deeply_nested(x, y, z):\n"
            "    if x:                    # +1\n"
            "        if y:                # +2 (1 + nesting=1)\n"
            "            if z:            # +3 (1 + nesting=2)\n"
            "                for i in range(10):  # +4 (1 + nesting=3)\n"
            "                    if i > 5:        # +5 (1 + nesting=4)\n"
            "                        if i > 8:    # +6 (1 + nesting=5)\n"
            "                            pass\n"
        )
        import ast

        tree = ast.parse(content)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        score = _python_cognitive_complexity(func)
        # Should be > 15 due to nesting penalties
        assert score > 15

    def test_flat_ifs_lower_cognitive(self) -> None:
        """Sequential ifs have lower cognitive complexity than nested ones."""
        content = (
            "def flat(x):\n"
            "    if x == 1:\n"
            "        return 1\n"
            "    if x == 2:\n"
            "        return 2\n"
            "    if x == 3:\n"
            "        return 3\n"
            "    return 0\n"
        )
        import ast

        tree = ast.parse(content)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        score = _python_cognitive_complexity(func)
        # 3 flat ifs = 3 × (+1 + 0 nesting) = 3
        assert score == 3

    def test_cognitive_warning_in_file(self, tmp_path: Path) -> None:
        # Create deeply nested code that exceeds cognitive threshold
        content = (
            "def very_cognitive(a, b, c, d, e):\n"
            "    if a:\n"
            "        if b:\n"
            "            if c:\n"
            "                if d:\n"
            "                    if e:\n"
            "                        for i in range(10):\n"
            "                            if i > 5:\n"
            "                                while i > 0:\n"
            "                                    i -= 1\n"
            "    if a and b:\n"
            "        for x in range(10):\n"
            "            if x > 3:\n"
            "                pass\n"
        )
        fp = _write_file(tmp_path, "cognitive.py", content)
        findings = _analyze_python_file(fp)
        assert any(f.rule == "V14-COGNITIVE-COMPLEXITY" for f in findings)

    def test_boolean_ops_add_complexity(self) -> None:
        content = "def bool_heavy(a, b, c, d):\n    if a and b:\n        pass\n    if c or d:\n        pass\n"
        import ast

        tree = ast.parse(content)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        score = _python_cognitive_complexity(func)
        # 2 ifs (+1 each) + 2 bool ops (+1 each) = 4
        assert score >= 4


# ============================================================================
# 2. Go analysis
# ============================================================================


class TestGoComplexity:
    def test_simple_function_no_finding(self, tmp_path: Path) -> None:
        content = "package main\n\nfunc add(a, b int) int {\n\treturn a + b\n}\n"
        fp = _write_file(tmp_path, "math.go", content)
        findings = _analyze_go_file(fp)
        assert not any(f.rule == "V14-HIGH-COMPLEXITY" for f in findings)

    def test_high_complexity_warning(self, tmp_path: Path) -> None:
        branches = "\n".join(f"\tif x == {i} {{\n\t\treturn {i}\n\t}}" for i in range(12))
        content = f"package main\n\nfunc complex(x int) int {{\n{branches}\n\treturn 0\n}}\n"
        fp = _write_file(tmp_path, "complex.go", content)
        findings = _analyze_go_file(fp)
        assert any(f.rule == "V14-HIGH-COMPLEXITY" for f in findings)


class TestGoLength:
    def test_long_function_warning(self, tmp_path: Path) -> None:
        # LENGTH_WARN=80 — produce 90 body lines.
        lines = "\n".join(f"\tx := {i}" for i in range(90))
        content = f"package main\n\nfunc longFunc() {{\n{lines}\n}}\n"
        fp = _write_file(tmp_path, "long.go", content)
        findings = _analyze_go_file(fp)
        assert any(f.rule == "V14-LONG-FUNCTION" for f in findings)


class TestGoParams:
    def test_many_params_warning(self, tmp_path: Path) -> None:
        content = "package main\n\nfunc manyParams(a int, b int, c int, d int, e int, f int, g int) {\n}\n"
        fp = _write_file(tmp_path, "params.go", content)
        findings = _analyze_go_file(fp)
        assert any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)

    def test_few_params_no_finding(self, tmp_path: Path) -> None:
        content = "package main\n\nfunc fewParams(a int, b int) {\n}\n"
        fp = _write_file(tmp_path, "few.go", content)
        findings = _analyze_go_file(fp)
        assert not any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)


# ============================================================================
# 3. TypeScript analysis
# ============================================================================


class TestTsComplexity:
    def test_simple_function_no_finding(self, tmp_path: Path) -> None:
        content = "function add(a: number, b: number): number {\n  return a + b;\n}\n"
        fp = _write_file(tmp_path, "math.ts", content)
        findings = _analyze_ts_file(fp)
        assert not any(f.rule == "V14-HIGH-COMPLEXITY" for f in findings)

    def test_high_complexity_warning(self, tmp_path: Path) -> None:
        branches = "\n".join(f"  if (x === {i}) return {i};" for i in range(12))
        content = f"function complex(x: number): number {{\n{branches}\n  return 0;\n}}\n"
        fp = _write_file(tmp_path, "complex.ts", content)
        findings = _analyze_ts_file(fp)
        assert any(f.rule == "V14-HIGH-COMPLEXITY" for f in findings)


class TestTsParams:
    def test_many_params_warning(self, tmp_path: Path) -> None:
        content = "function manyParams(a: number, b: string, c: boolean, d: number, e: string, f: boolean) {\n}\n"
        fp = _write_file(tmp_path, "params.ts", content)
        findings = _analyze_ts_file(fp)
        assert any(f.rule == "V14-TOO-MANY-PARAMS" for f in findings)


# ============================================================================
# 4. validate integration
# ============================================================================


class TestValidateIntegration:
    def test_validate_python_file(self, validator: ComplexityGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
        content = f"def complex_func(x):\n{branches}\n    return 0\n"
        fp = _write_file(tmp_path, "complex.py", content)

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V14-HIGH-COMPLEXITY" for f in result.findings)

    def test_validate_clean_file(self, validator: ComplexityGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        fp = _write_file(tmp_path, "simple.py", "def add(a, b):\n    return a + b\n")

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert not result.has_errors
        assert not result.has_warnings

    def test_validate_go_file(self, validator: ComplexityGuardValidator, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        branches = "\n".join(f"\tif x == {i} {{\n\t\treturn {i}\n\t}}" for i in range(12))
        content = f"package main\n\nfunc complex(x int) int {{\n{branches}\n\treturn 0\n}}\n"
        fp = _write_file(tmp_path, "complex.go", content)

        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.run(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V14-HIGH-COMPLEXITY" for f in result.findings)


# ============================================================================
# 5. should_run
# ============================================================================


class TestShouldRun:
    def test_go_file(self, validator: ComplexityGuardValidator) -> None:
        assert validator.should_run("/project/server/handler.go") is True

    def test_python_file(self, validator: ComplexityGuardValidator) -> None:
        assert validator.should_run("/project/app.py") is True

    def test_ts_file(self, validator: ComplexityGuardValidator) -> None:
        assert validator.should_run("/project/web/src/handler.ts") is True

    def test_tsx_file(self, validator: ComplexityGuardValidator) -> None:
        assert validator.should_run("/project/web/src/App.tsx") is True

    def test_yaml_file_excluded(self, validator: ComplexityGuardValidator) -> None:
        assert validator.should_run("/project/config.yaml") is False


# ============================================================================
# 6. Standalone main()
# ============================================================================


class TestMain:
    def test_main_with_complex_python(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        branches = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(12))
        content = f"def complex_func(x):\n{branches}\n    return 0\n"
        fp = _write_file(tmp_path, "complex.py", content)

        input_data = {
            "tool_name": "Write",
            "tool_input": {"file_path": fp},
            "cwd": str(tmp_path),
        }
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert "additionalContext" in output
        assert "V14-HIGH-COMPLEXITY" in output["additionalContext"]

    def test_main_non_edit_tool(self) -> None:
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/file.py"},
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
    from hooks.validators.complexity_guard import main

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
