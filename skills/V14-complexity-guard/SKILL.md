# V14 — complexity-guard

> **Owner**: `hooks/validators/complexity_guard.py`
> **Tier**: 2 (PostToolUse) per-file analysis; 3 (Stop) project-wide sweep that filters via `ctx.is_excluded` plus the built-in noise-dir blacklist.
> **File patterns**: `**/*.go`, `**/*.py`, `**/*.ts`, `**/*.tsx`

## Rules

| Rule ID | Severity | When |
|---|---|---|
| `V14-HIGH-COMPLEXITY` | warning (≥10) / error (≥20) | Cyclomatic complexity exceeds the threshold. Python uses true AST; Go/TS use a heuristic over `if/elif/case/for/while/except/with/&&/||/?:`. |
| `V14-COGNITIVE-COMPLEXITY` | warning (≥15) / error (≥30) | Sonar-style cognitive complexity (nesting weighted heavier than flat branching). Different signal from cyclomatic — high cognitive ≠ high cyclomatic and vice versa. |
| `V14-LONG-FUNCTION` | warning (≥80 lines) / error (≥150) | Function body length. Python's `end_lineno` (Python 3.8+ AST) is exact; Go/TS use balanced-brace counting. |
| `V14-DEEP-NESTING` | warning (≥4) | Maximum nesting depth inside the function. |
| `V14-TOO-MANY-PARAMS` | warning (≥5) | Function parameter count. Python excludes `self` / `cls`. |

All five thresholds are configurable in `.verifiers/config.yaml`:

```yaml
thresholds:
  complexity:
    cyclomatic_warn: 10
    cyclomatic_error: 20
    cognitive_warn: 15
    cognitive_error: 30
    function_lines_warn: 80
    function_lines_error: 150
    nesting_warn: 4
    params_warn: 5
```

## Why this verifier exists

A function that scores 25 cyclomatic / 35 cognitive / 200 lines is **almost impossible to test thoroughly** — every branch combination explodes. That kind of function is also disproportionately the source of bugs (correlation well-established in software-engineering empirics).

The pragmatic stance V14 takes: surface the metric *as soon as the function is written*, while it's still small psychological effort to refactor. Once the function ships and accretes call sites, the cost of breaking it up climbs steeply.

Five complementary metrics because each catches a different smell:
- **Cyclomatic** — branch density. Captures "many independent decisions".
- **Cognitive** — nesting penalty. Captures "deeply pyramidal logic" even when branch count is low.
- **Length** — captures "doing too many distinct things in sequence".
- **Nesting** — captures "you'll get lost reading this".
- **Params** — captures "this function is taking on too much surface".

## Design rationale

- **Two-tier scan: per-file (Tier 2) + project-wide (Tier 3).** Tier 2 catches the function the user just edited. Tier 3 sweeps the whole repo so an old function that drifted past threshold (because someone added one more `if`) is also surfaced.
- **AST for Python, regex for Go/TS.** Python's `ast` module gives exact `end_lineno` and decision-point counts. Go/TS would require `go/parser` or `@swc/parser` for the same precision; V14 trades precision for portability. The heuristic is well-tuned in practice — false-positive rate is low.
- **Project-level scan respects user excludes.** Phase34's `ctx.is_excluded` integration means `vendor/`, `.gen/`, `node_modules/`, etc. are skipped by user config first, then the built-in `_should_skip` for stragglers (`.config/ranger/commands_full.py` etc.).
- **Warn before error.** Two-level severity prevents "I edited a single line; now V14 hard-fails" — the user sees the warning at 10 and has time to refactor before hitting 20.
- **Threshold knobs.** A library with intricate combinator code legitimately has higher cyclomatic baseline than a CRUD app. Per-project knobs survive that gracefully.

## How it checks (implementation)

Lives in `hooks/validators/complexity_guard.py`.

### Python (true AST)

```python
import ast

def _analyze_python_file(file_path, thresholds):
    tree = ast.parse(file_path.read_text())
    findings: list[Finding] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # 1. Cyclomatic — count decision points
        cyclomatic = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.AsyncFor)):
                cyclomatic += 1
            elif isinstance(child, ast.BoolOp):
                cyclomatic += len(child.values) - 1
            elif isinstance(child, ast.Try):
                cyclomatic += len(child.handlers)
            elif isinstance(child, ast.IfExp):     # ternary
                cyclomatic += 1
            elif isinstance(child, ast.With) and len(child.items) > 1:
                cyclomatic += len(child.items) - 1

        # 2. Cognitive — nesting-weighted
        cognitive = _cognitive_complexity_python(node)

        # 3. Length (uses Python 3.8+ end_lineno)
        length = node.end_lineno - node.lineno + 1

        # 4. Nesting depth
        max_depth = _max_nesting_depth(node)

        # 5. Params (exclude self/cls)
        params = [a for a in node.args.args if a.arg not in ("self", "cls")]
        param_count = len(params) + len(node.args.kwonlyargs) + len(node.args.posonlyargs)

        findings.extend(_emit_metrics(
            file_path, node, cyclomatic, cognitive, length, max_depth, param_count, thresholds
        ))
    return findings
```

### Go / TS (heuristic)

```python
def _analyze_go_file(file_path, thresholds):
    src = file_path.read_text()
    findings: list[Finding] = []

    # Find function bodies via balanced-brace tracking
    for func_match in re.finditer(
        r'^func(?:\s+\([^)]+\))?\s+(\w+)\s*\([^)]*\)[^{]*\{',
        src, re.MULTILINE,
    ):
        body, end_line = _extract_balanced_body(src, func_match.end())
        start_line = src[:func_match.start()].count("\n") + 1

        # Cyclomatic — heuristic
        cyclomatic = (
            1
            + len(re.findall(r'\b(if|for|switch|case|select)\b', body))
            + len(re.findall(r'&&|\|\|', body))
        )

        # Cognitive — same as cyclomatic + nesting bonus
        cognitive = cyclomatic + _nesting_bonus_braces(body)

        # Length
        length = end_line - start_line + 1

        # Nesting from balanced-brace track
        nesting = _max_brace_depth(body)

        # Params
        params = _count_go_params(func_match.group(0))

        findings.extend(_emit_metrics(...))
    return findings
```

TypeScript / TSX uses an analogous balanced-brace scanner with a slightly different function-prefix regex (`function`, `const X = (...) =>`, method shorthand).

### Project-level sweep

```python
def validate_project(self, ctx):
    thresholds = ctx.config.thresholds.complexity
    return self._scan_all_files(ctx, thresholds)

def _scan_all_files(self, ctx, thresholds):
    findings: list[Finding] = []
    findings.extend(self._scan_dir(ctx, ctx.server_dir, ["*.go"], thresholds))
    findings.extend(self._scan_dir(ctx, ctx.web_dir, ["*.ts", "*.tsx"], thresholds))
    findings.extend(self._scan_dir(ctx, ctx.project_root, ["*.py"], thresholds))
    return findings

def _scan_dir(self, ctx, directory, globs, thresholds):
    if not (directory and directory.exists()):
        return []
    findings: list[Finding] = []
    for glob in globs:
        for src_file in directory.rglob(glob):
            fp = str(src_file)
            if ctx.is_excluded(fp):       # Phase34 (S1): user config first
                continue
            if self._should_skip(fp):      # built-in noise dirs
                continue
            findings.extend(self._analyze_file(fp, thresholds))
    return findings
```

### Could be more effective

- **Real Go / TS AST.** `go/parser` and `@swc/parser` would replace the heuristic and eliminate edge cases (multi-line lambdas, generic methods). Cost: Python↔Go bridge or a Node sidecar.
- **Halstead complexity.** A second per-language metric capturing operator/operand variety. Often correlates with bug density better than cyclomatic. Modest implementation cost given the AST is already walked.
- **Function call-graph integration.** A simple function that *calls* a complex one is itself complex in practice. A "transitive cyclomatic" metric (cyclomatic + max(callees.cyclomatic) up to N hops) would surface the systemic complexity that single-function metrics miss.
- **Auto-extract suggestion.** Given a function that crosses cyclomatic 20, V14 could identify the natural cut points (each top-level `if` block becomes a candidate for `extractMethod`). Heuristic but actionable.
- **Trend tracking.** "This function was 14 last week, 22 this week" is a meaningful signal a single-snapshot scan misses. Persisting per-function cyclomatic in `.verifiers/state/` would unlock it.

## References

- [Thomas J. McCabe — *A Complexity Measure*](https://www.literateprogramming.com/mccabe.pdf) — IEEE Transactions on Software Engineering, *published 1976*, retrieved 2026-04-30. The original cyclomatic-complexity paper.
- [SonarSource — Cognitive Complexity (white paper)](https://www.sonarsource.com/docs/CognitiveComplexity.pdf) — SonarSource, *published 2018-03*, retrieved 2026-04-30. Source of the V14-COGNITIVE-COMPLEXITY metric and the nesting-weighted formula.
- [Robert C. Martin — *Clean Code*, ch. 3 (Functions)](https://www.oreilly.com/library/view/clean-code/9780136083238/) — Robert C. Martin, *published 2008*, retrieved 2026-04-30. Source of the "small functions" principle V14 enforces.
- [Python — `ast` module, AST node `end_lineno`](https://docs.python.org/3/library/ast.html) — Python team, *added in 3.8, continuously updated*, retrieved 2026-04-30. The exact-line-count Python implementation V14 uses.
- [SEI / CMU — Empirical findings on complexity-defect correlation](https://insights.sei.cmu.edu/library/empirical-evaluation-of-defect-projection-models-for-widely-deployed-production-software-systems/) — SEI, *published 2004*, retrieved 2026-04-30. Background data on why high cyclomatic correlates with bug density.

## Examples

### ✓ Pass

```python
def find_user_by_email(repo, email):
    user = repo.get_by_email(email)
    if not user:
        return None
    if not user.is_active:
        return None
    return user
# cyclomatic=3, cognitive=3, length=6, nesting=1, params=2
```

### ✗ Fail

```python
def process_order(order, user, region, plan, coupons, taxes, shipping):  # → V14-TOO-MANY-PARAMS (7 ≥ 5)
    if order.status != "pending":                              # nesting 1
        if user.is_premium:                                    # nesting 2
            for coupon in coupons:                             # nesting 3
                if coupon.region == region:                    # nesting 4 → V14-DEEP-NESTING
                    if coupon.expires > now():
                        if coupon.applies_to(order):
                            ...  # … keeps going for 90 more lines → V14-LONG-FUNCTION
    # cyclomatic ~ 18 → V14-HIGH-COMPLEXITY (warning at 10, error at 20)
    # cognitive ~ 30 → V14-COGNITIVE-COMPLEXITY (error at 30)
```
