"""V14: Complexity Guard — cyclomatic complexity, function length, nesting, params.

Inspired by Robert C. Martin (Uncle Bob) "Keep methods small, single-responsibility"
and Neal Ford's "Architecture Fitness Functions" for automated quality gates.

Checks:
  V14-HIGH-COMPLEXITY: Function cyclomatic complexity exceeds threshold
  V14-COGNITIVE-COMPLEXITY: Function cognitive complexity exceeds threshold (Sonar-style)
  V14-LONG-FUNCTION: Function exceeds line count threshold
  V14-DEEP-NESTING: Code nesting depth exceeds threshold
  V14-TOO-MANY-PARAMS: Function parameter count exceeds threshold
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import ast
import hashlib
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.per_file_cache import PerFileCache
from lib.config_loader import ComplexityThresholds
from lib.project_context import ProjectContext

# ── Thresholds ──────────────────────────────────────────────────────────────
# Defaults sourced from ComplexityThresholds so a single source of truth
# governs both this validator and the per-project config schema.
# The legacy module-level constants are kept as read-once aliases so
# anything that historically imported them keeps working.

_DEFAULT_THRESHOLDS = ComplexityThresholds()

COMPLEXITY_WARN = _DEFAULT_THRESHOLDS.cyclomatic_warn
COMPLEXITY_ERROR = _DEFAULT_THRESHOLDS.cyclomatic_error

LENGTH_WARN = _DEFAULT_THRESHOLDS.function_lines_warn
LENGTH_ERROR = _DEFAULT_THRESHOLDS.function_lines_error

NESTING_WARN = _DEFAULT_THRESHOLDS.nesting_warn

PARAMS_WARN = _DEFAULT_THRESHOLDS.params_warn

# Cognitive complexity (Sonar-style: penalizes nesting)
COGNITIVE_WARN = _DEFAULT_THRESHOLDS.cognitive_warn
COGNITIVE_ERROR = _DEFAULT_THRESHOLDS.cognitive_error


# ── Python analysis (AST-based) ────────────────────────────────────────────


def _analyze_python_file(
    file_path: str,
    thresholds: ComplexityThresholds | None = None,
) -> list[Finding]:
    """Analyze a Python file using the ast module for precise metrics.

    ``thresholds`` defaults to the module-level defaults; pass an
    overridden ``ComplexityThresholds`` to honor per-project config.
    """
    t = thresholds or _DEFAULT_THRESHOLDS

    try:
        content = Path(file_path).read_text(errors="replace")
    except OSError:
        return []

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_check_python_function(node, file_path, t))
    return findings


class _FuncLoc(NamedTuple):
    """Location info for a function being checked."""

    func_name: str
    file_path: str
    start_line: int


def _check_python_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: str,
    thresholds: ComplexityThresholds | None = None,
) -> list[Finding]:
    """Check a single Python function for all complexity metrics."""
    t = thresholds or _DEFAULT_THRESHOLDS
    loc = _FuncLoc(node.name, file_path, node.lineno)
    findings: list[Finding] = []

    findings.extend(
        _check_threshold(
            _python_cyclomatic_complexity(node),
            t.cyclomatic_warn,
            t.cyclomatic_error,
            "V14-HIGH-COMPLEXITY",
            "cyclomatic complexity",
            loc,
        )
    )
    findings.extend(
        _check_threshold(
            _python_cognitive_complexity(node),
            t.cognitive_warn,
            t.cognitive_error,
            "V14-COGNITIVE-COMPLEXITY",
            "cognitive complexity",
            loc,
        )
    )

    end_line = getattr(node, "end_lineno", None)
    if end_line:
        length = end_line - loc.start_line + 1
        findings.extend(
            _check_threshold(
                length,
                t.function_lines_warn,
                t.function_lines_error,
                "V14-LONG-FUNCTION",
                "lines long",
                loc,
            )
        )

    findings.extend(
        _check_threshold(
            _python_max_nesting(node),
            t.nesting_warn,
            t.nesting_warn + 1,
            "V14-DEEP-NESTING",
            "nesting depth",
            loc,
        )
    )
    findings.extend(
        _check_threshold(
            _python_param_count(node),
            t.params_warn,
            t.params_warn + 1,
            "V14-TOO-MANY-PARAMS",
            "parameters",
            loc,
        )
    )
    return findings


def _check_threshold(
    value: int,
    warn_threshold: int,
    error_threshold: int,
    rule: str,
    metric_name: str,
    loc: _FuncLoc,
) -> list[Finding]:
    """Check a metric value against warn/error thresholds and return findings."""
    if value > error_threshold:
        return [
            Finding(
                severity="error",
                file=loc.file_path,
                rule=rule,
                message=f"Function '{loc.func_name}' has {metric_name} {value} (max {error_threshold})",
                fix=f"Refactor '{loc.func_name}' at {loc.file_path}:{loc.start_line} into smaller functions.",
                line=loc.start_line,
            )
        ]
    if value > warn_threshold:
        return [
            Finding(
                severity="warning",
                file=loc.file_path,
                rule=rule,
                message=f"Function '{loc.func_name}' has {metric_name} {value} (recommended max {warn_threshold})",
                fix=f"Consider simplifying '{loc.func_name}' at {loc.file_path}:{loc.start_line}.",
                line=loc.start_line,
            )
        ]
    return []


_CYCLOMATIC_SINGLE_INCREMENT = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.With,
    ast.AsyncWith,
    ast.Assert,
)


def _python_cyclomatic_complexity(node: ast.AST) -> int:
    """Calculate cyclomatic complexity of a Python function.

    Counts: if, elif, for, while, except, with, and, or, assert (condition part),
    comprehension ifs, ternary expressions.
    Base complexity is 1.
    """
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, _CYCLOMATIC_SINGLE_INCREMENT):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            complexity += len(child.ifs)
    return complexity


def _python_max_nesting(node: ast.AST) -> int:
    """Calculate the maximum nesting depth within a function.

    Counts: if/elif/else, for, while, with, try/except blocks.
    """
    return _walk_nesting(node, 0) - 1  # Subtract 1 for the function itself


def _walk_nesting(node: ast.AST, current_depth: int) -> int:
    """Recursively find maximum nesting depth."""
    # These nodes increase nesting depth
    nesting_nodes = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.ExceptHandler,
    )

    # Also handle TryStar for Python 3.11+
    try:
        nesting_nodes = (*nesting_nodes, ast.TryStar)  # type: ignore[attr-defined]
    except AttributeError:
        pass

    max_depth = current_depth

    for child in ast.iter_child_nodes(node):
        if isinstance(child, nesting_nodes):
            child_depth = _walk_nesting(child, current_depth + 1)
        else:
            child_depth = _walk_nesting(child, current_depth)
        max_depth = max(max_depth, child_depth)

    return max_depth


def _python_param_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count function parameters, excluding 'self' and 'cls'."""
    args = node.args
    count = 0
    for arg in args.args:
        if arg.arg not in ("self", "cls"):
            count += 1
    count += len(args.posonlyargs)
    count += len(args.kwonlyargs)
    if args.vararg:
        count += 1
    if args.kwarg:
        count += 1
    return count


def _python_cognitive_complexity(node: ast.AST) -> int:
    """Calculate cognitive complexity of a Python function (Sonar-style).

    Unlike cyclomatic complexity, cognitive complexity penalizes nested conditions
    more heavily. Each nesting level adds a weight to the increment.

    Rules:
      1. Base increment (+1) for: if, elif, else, for, while, except, ternary
      2. Nesting increment: +current_nesting for each of the above
      3. Structural increment (+1) for: break, continue, recursion
      4. Boolean sequence: consecutive `and`/`or` add 0, switching adds +1
    """
    return _walk_cognitive(node, nesting=0)


def _walk_cognitive(node: ast.AST, nesting: int) -> int:
    """Recursively calculate cognitive complexity."""
    total = 0

    # Nodes that increase both complexity and nesting
    nesting_nodes = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)
    # Nodes that increase complexity but not nesting
    flat_nodes = (ast.ExceptHandler,)

    for child in ast.iter_child_nodes(node):
        if isinstance(child, nesting_nodes):
            # +1 base + nesting level bonus
            total += 1 + nesting
            # Recurse with increased nesting
            total += _walk_cognitive(child, nesting + 1)
        elif isinstance(child, flat_nodes):
            total += 1 + nesting
            total += _walk_cognitive(child, nesting + 1)
        elif isinstance(child, ast.IfExp):
            # Ternary expression
            total += 1 + nesting
            total += _walk_cognitive(child, nesting)
        elif isinstance(child, ast.BoolOp):
            # Boolean operators: +1 for switching between and/or sequences
            total += 1
            total += _walk_cognitive(child, nesting)
        elif isinstance(child, (ast.Break, ast.Continue)):
            total += 1
        else:
            total += _walk_cognitive(child, nesting)

    return total


# ── Go analysis (regex-based) ──────────────────────────────────────────────

# Regex to match Go function declarations
GO_FUNC_RE = re.compile(
    r"^func\s+"
    r"(?:\([^)]*\)\s*)?"  # optional receiver
    r"(\w+)\s*"  # function name
    r"\(([^)]*)\)",  # parameters
    re.MULTILINE,
)

# Go branch keywords that increase complexity
GO_BRANCH_PATTERNS = [
    r"\bif\s+",
    r"\belse\s+if\s+",
    r"\bcase\s+",
    r"\bfor\s+",
    r"\bfor\s*{",  # infinite loop
    r"\bselect\s*{",
    r"\|\|",
    r"&&",
]

# Go nesting patterns
GO_NESTING_OPEN = re.compile(r"\{")
GO_NESTING_CLOSE = re.compile(r"\}")


def _analyze_go_file(
    file_path: str,
    thresholds: ComplexityThresholds | None = None,
) -> list[Finding]:
    """Analyze a Go file using regex-based heuristics."""
    t = thresholds or _DEFAULT_THRESHOLDS

    try:
        content = Path(file_path).read_text(errors="replace")
    except OSError:
        return []

    findings: list[Finding] = []
    lines = content.split("\n")

    # Find all function boundaries
    functions = _go_find_functions(lines)

    for func_name, start_line, end_line, param_str in functions:
        func_lines = lines[start_line - 1 : end_line]
        func_body = "\n".join(func_lines)

        # ── Complexity ──
        complexity = _go_cyclomatic_complexity(func_body)
        if complexity > t.cyclomatic_error:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (max {t.cyclomatic_error})",
                    fix=f"Refactor '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif complexity > t.cyclomatic_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=(
                        f"Function '{func_name}' has cyclomatic complexity {complexity} "
                        f"(recommended max {t.cyclomatic_warn})"
                    ),
                    fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Length ──
        length = end_line - start_line + 1
        if length > t.function_lines_error:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (max {t.function_lines_error})",
                    fix=f"Break '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif length > t.function_lines_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (recommended max {t.function_lines_warn})",
                    fix=f"Consider splitting '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Nesting ──
        max_depth = _go_max_nesting(func_lines)
        if max_depth > t.nesting_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-DEEP-NESTING",
                    message=f"Function '{func_name}' has nesting depth {max_depth} (max {t.nesting_warn})",
                    fix=f"Reduce nesting in '{func_name}' at {file_path}:{start_line}. Use early returns or guard clauses.",
                    line=start_line,
                )
            )

        # ── Params ──
        params = _go_param_count(param_str)
        if params > t.params_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-TOO-MANY-PARAMS",
                    message=f"Function '{func_name}' has {params} parameters (max {t.params_warn})",
                    fix=f"Reduce parameters for '{func_name}' at {file_path}:{start_line}. Use a struct for grouped parameters.",
                    line=start_line,
                )
            )

    return findings


def _go_find_functions(lines: list[str]) -> list[tuple[str, int, int, str]]:
    """Find Go function boundaries.

    Returns list of (func_name, start_line, end_line, param_string).
    Uses brace counting to find function end.
    """
    functions: list[tuple[str, int, int, str]] = []

    for i, line in enumerate(lines):
        match = GO_FUNC_RE.match(line)
        if match:
            func_name = match.group(1)
            param_str = match.group(2)
            start_line = i + 1  # 1-indexed

            # Find closing brace by counting
            brace_count = 0
            end_line = start_line
            found_open = False
            for j in range(i, len(lines)):
                for ch in lines[j]:
                    if ch == "{":
                        brace_count += 1
                        found_open = True
                    elif ch == "}":
                        brace_count -= 1

                if found_open and brace_count == 0:
                    end_line = j + 1  # 1-indexed
                    break
            else:
                end_line = len(lines)

            functions.append((func_name, start_line, end_line, param_str))

    return functions


def _go_cyclomatic_complexity(func_body: str) -> int:
    """Calculate cyclomatic complexity for a Go function body."""
    complexity = 1
    for pattern in GO_BRANCH_PATTERNS:
        complexity += len(re.findall(pattern, func_body))
    return complexity


def _go_max_nesting(func_lines: list[str]) -> int:
    """Calculate max nesting depth for Go function lines.

    Subtracts 1 for the function-level braces.
    """
    max_depth = 0
    current_depth = 0

    for line in func_lines:
        # Skip comments and strings (simple heuristic)
        stripped = line.strip()
        if stripped.startswith("//"):
            continue

        for ch in line:
            if ch == "{":
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif ch == "}":
                current_depth -= 1

    # Subtract 1 for the function body itself
    return max(0, max_depth - 1)


def _go_param_count(param_str: str) -> int:
    """Count Go function parameters from parameter string."""
    param_str = param_str.strip()
    if not param_str:
        return 0

    # Split by comma, count non-empty segments
    params = [p.strip() for p in param_str.split(",") if p.strip()]
    return len(params)


# ── TypeScript analysis (regex-based) ──────────────────────────────────────

# TS function patterns
TS_FUNC_PATTERNS = [
    # function declaration
    re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", re.MULTILINE),
    # arrow function assigned to const/let/var
    re.compile(
        r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*[^=]+)?\s*=>", re.MULTILINE
    ),
    # class method
    re.compile(r"(?:public|private|protected|static|async|\s)*(\w+)\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\s*\{", re.MULTILINE),
]

TS_BRANCH_PATTERNS = [
    r"\bif\s*\(",
    r"\belse\s+if\s*\(",
    r"\bcase\s+",
    r"\bfor\s*\(",
    r"\bwhile\s*\(",
    r"\bcatch\s*\(",
    r"\|\|",
    r"&&",
    r"\?\?",
    r"\?\s*[^:]+\s*:",  # ternary
]


def _analyze_ts_file(
    file_path: str,
    thresholds: ComplexityThresholds | None = None,
) -> list[Finding]:
    """Analyze a TypeScript file using regex-based heuristics."""
    t = thresholds or _DEFAULT_THRESHOLDS

    try:
        content = Path(file_path).read_text(errors="replace")
    except OSError:
        return []

    findings: list[Finding] = []
    lines = content.split("\n")

    # Find functions
    functions = _ts_find_functions(lines, content)

    for func_name, start_line, end_line, param_str in functions:
        func_lines = lines[start_line - 1 : end_line]
        func_body = "\n".join(func_lines)

        # ── Complexity ──
        complexity = _ts_cyclomatic_complexity(func_body)
        if complexity > t.cyclomatic_error:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (max {t.cyclomatic_error})",
                    fix=f"Refactor '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif complexity > t.cyclomatic_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=(
                        f"Function '{func_name}' has cyclomatic complexity {complexity} "
                        f"(recommended max {t.cyclomatic_warn})"
                    ),
                    fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Length ──
        length = end_line - start_line + 1
        if length > t.function_lines_error:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (max {t.function_lines_error})",
                    fix=f"Break '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif length > t.function_lines_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (recommended max {t.function_lines_warn})",
                    fix=f"Consider splitting '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Nesting ──
        max_depth = _ts_max_nesting(func_lines)
        if max_depth > t.nesting_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-DEEP-NESTING",
                    message=f"Function '{func_name}' has nesting depth {max_depth} (max {t.nesting_warn})",
                    fix=f"Reduce nesting in '{func_name}' at {file_path}:{start_line}. Use early returns or guard clauses.",
                    line=start_line,
                )
            )

        # ── Params ──
        params = _ts_param_count(param_str)
        if params > t.params_warn:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-TOO-MANY-PARAMS",
                    message=f"Function '{func_name}' has {params} parameters (max {t.params_warn})",
                    fix=f"Reduce parameters for '{func_name}' at {file_path}:{start_line}. Use an options object instead.",
                    line=start_line,
                )
            )

    return findings


_TS_NON_FUNC_KEYWORDS = frozenset(("if", "for", "while", "switch", "catch", "import", "from", "return", "new"))


def _ts_find_functions(lines: list[str], content: str) -> list[tuple[str, int, int, str]]:
    """Find TypeScript function boundaries.

    Returns list of (func_name, start_line, end_line, param_string).
    """
    functions: list[tuple[str, int, int, str]] = []
    seen_lines: set[int] = set()

    for pattern in TS_FUNC_PATTERNS:
        for match in pattern.finditer(content):
            func_name = match.group(1)
            if func_name in _TS_NON_FUNC_KEYWORDS:
                continue

            start_line = content[: match.start()].count("\n") + 1
            if start_line in seen_lines:
                continue
            seen_lines.add(start_line)

            found_open, end_line = _find_brace_end(lines, start_line)
            if found_open and end_line > start_line:
                functions.append((func_name, start_line, end_line, match.group(2)))

    return functions


def _find_brace_end(lines: list[str], start_line: int) -> tuple[bool, int]:
    """Find the end line of a brace-delimited block starting at start_line."""
    brace_count = 0
    found_open = False
    for j in range(start_line - 1, len(lines)):
        for ch in lines[j]:
            if ch == "{":
                brace_count += 1
                found_open = True
            elif ch == "}":
                brace_count -= 1
        if found_open and brace_count == 0:
            return True, j + 1
    return found_open, len(lines)


def _ts_cyclomatic_complexity(func_body: str) -> int:
    """Calculate cyclomatic complexity for a TypeScript function body."""
    complexity = 1
    for pattern in TS_BRANCH_PATTERNS:
        complexity += len(re.findall(pattern, func_body))
    return complexity


def _ts_max_nesting(func_lines: list[str]) -> int:
    """Calculate max nesting depth for TypeScript function lines."""
    max_depth = 0
    current_depth = 0

    for line in func_lines:
        stripped = line.strip()
        if stripped.startswith("//"):
            continue

        for ch in line:
            if ch == "{":
                current_depth += 1
                max_depth = max(max_depth, current_depth)
            elif ch == "}":
                current_depth -= 1

    return max(0, max_depth - 1)


def _ts_param_count(param_str: str) -> int:
    """Count TypeScript function parameters."""
    param_str = param_str.strip()
    if not param_str:
        return 0

    # Remove type annotations for counting
    # Split by comma at top level (not inside <>)
    depth = 0
    params = []
    current = ""
    for ch in param_str:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "," and depth == 0:
            if current.strip():
                params.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        params.append(current.strip())

    return len(params)


# ── Main validator class ───────────────────────────────────────────────────


def _complexity_fingerprint(thresholds: "ComplexityThresholds") -> str:
    """Phase 64.4: stable hash of the complexity thresholds.

    V14's findings depend on threshold values (e.g. ``cyclomatic_warn``
    going from 10 → 15 means existing complexity-9 functions stop being
    flagged). When a user edits ``.verifiers/config.yaml`` to tighten
    or relax thresholds, the per-file cache MUST invalidate or stale
    findings would persist forever. This fingerprint is stored in the
    cache header; mismatch → cache wipe.

    Defined at module scope so unit tests can call it without
    instantiating the validator.
    """
    bucket = (
        thresholds.cyclomatic_warn,
        thresholds.cyclomatic_error,
        thresholds.cognitive_warn,
        thresholds.cognitive_error,
        thresholds.function_lines_warn,
        thresholds.function_lines_error,
        thresholds.nesting_warn,
        thresholds.params_warn,
    )
    return hashlib.sha256(repr(bucket).encode("utf-8")).hexdigest()


class ComplexityGuardValidator(BaseValidator):
    """V14: Complexity Guard — cyclomatic complexity, function length, nesting, params.

    Thresholds resolve from ``ctx.config.thresholds.complexity`` so a project
    can override defaults via ``.verifiers/config.yaml`` (P1-3 wiring).
    """

    id = "V14-complexity-guard"
    name = "Complexity Guard"
    file_patterns: list[str] = ["**/*.go", "**/*.py", "**/*.ts", "**/*.tsx"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-file complexity analysis (Tier 2)."""
        thresholds = ctx.config.thresholds.complexity
        return self._analyze_file(file_path, thresholds)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide complexity sweep (Tier 3).

        Phase 64.4: load the per-file cache once, pass it through to
        ``_scan_dir`` / ``_analyze_file_cached``, then save once at
        the end. Files whose mtime hasn't changed since the last
        successful scan reuse cached findings.
        """
        thresholds = ctx.config.thresholds.complexity
        fingerprint = _complexity_fingerprint(thresholds)
        cache = PerFileCache.load(ctx.project_root, self.id, config_fingerprint=fingerprint)
        findings = self._scan_all_files(ctx, thresholds, cache)
        cache.save()
        return findings

    def _scan_all_files(
        self,
        ctx: ProjectContext,
        thresholds: ComplexityThresholds,
        cache: PerFileCache,
    ) -> list[Finding]:
        """Scan all source files in the project for complexity issues."""
        findings: list[Finding] = []
        findings.extend(self._scan_dir(ctx, ctx.server_dir, ["*.go"], thresholds, cache))
        findings.extend(self._scan_dir(ctx, ctx.web_dir, ["*.ts", "*.tsx"], thresholds, cache))
        findings.extend(self._scan_dir(ctx, ctx.project_root, ["*.py"], thresholds, cache))
        return findings

    def _scan_dir(
        self,
        ctx: ProjectContext,
        directory: Path | None,
        globs: list[str],
        thresholds: ComplexityThresholds,
        cache: PerFileCache,
    ) -> list[Finding]:
        """Scan a directory with given glob patterns.

        Phase34 (S1 audit): file_path is filtered by both
        ``ctx.is_excluded`` (user's ``exclude.paths`` config — gitignore
        glob) and the validator's hard-coded ``_should_skip`` defaults
        (vendor / node_modules / .gen / __pycache__ / etc). The user
        config gets first crack so noisy projects can opt-out without
        touching validator code.

        Phase 64.4: each file is hashed by mtime against the
        per-file cache before running the language-specific analyzer.
        Cache hit → reuse the prior findings. Miss → analyze + record.
        """
        findings: list[Finding] = []
        if not (directory and directory.exists()):
            return findings
        for glob_pattern in globs:
            for src_file in directory.rglob(glob_pattern):
                fp = str(src_file)
                if ctx.is_excluded(fp):
                    continue
                if self._should_skip(fp):
                    continue
                findings.extend(self._analyze_file_cached(fp, thresholds, cache))
        return findings

    def _analyze_file_cached(
        self,
        file_path: str,
        thresholds: ComplexityThresholds,
        cache: PerFileCache,
    ) -> list[Finding]:
        """Cache-aware wrapper around ``_analyze_file`` (Phase 64.4).

        Looks up ``(file_path, mtime_ns)`` in the cache; on miss runs
        the analyzer and records the result. ``stat`` failures fall
        through to the legacy uncached path so we never silently lose
        a finding because of a missing file.
        """
        try:
            mtime_ns = Path(file_path).stat().st_mtime_ns
        except OSError:
            return self._analyze_file(file_path, thresholds)

        cached = cache.get(file_path, mtime_ns)
        if cached is not None:
            return cached

        fresh = self._analyze_file(file_path, thresholds)
        cache.put(file_path, mtime_ns, fresh)
        return fresh

    def _analyze_file(self, file_path: str, thresholds: ComplexityThresholds | None = None) -> list[Finding]:
        """Route analysis to the correct language-specific analyzer."""
        if file_path.endswith(".py"):
            return _analyze_python_file(file_path, thresholds)
        elif file_path.endswith(".go"):
            return _analyze_go_file(file_path, thresholds)
        elif file_path.endswith((".ts", ".tsx")):
            return _analyze_ts_file(file_path, thresholds)
        return []

    def _should_skip(self, file_path: str) -> bool:
        """Skip generated files, vendor directories, etc."""
        skip_patterns = [
            "vendor/",
            "node_modules/",
            ".gen.",
            "generated",
            "gen/",
            "__pycache__",
            ".venv/",
            "/.config/ranger/commands_full.py",  # Third-party ranger configuration
            ".oh-my-zsh/",  # Third-party zsh plugin files
            ".claude/",  # Claude Code hooks, skills, plugins (third-party)
            "/theme/",  # MUI/CSS theme config (large config objects, not complex logic)
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
    validator = ComplexityGuardValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")
    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
