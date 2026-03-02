"""Tests for V13: AiCheatingGuardValidator — AI test deletion, disabling, weakening.

Covers:
  - _detect_test_language: test file pattern matching per language
  - check_edit: test deletion, assertion removal, skip addition, weakening
  - _check_skip_patterns: skip/disable pattern detection in file content
  - validate: integration test for PostToolUse mode
  - main(): standalone execution with mocked stdin/stdout
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from hooks.validators.ai_cheating_guard import (
    MOCK_THRESHOLD,
    AiCheatingGuardValidator,
    _count_assertions,
    _count_mocks_in_func,
    _count_test_funcs,
    _count_trivial_assertions,
    _detect_test_language,
    _extract_test_functions,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def validator() -> AiCheatingGuardValidator:
    return AiCheatingGuardValidator()


def _write_file(base: Path, name: str, content: str) -> str:
    fp = base / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content)
    return str(fp)


# ============================================================================
# 1. _detect_test_language
# ============================================================================


class TestDetectTestLanguage:
    def test_go_test_file(self) -> None:
        assert _detect_test_language("handler_test.go") == "go"

    def test_python_test_prefix(self) -> None:
        assert _detect_test_language("test_handler.py") == "python"

    def test_python_test_suffix(self) -> None:
        assert _detect_test_language("handler_test.py") == "python"

    def test_ts_test_file(self) -> None:
        assert _detect_test_language("handler.test.ts") == "typescript"

    def test_tsx_spec_file(self) -> None:
        assert _detect_test_language("Button.spec.tsx") == "typescript"

    def test_js_test_file(self) -> None:
        assert _detect_test_language("utils.test.js") == "typescript"

    def test_jsx_spec_file(self) -> None:
        assert _detect_test_language("App.spec.jsx") == "typescript"

    def test_tests_directory(self) -> None:
        assert _detect_test_language("src/__tests__/handler.ts") == "typescript"

    def test_non_test_file(self) -> None:
        assert _detect_test_language("handler.go") is None

    def test_non_test_python(self) -> None:
        assert _detect_test_language("handler.py") is None

    def test_non_test_ts(self) -> None:
        assert _detect_test_language("handler.ts") is None


# ============================================================================
# 2. count helpers
# ============================================================================


class TestCountHelpers:
    def test_count_go_test_funcs(self) -> None:
        content = "func TestAdd(t *testing.T) {\n}\nfunc TestSub(t *testing.T) {\n}\n"
        assert _count_test_funcs(content, "go") == 2

    def test_count_go_benchmark(self) -> None:
        content = "func BenchmarkAdd(b *testing.B) {\n}\n"
        assert _count_test_funcs(content, "go") == 1

    def test_count_python_test_funcs(self) -> None:
        content = "def test_add():\n    pass\ndef test_sub():\n    pass\n"
        assert _count_test_funcs(content, "python") == 2

    def test_count_python_test_class(self) -> None:
        content = "class TestMath:\n    def test_add(self):\n        pass\n"
        assert _count_test_funcs(content, "python") == 2  # class + method

    def test_count_ts_test_funcs(self) -> None:
        content = 'it("should add", () => {});\ntest("should sub", () => {});\n'
        assert _count_test_funcs(content, "typescript") == 2

    def test_count_ts_describe(self) -> None:
        content = 'describe("Math", () => {\n  it("adds", () => {});\n});\n'
        assert _count_test_funcs(content, "typescript") == 2

    def test_count_go_assertions(self) -> None:
        content = "assert.Equal(t, 1, 1)\nrequire.NoError(t, err)\nt.Fatal(err)\n"
        assert _count_assertions(content, "go") == 3

    def test_count_python_assertions(self) -> None:
        content = "assert result == 1\nself.assertEqual(a, b)\npytest.raises(ValueError)\n"
        assert _count_assertions(content, "python") == 3

    def test_count_ts_assertions(self) -> None:
        content = "expect(result).toBe(1);\nassert.equal(a, b);\n"
        assert _count_assertions(content, "typescript") == 2


# ============================================================================
# 3. check_edit — test deletion
# ============================================================================


class TestCheckEditTestDeleted:
    def test_go_test_deleted(self, validator: AiCheatingGuardValidator) -> None:
        old = "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\nfunc TestSub(t *testing.T) {\n\tassert.Equal(t, 0, sub(1, 1))\n}\n"
        new = "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert any(f.rule == "V13-TEST-DELETED" for f in findings)
        deleted_finding = next(f for f in findings if f.rule == "V13-TEST-DELETED")
        assert "1 test function" in deleted_finding.message
        assert deleted_finding.severity == "error"

    def test_python_test_deleted(self, validator: AiCheatingGuardValidator) -> None:
        old = "def test_add():\n    assert add(1, 1) == 2\ndef test_sub():\n    assert sub(1, 1) == 0\n"
        new = "def test_add():\n    assert add(1, 1) == 2\n"
        findings = validator.check_edit("test_math.py", old, new)
        assert any(f.rule == "V13-TEST-DELETED" for f in findings)

    def test_ts_test_deleted(self, validator: AiCheatingGuardValidator) -> None:
        old = 'it("should add", () => { expect(add(1,1)).toBe(2); });\nit("should sub", () => { expect(sub(1,1)).toBe(0); });\n'
        new = 'it("should add", () => { expect(add(1,1)).toBe(2); });\n'
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-TEST-DELETED" for f in findings)

    def test_no_deletion_no_finding(self, validator: AiCheatingGuardValidator) -> None:
        old = "func TestAdd(t *testing.T) {\n}\n"
        new = "func TestAdd(t *testing.T) {\n\t// updated\n}\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert not any(f.rule == "V13-TEST-DELETED" for f in findings)

    def test_non_test_file_ignored(self, validator: AiCheatingGuardValidator) -> None:
        old = "func TestAdd(t *testing.T) {}\n"
        new = ""
        findings = validator.check_edit("handler.go", old, new)
        assert findings == []


# ============================================================================
# 4. check_edit — assertion removal
# ============================================================================


class TestCheckEditAssertionRemoved:
    def test_go_assertion_removed(self, validator: AiCheatingGuardValidator) -> None:
        old = "assert.Equal(t, 1, 1)\nassert.NoError(t, err)\nassert.True(t, ok)\n"
        new = "assert.Equal(t, 1, 1)\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert any(f.rule == "V13-ASSERTION-REMOVED" for f in findings)
        finding = next(f for f in findings if f.rule == "V13-ASSERTION-REMOVED")
        assert "2 assertion" in finding.message
        assert finding.severity == "warning"

    def test_python_assertion_removed(self, validator: AiCheatingGuardValidator) -> None:
        old = "assert result == 1\nassert error is None\n"
        new = "assert result == 1\n"
        findings = validator.check_edit("test_math.py", old, new)
        assert any(f.rule == "V13-ASSERTION-REMOVED" for f in findings)

    def test_ts_assertion_removed(self, validator: AiCheatingGuardValidator) -> None:
        old = "expect(a).toBe(1);\nexpect(b).toBe(2);\nexpect(c).toBe(3);\n"
        new = "expect(a).toBe(1);\n"
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-ASSERTION-REMOVED" for f in findings)

    def test_assertion_added_no_finding(self, validator: AiCheatingGuardValidator) -> None:
        old = "assert.Equal(t, 1, 1)\n"
        new = "assert.Equal(t, 1, 1)\nassert.NoError(t, err)\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert not any(f.rule == "V13-ASSERTION-REMOVED" for f in findings)


# ============================================================================
# 5. check_edit — test disabled
# ============================================================================


class TestCheckEditTestDisabled:
    def test_go_skip_added(self, validator: AiCheatingGuardValidator) -> None:
        old = "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\n"
        new = "func TestAdd(t *testing.T) {\n\tt.Skip(\"temporarily disabled\")\n\tassert.Equal(t, 2, add(1, 1))\n}\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert any(f.rule == "V13-TEST-DISABLED" for f in findings)

    def test_python_skip_added(self, validator: AiCheatingGuardValidator) -> None:
        old = "def test_add():\n    assert add(1, 1) == 2\n"
        new = "@pytest.mark.skip\ndef test_add():\n    assert add(1, 1) == 2\n"
        findings = validator.check_edit("test_math.py", old, new)
        assert any(f.rule == "V13-TEST-DISABLED" for f in findings)

    def test_ts_xit_added(self, validator: AiCheatingGuardValidator) -> None:
        old = 'it("should add", () => { expect(add(1,1)).toBe(2); });\n'
        new = 'xit("should add", () => { expect(add(1,1)).toBe(2); });\n'
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-TEST-DISABLED" for f in findings)

    def test_ts_describe_skip_added(self, validator: AiCheatingGuardValidator) -> None:
        old = 'describe("Math", () => {});\n'
        new = 'describe.skip("Math", () => {});\n'
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-TEST-DISABLED" for f in findings)

    def test_no_skip_no_finding(self, validator: AiCheatingGuardValidator) -> None:
        old = "func TestAdd(t *testing.T) {\n}\n"
        new = "func TestAdd(t *testing.T) {\n\t// comment\n}\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert not any(f.rule == "V13-TEST-DISABLED" for f in findings)


# ============================================================================
# 6. check_edit — test weakened
# ============================================================================


class TestCheckEditTestWeakened:
    def test_go_weakened(self, validator: AiCheatingGuardValidator) -> None:
        old = "assert.Equal(t, expected, actual)\n"
        new = "assert.NotNil(t, actual)\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert any(f.rule == "V13-TEST-WEAKENED" for f in findings)

    def test_python_weakened(self, validator: AiCheatingGuardValidator) -> None:
        old = "self.assertEqual(a, b)\n"
        new = "self.assertTrue(a)\n"
        findings = validator.check_edit("test_math.py", old, new)
        assert any(f.rule == "V13-TEST-WEAKENED" for f in findings)

    def test_ts_weakened(self, validator: AiCheatingGuardValidator) -> None:
        old = "expect(result).toEqual(expected);\n"
        new = "expect(result).toBeTruthy();\n"
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-TEST-WEAKENED" for f in findings)

    def test_ts_to_defined_weakened(self, validator: AiCheatingGuardValidator) -> None:
        old = "expect(result).toBe(42);\n"
        new = "expect(result).toBeDefined();\n"
        findings = validator.check_edit("math.test.ts", old, new)
        assert any(f.rule == "V13-TEST-WEAKENED" for f in findings)

    def test_no_weakening_no_finding(self, validator: AiCheatingGuardValidator) -> None:
        old = "assert.Equal(t, 1, 1)\n"
        new = "assert.Equal(t, 2, add(1, 1))\n"
        findings = validator.check_edit("math_test.go", old, new)
        assert not any(f.rule == "V13-TEST-WEAKENED" for f in findings)


# ============================================================================
# 7. _extract_test_functions
# ============================================================================


class TestExtractTestFunctions:
    def test_python_functions(self) -> None:
        content = "def test_add():\n    assert 1 + 1 == 2\n\ndef test_sub():\n    assert 2 - 1 == 1\n"
        funcs = _extract_test_functions(content, "python")
        assert len(funcs) == 2
        assert funcs[0][0] == "test_add"
        assert funcs[1][0] == "test_sub"

    def test_python_indented_class_methods(self) -> None:
        content = (
            "class TestMath:\n"
            "    def test_add(self):\n"
            "        assert 1 == 1\n"
            "\n"
            "    def test_sub(self):\n"
            "        assert 2 == 2\n"
        )
        funcs = _extract_test_functions(content, "python")
        assert len(funcs) == 2

    def test_go_functions(self) -> None:
        content = (
            "func TestAdd(t *testing.T) {\n"
            "\tassert.Equal(t, 2, 1+1)\n"
            "}\n\n"
            "func TestSub(t *testing.T) {\n"
            "\tassert.Equal(t, 1, 2-1)\n"
            "}\n"
        )
        funcs = _extract_test_functions(content, "go")
        assert len(funcs) == 2
        assert funcs[0][0] == "TestAdd"
        assert funcs[1][0] == "TestSub"

    def test_ts_functions(self) -> None:
        content = (
            'it("should add", () => {\n'
            "  expect(1 + 1).toBe(2);\n"
            "});\n\n"
            'test("should sub", () => {\n'
            "  expect(2 - 1).toBe(1);\n"
            "});\n"
        )
        funcs = _extract_test_functions(content, "typescript")
        assert len(funcs) == 2
        assert funcs[0][0] == "should add"
        assert funcs[1][0] == "should sub"

    def test_empty_content(self) -> None:
        assert _extract_test_functions("", "python") == []
        assert _extract_test_functions("", "go") == []
        assert _extract_test_functions("", "typescript") == []


# ============================================================================
# 8. Mock counting and excessive mocking
# ============================================================================


class TestMockCounting:
    def test_count_python_mocks(self) -> None:
        func_body = (
            "def test_handler():\n"
            "    with mock.patch('a') as m1:\n"
            "        with mock.patch('b') as m2:\n"
            "            m3 = mock.MagicMock()\n"
            "            m4 = mock.Mock()\n"
            "            assert True\n"
        )
        assert _count_mocks_in_func(func_body, "python") == 4

    def test_count_go_mocks(self) -> None:
        func_body = (
            "func TestHandler(t *testing.T) {\n"
            "\tctrl := gomock.NewController(t)\n"
            "\tmockA := mock.NewMockA(ctrl)\n"
            "\tmockB := mock.NewMockB(ctrl)\n"
            "\tmockA.EXPECT().Do().Return(nil)\n"
            "\tmockB.EXPECT().Do().Return(nil)\n"
            "}\n"
        )
        assert _count_mocks_in_func(func_body, "go") == 5

    def test_count_ts_mocks(self) -> None:
        func_body = (
            'it("should handle", () => {\n'
            '  jest.mock("./a");\n'
            '  jest.mock("./b");\n'
            "  const spy = jest.spyOn(obj, 'method');\n"
            "  spy.mockReturnValue(42);\n"
            "  expect(result).toBe(42);\n"
            "});\n"
        )
        assert _count_mocks_in_func(func_body, "typescript") == 4

    def test_zero_mocks(self) -> None:
        assert _count_mocks_in_func("def test_simple():\n    assert 1 == 1\n", "python") == 0


class TestExcessiveMocks:
    def test_python_excessive_mocks_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        # Create a test with > MOCK_THRESHOLD mocks
        patches = "\n".join(f"    with mock.patch('mod{i}'):" for i in range(MOCK_THRESHOLD + 1))
        content = f"def test_over_mocked():\n{patches}\n        assert True\n"
        fp = _write_file(tmp_path, "test_handler.py", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-MOCK-EVERYTHING" for f in result.findings)

    def test_python_under_threshold_no_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        content = (
            "def test_normal():\n"
            "    with mock.patch('a'):\n"
            "        with mock.patch('b'):\n"
            "            assert True\n"
        )
        fp = _write_file(tmp_path, "test_handler.py", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V13-MOCK-EVERYTHING" for f in result.findings)

    def test_go_excessive_mocks_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        mocks = "\n".join(f"\tmock{i} := mock.NewMock{i}(ctrl)" for i in range(MOCK_THRESHOLD + 1))
        content = (
            "package handler_test\n\n"
            "func TestOverMocked(t *testing.T) {\n"
            "\tctrl := gomock.NewController(t)\n"
            f"{mocks}\n"
            "}\n"
        )
        fp = _write_file(tmp_path, "handler_test.go", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-MOCK-EVERYTHING" for f in result.findings)

    def test_ts_excessive_mocks_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        mocks = "\n".join(f'  jest.mock("./mod{i}");' for i in range(MOCK_THRESHOLD + 1))
        content = f'it("should handle", () => {{\n{mocks}\n  expect(true).toBe(true);\n}});\n'
        fp = _write_file(tmp_path, "handler.test.ts", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-MOCK-EVERYTHING" for f in result.findings)


# ============================================================================
# 9. Trivial assertion detection
# ============================================================================


class TestTrivialAssertions:
    def test_python_assert_true(self) -> None:
        content = "def test_placeholder():\n    assert True\n"
        matches = _count_trivial_assertions(content, "python")
        assert len(matches) >= 1

    def test_python_assert_1_eq_1(self) -> None:
        content = "def test_placeholder():\n    assert 1 == 1\n"
        matches = _count_trivial_assertions(content, "python")
        assert len(matches) >= 1

    def test_python_self_assertTrue_True(self) -> None:
        content = "class TestFoo:\n    def test_it(self):\n        self.assertTrue(True)\n"
        matches = _count_trivial_assertions(content, "python")
        assert len(matches) >= 1

    def test_python_real_assert_no_match(self) -> None:
        content = "def test_real():\n    assert result == expected\n"
        matches = _count_trivial_assertions(content, "python")
        assert len(matches) == 0

    def test_go_assert_true_true(self) -> None:
        content = "func TestFoo(t *testing.T) {\n\tassert.True(t, true)\n}\n"
        matches = _count_trivial_assertions(content, "go")
        assert len(matches) >= 1

    def test_go_assert_equal_1_1(self) -> None:
        content = "func TestFoo(t *testing.T) {\n\tassert.Equal(t, 1, 1)\n}\n"
        matches = _count_trivial_assertions(content, "go")
        assert len(matches) >= 1

    def test_go_real_assert_no_match(self) -> None:
        content = "func TestFoo(t *testing.T) {\n\tassert.Equal(t, expected, actual)\n}\n"
        matches = _count_trivial_assertions(content, "go")
        assert len(matches) == 0

    def test_ts_expect_true_toBe_true(self) -> None:
        content = 'it("should pass", () => { expect(true).toBe(true); });\n'
        matches = _count_trivial_assertions(content, "typescript")
        assert len(matches) >= 1

    def test_ts_expect_1_toBe_1(self) -> None:
        content = 'it("should pass", () => { expect(1).toBe(1); });\n'
        matches = _count_trivial_assertions(content, "typescript")
        assert len(matches) >= 1

    def test_ts_real_expect_no_match(self) -> None:
        content = 'it("should work", () => { expect(result).toBe(42); });\n'
        matches = _count_trivial_assertions(content, "typescript")
        assert len(matches) == 0


class TestTrivialTestValidation:
    def test_python_trivial_test_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        content = "def test_placeholder():\n    assert True\n"
        fp = _write_file(tmp_path, "test_handler.py", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-TRIVIAL-TEST" for f in result.findings)

    def test_go_trivial_test_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        content = "func TestPlaceholder(t *testing.T) {\n\tassert.True(t, true)\n}\n"
        fp = _write_file(tmp_path, "handler_test.go", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-TRIVIAL-TEST" for f in result.findings)

    def test_ts_trivial_test_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        content = 'it("placeholder", () => { expect(true).toBe(true); });\n'
        fp = _write_file(tmp_path, "handler.test.ts", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-TRIVIAL-TEST" for f in result.findings)

    def test_real_test_no_trivial_warning(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        content = "def test_real():\n    result = add(1, 2)\n    assert result == 3\n"
        fp = _write_file(tmp_path, "test_math.py", content)
        from lib.project_context import ProjectContext

        (tmp_path / ".git").mkdir(exist_ok=True)
        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert not any(f.rule == "V13-TRIVIAL-TEST" for f in result.findings)


# ============================================================================
# 10. validate (file-level checks — skip patterns)
# ============================================================================


class TestValidate:
    def test_validate_detects_skip_in_go_file(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        fp = _write_file(
            tmp_path,
            "math_test.go",
            'func TestAdd(t *testing.T) {\n\tt.Skip("temp")\n}\n',
        )
        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-TEST-DISABLED" for f in result.findings)

    def test_validate_detects_skip_in_python_file(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        fp = _write_file(
            tmp_path,
            "test_math.py",
            "@pytest.mark.skip\ndef test_add():\n    assert 1 == 1\n",
        )
        (tmp_path / ".git").mkdir(exist_ok=True)
        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert any(f.rule == "V13-TEST-DISABLED" for f in result.findings)

    def test_validate_clean_file_no_findings(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        fp = _write_file(
            tmp_path,
            "math_test.go",
            "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\n",
        )
        (tmp_path / ".git").mkdir(exist_ok=True)
        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert not result.has_errors
        assert not result.has_warnings

    def test_validate_non_test_file_no_findings(
        self, validator: AiCheatingGuardValidator, tmp_path: Path
    ) -> None:
        fp = _write_file(tmp_path, "handler.go", "package main\n")
        (tmp_path / ".git").mkdir(exist_ok=True)
        from lib.project_context import ProjectContext

        ctx = ProjectContext(tmp_path)
        result = validator.validate(ctx, file_path=fp, mode="post_tool_use")
        assert not result.findings


# ============================================================================
# 11. should_run
# ============================================================================


class TestShouldRun:
    def test_go_test_file(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("handler_test.go") is True

    def test_python_test_file(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("test_handler.py") is True

    def test_ts_test_file(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("handler.test.ts") is True

    def test_spec_file(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("handler.spec.tsx") is True

    def test_non_test_file(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("handler.go") is False

    def test_non_test_python(self, validator: AiCheatingGuardValidator) -> None:
        assert validator.should_run("handler.py") is False


# ============================================================================
# 12. Standalone main()
# ============================================================================


class TestMain:
    def test_main_edit_with_test_deletion(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        fp = _write_file(
            tmp_path,
            "math_test.go",
            "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\n",
        )
        input_data = {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": fp,
                "old_string": "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\nfunc TestSub(t *testing.T) {\n\tassert.Equal(t, 0, sub(1, 1))\n}\n",
                "new_string": "func TestAdd(t *testing.T) {\n\tassert.Equal(t, 2, add(1, 1))\n}\n",
            },
            "cwd": str(tmp_path),
        }
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert "additionalContext" in output
        assert "V13-TEST-DELETED" in output["additionalContext"]

    def test_main_non_edit_tool_ignored(self) -> None:
        input_data = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/some/test_math.py"},
            "cwd": ".",
        }
        stdout = _run_main(input_data)
        output = json.loads(stdout)
        assert output == {}

    def test_main_non_test_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        fp = _write_file(tmp_path, "handler.go", "package main\n")
        input_data = {
            "tool_name": "Edit",
            "tool_input": {"file_path": fp},
            "cwd": str(tmp_path),
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
    from hooks.validators.ai_cheating_guard import main

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
