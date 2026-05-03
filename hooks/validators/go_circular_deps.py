"""V80: Go circular package dependencies — pure-Python static analysis.

The Go compiler rejects direct circular imports at build time, but a
PR introducing a cycle still wastes the user's CI minutes (and local
``go build`` time) to surface what verifier could flag at Stop time.
Beyond the build-time win, V80 catches:

  - **interface-mediated indirect cycles** that the compiler accepts
    in isolation but trip later refactors
  - **3+ package cycles** that are hard to see manually in a graph
    review
  - **non-Go-build pipelines** (cgo-only, replace-directives) where
    the compiler isn't authoritative

Algorithm (no subprocess required):

  1. Locate ``go.mod`` (server/go.mod first, project root fallback)
  2. Read the ``module`` directive — internal imports start with this
     prefix
  3. For every .go file in the module, parse imports (single-line
     and block forms)
  4. Build a directed graph keyed on **package import path** (a
     directory's package is one node — multiple files contribute to
     the same node's edge set)
  5. Run Tarjan's SCC (iterative, no recursion-depth risk)
  6. Filter SCCs with size > 1 → these are cycles

Rules:
  - V80-CIRCULAR-DEPS — package cycle detected (warning).

Test files (``_test.go``) are excluded since test helpers may
legitimately depend on prod packages while prod tests use them, and
a separate test-only cycle isn't fatal at runtime.

Reference: [Go spec — Import declarations](https://go.dev/ref/spec#Import_declarations)
(continuously updated). [Tarjan's SCC algorithm](https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm)
(foundational, R. E. Tarjan 1972). [madge](https://github.com/pahen/madge)
(continuously developed since 2014) — TS equivalent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

RE_GO_MOD_MODULE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
RE_IMPORT_SINGLE = re.compile(r'^\s*import\s+(?:\w+\s+)?"([^"]+)"\s*(?://.*)?$')
RE_IMPORT_BLOCK_START = re.compile(r"^\s*import\s*\(\s*(?://.*)?$")
RE_IMPORT_BLOCK_PATH = re.compile(r'^\s*(?:(?:\w+|_|\.)\s+)?"([^"]+)"\s*(?://.*)?$')

_SKIP_FILE_SUFFIX = "_test.go"


def _read_module_name(go_mod: Path) -> str | None:
    try:
        text = go_mod.read_text(errors="replace")
    except OSError:
        return None
    m = RE_GO_MOD_MODULE.search(text)
    return m.group(1) if m else None


def _parse_imports(src: str) -> list[str]:
    out: list[str] = []
    in_block = False
    for line in src.splitlines():
        stripped = line.strip()
        if not in_block:
            m = RE_IMPORT_SINGLE.match(stripped)
            if m:
                out.append(m.group(1))
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
                out.append(m.group(1))
    return out


def _tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan SCC. Returns SCCs of size > 1 (cycles)."""
    index_counter = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    cycles: list[list[str]] = []

    nodes = list(graph.keys())

    for start in nodes:
        if start in indices:
            continue
        # Iterative DFS frame stack: each entry is (node, neighbors_iter, parent)
        indices[start] = index_counter
        lowlinks[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack.add(start)
        work: list[tuple[str, list[str], int, str | None]] = [
            (start, sorted(graph.get(start, set())), 0, None)
        ]

        while work:
            node, neighbors, idx, parent = work[-1]
            if idx < len(neighbors):
                nb = neighbors[idx]
                work[-1] = (node, neighbors, idx + 1, parent)
                if nb not in indices:
                    indices[nb] = index_counter
                    lowlinks[nb] = index_counter
                    index_counter += 1
                    stack.append(nb)
                    on_stack.add(nb)
                    work.append((nb, sorted(graph.get(nb, set())), 0, node))
                elif nb in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[nb])
            else:
                # Backtrack
                if lowlinks[node] == indices[node]:
                    scc: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.append(w)
                        if w == node:
                            break
                    if len(scc) > 1:
                        cycles.append(sorted(scc))
                work.pop()
                if parent is not None:
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[node])
    return cycles


class GoCircularDepsValidator(BaseValidator):
    """V80: detect Go package import cycles via Tarjan SCC."""

    id = "V80-go-circular-deps"
    name = "Go Circular Dependencies"
    file_patterns: list[str] = []  # Stop-only — graph is project-wide.

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        # Locate go.mod (server-first, then project root).
        go_mod: Path | None = None
        if ctx.server_dir is not None and (ctx.server_dir / "go.mod").is_file():
            go_mod = ctx.server_dir / "go.mod"
        elif (ctx.project_root / "go.mod").is_file():
            go_mod = ctx.project_root / "go.mod"
        if go_mod is None:
            return []  # No Go module — silent.

        module_name = _read_module_name(go_mod)
        if not module_name:
            return []
        module_dir = go_mod.parent.resolve()

        graph: dict[str, set[str]] = {}
        # Walk all .go files under module_dir.
        for go_file in ctx.file_index.find_by_pattern("*.go"):
            try:
                resolved = go_file.resolve()
                rel = resolved.relative_to(module_dir)
            except (ValueError, OSError):
                continue
            if str(go_file).endswith(_SKIP_FILE_SUFFIX):
                continue
            # Package import path = module + "/" + dirname(rel) (no trailing slash).
            parent = str(rel.parent).replace("\\", "/")
            pkg = module_name if parent in ("", ".") else f"{module_name}/{parent}"

            try:
                src = go_file.read_text(errors="replace")
            except OSError:
                continue
            imports = _parse_imports(src)
            edges = graph.setdefault(pkg, set())
            for imp in imports:
                if imp == pkg:
                    continue  # Self-package import — Go compiler allows but rare.
                if imp == module_name or imp.startswith(module_name + "/"):
                    edges.add(imp)

        if not graph:
            return []

        cycles = _tarjan_scc(graph)
        if not cycles:
            return []

        findings: list[Finding] = []
        for cycle in cycles:
            cycle_path = " → ".join(cycle + [cycle[0]])
            findings.append(
                Finding(
                    severity="warning",
                    file=str(module_dir),
                    rule="V80-CIRCULAR-DEPS",
                    message=(
                        f"Go package cycle detected ({len(cycle)} packages): {cycle_path}. "
                        "Cycles cause `go build` failure; even when refactored to compile, "
                        "they signal layer leakage that breaks future evolution."
                    ),
                    fix=(
                        "Break the cycle by extracting the shared interface or type into a "
                        "third package that both originals depend on. Common patterns:\n"
                        "  (a) Move the shared interface to `<pkg>/contract` or `<pkg>/types`\n"
                        "  (b) Invert the dependency — let one side accept the other's "
                        "interface as a parameter instead of importing\n"
                        "  (c) If the cycle reflects two genuinely-coupled concerns, merge "
                        "them into one package"
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
    validator = GoCircularDepsValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
