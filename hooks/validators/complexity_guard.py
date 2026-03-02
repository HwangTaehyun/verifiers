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
import re
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import (
    BaseValidator,
    Finding,
    ValidationResult,
    format_output,
    read_hook_input,
    write_hook_output,
)
from lib.project_context import ProjectContext

# ── Thresholds ──────────────────────────────────────────────────────────────

COMPLEXITY_WARN = 10
COMPLEXITY_ERROR = 20

LENGTH_WARN = 50
LENGTH_ERROR = 100

NESTING_WARN = 4

PARAMS_WARN = 5

# Cognitive complexity (Sonar-style: penalizes nesting)
COGNITIVE_WARN = 15
COGNITIVE_ERROR = 30


# ── Python analysis (AST-based) ────────────────────────────────────────────


def _analyze_python_file(file_path: str) -> list[Finding]:
    """Analyze a Python file using the ast module for precise metrics."""
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
            func_name = node.name
            start_line = node.lineno

            # ── Complexity ──
            complexity = _python_cyclomatic_complexity(node)
            if complexity > COMPLEXITY_ERROR:
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V14-HIGH-COMPLEXITY",
                        message=f"Function '{func_name}' has cyclomatic complexity {complexity} (max {COMPLEXITY_ERROR})",
                        fix=f"Refactor '{func_name}' at {file_path}:{start_line} into smaller functions. Extract complex conditional logic.",
                        line=start_line,
                    )
                )
            elif complexity > COMPLEXITY_WARN:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V14-HIGH-COMPLEXITY",
                        message=f"Function '{func_name}' has cyclomatic complexity {complexity} (recommended max {COMPLEXITY_WARN})",
                        fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}. Extract conditionals into helper functions.",
                        line=start_line,
                    )
                )

            # ── Cognitive Complexity ──
            cognitive = _python_cognitive_complexity(node)
            if cognitive > COGNITIVE_ERROR:
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V14-COGNITIVE-COMPLEXITY",
                        message=f"Function '{func_name}' has cognitive complexity {cognitive} (max {COGNITIVE_ERROR})",
                        fix=f"Simplify '{func_name}' at {file_path}:{start_line}. Reduce nesting and break into smaller functions.",
                        line=start_line,
                    )
                )
            elif cognitive > COGNITIVE_WARN:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V14-COGNITIVE-COMPLEXITY",
                        message=f"Function '{func_name}' has cognitive complexity {cognitive} (recommended max {COGNITIVE_WARN})",
                        fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}. Nested conditions increase cognitive load.",
                        line=start_line,
                    )
                )

            # ── Length ──
            end_line = getattr(node, "end_lineno", None)
            if end_line:
                length = end_line - start_line + 1
                if length > LENGTH_ERROR:
                    findings.append(
                        Finding(
                            severity="error",
                            file=file_path,
                            rule="V14-LONG-FUNCTION",
                            message=f"Function '{func_name}' is {length} lines long (max {LENGTH_ERROR})",
                            fix=f"Break '{func_name}' at {file_path}:{start_line} into smaller functions. Each should do one thing.",
                            line=start_line,
                        )
                    )
                elif length > LENGTH_WARN:
                    findings.append(
                        Finding(
                            severity="warning",
                            file=file_path,
                            rule="V14-LONG-FUNCTION",
                            message=f"Function '{func_name}' is {length} lines long (recommended max {LENGTH_WARN})",
                            fix=f"Consider splitting '{func_name}' at {file_path}:{start_line} into smaller functions.",
                            line=start_line,
                        )
                    )

            # ── Nesting ──
            max_depth = _python_max_nesting(node)
            if max_depth > NESTING_WARN:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V14-DEEP-NESTING",
                        message=f"Function '{func_name}' has nesting depth {max_depth} (max {NESTING_WARN})",
                        fix=f"Reduce nesting in '{func_name}' at {file_path}:{start_line}. Use early returns, guard clauses, or extract helper functions.",
                        line=start_line,
                    )
                )

            # ── Params ──
            params = _python_param_count(node)
            if params > PARAMS_WARN:
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V14-TOO-MANY-PARAMS",
                        message=f"Function '{func_name}' has {params} parameters (max {PARAMS_WARN})",
                        fix=f"Reduce parameters for '{func_name}' at {file_path}:{start_line}. Use a dataclass/dict for grouped parameters.",
                        line=start_line,
                    )
                )

    return findings


def _python_cyclomatic_complexity(node: ast.AST) -> int:
    """Calculate cyclomatic complexity of a Python function.

    Counts: if, elif, for, while, except, with, and, or, assert (condition part),
    comprehension ifs, ternary expressions.
    Base complexity is 1.
    """
    complexity = 1

    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.IfExp)):
            complexity += 1
        elif isinstance(child, (ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(child, (ast.While,)):
            complexity += 1
        elif isinstance(child, ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, (ast.With, ast.AsyncWith)):
            complexity += 1
        elif isinstance(child, ast.Assert):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            # `and` / `or` — each operator adds 1
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


def _analyze_go_file(file_path: str) -> list[Finding]:
    """Analyze a Go file using regex-based heuristics."""
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
        if complexity > COMPLEXITY_ERROR:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (max {COMPLEXITY_ERROR})",
                    fix=f"Refactor '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif complexity > COMPLEXITY_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (recommended max {COMPLEXITY_WARN})",
                    fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Length ──
        length = end_line - start_line + 1
        if length > LENGTH_ERROR:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (max {LENGTH_ERROR})",
                    fix=f"Break '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif length > LENGTH_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (recommended max {LENGTH_WARN})",
                    fix=f"Consider splitting '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Nesting ──
        max_depth = _go_max_nesting(func_lines)
        if max_depth > NESTING_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-DEEP-NESTING",
                    message=f"Function '{func_name}' has nesting depth {max_depth} (max {NESTING_WARN})",
                    fix=f"Reduce nesting in '{func_name}' at {file_path}:{start_line}. Use early returns or guard clauses.",
                    line=start_line,
                )
            )

        # ── Params ──
        params = _go_param_count(param_str)
        if params > PARAMS_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-TOO-MANY-PARAMS",
                    message=f"Function '{func_name}' has {params} parameters (max {PARAMS_WARN})",
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
    re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*(?::\s*[^=]+)?\s*=>", re.MULTILINE),
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


def _analyze_ts_file(file_path: str) -> list[Finding]:
    """Analyze a TypeScript file using regex-based heuristics."""
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
        if complexity > COMPLEXITY_ERROR:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (max {COMPLEXITY_ERROR})",
                    fix=f"Refactor '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif complexity > COMPLEXITY_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-HIGH-COMPLEXITY",
                    message=f"Function '{func_name}' has cyclomatic complexity {complexity} (recommended max {COMPLEXITY_WARN})",
                    fix=f"Consider simplifying '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Length ──
        length = end_line - start_line + 1
        if length > LENGTH_ERROR:
            findings.append(
                Finding(
                    severity="error",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (max {LENGTH_ERROR})",
                    fix=f"Break '{func_name}' at {file_path}:{start_line} into smaller functions.",
                    line=start_line,
                )
            )
        elif length > LENGTH_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-LONG-FUNCTION",
                    message=f"Function '{func_name}' is {length} lines long (recommended max {LENGTH_WARN})",
                    fix=f"Consider splitting '{func_name}' at {file_path}:{start_line}.",
                    line=start_line,
                )
            )

        # ── Nesting ──
        max_depth = _ts_max_nesting(func_lines)
        if max_depth > NESTING_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-DEEP-NESTING",
                    message=f"Function '{func_name}' has nesting depth {max_depth} (max {NESTING_WARN})",
                    fix=f"Reduce nesting in '{func_name}' at {file_path}:{start_line}. Use early returns or guard clauses.",
                    line=start_line,
                )
            )

        # ── Params ──
        params = _ts_param_count(param_str)
        if params > PARAMS_WARN:
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    rule="V14-TOO-MANY-PARAMS",
                    message=f"Function '{func_name}' has {params} parameters (max {PARAMS_WARN})",
                    fix=f"Reduce parameters for '{func_name}' at {file_path}:{start_line}. Use an options object instead.",
                    line=start_line,
                )
            )

    return findings


def _ts_find_functions(lines: list[str], content: str) -> list[tuple[str, int, int, str]]:
    """Find TypeScript function boundaries.

    Returns list of (func_name, start_line, end_line, param_string).
    """
    functions: list[tuple[str, int, int, str]] = []
    seen_lines: set[int] = set()

    for pattern in TS_FUNC_PATTERNS:
        for match in pattern.finditer(content):
            func_name = match.group(1)
            param_str = match.group(2)

            # Skip common non-function matches
            if func_name in ("if", "for", "while", "switch", "catch", "import", "from", "return", "new"):
                continue

            # Find line number of match
            start_pos = match.start()
            start_line = content[:start_pos].count("\n") + 1

            if start_line in seen_lines:
                continue
            seen_lines.add(start_line)

            # Find function body end by brace counting
            brace_count = 0
            found_open = False
            end_line = start_line

            for j in range(start_line - 1, len(lines)):
                for ch in lines[j]:
                    if ch == "{":
                        brace_count += 1
                        found_open = True
                    elif ch == "}":
                        brace_count -= 1

                if found_open and brace_count == 0:
                    end_line = j + 1
                    break
            else:
                end_line = len(lines)

            # Only include if we actually found a body
            if found_open and end_line > start_line:
                functions.append((func_name, start_line, end_line, param_str))

    return functions


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


class ComplexityGuardValidator(BaseValidator):
    """V14: Complexity Guard — cyclomatic complexity, function length, nesting, params."""

    id = "V14-complexity-guard"
    name = "Complexity Guard"
    file_patterns: list[str] = ["**/*.go", "**/*.py", "**/*.ts", "**/*.tsx"]

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        if file_path:
            findings.extend(self._analyze_file(file_path))
        elif mode == "stop":
            # In stop mode, analyze all source files
            if ctx.server_dir and ctx.server_dir.exists():
                for go_file in ctx.server_dir.rglob("*.go"):
                    if self._should_skip(str(go_file)):
                        continue
                    findings.extend(self._analyze_file(str(go_file)))

            if ctx.web_dir and ctx.web_dir.exists():
                for ext in ("*.ts", "*.tsx"):
                    for ts_file in ctx.web_dir.rglob(ext):
                        if self._should_skip(str(ts_file)):
                            continue
                        findings.extend(self._analyze_file(str(ts_file)))

            # Python files in project root
            for py_file in ctx.project_root.rglob("*.py"):
                if self._should_skip(str(py_file)):
                    continue
                findings.extend(self._analyze_file(str(py_file)))

        return ValidationResult(validator_id=self.id, findings=findings)

    def _analyze_file(self, file_path: str) -> list[Finding]:
        """Route analysis to the correct language-specific analyzer."""
        if file_path.endswith(".py"):
            return _analyze_python_file(file_path)
        elif file_path.endswith(".go"):
            return _analyze_go_file(file_path)
        elif file_path.endswith((".ts", ".tsx")):
            return _analyze_ts_file(file_path)
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
