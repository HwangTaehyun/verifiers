"""V76: React Hook Form ↔ Zod schema sync — single-file heuristic.

[React Hook Form](https://react-hook-form.com/get-started#SchemaValidation)
(continuously updated, retrieved 2026-05-03) lets you wire a Zod schema
into the form via ``zodResolver``. The form's TypeScript generic
``useForm<T>(...)`` should align with the schema: if ``T`` is defined
independently from the schema, the two can drift, and ``form.register('x')``
silently registers fields the schema doesn't validate. Production data
flows through unchecked.

V76 enforces the canonical pattern within a single file:

    const userSchema = z.object({ email: z.string(), name: z.string() });
    type FormData = z.infer<typeof userSchema>;     // ← single source of truth
    const form = useForm<FormData>({ resolver: zodResolver(userSchema) });

Rules:
  - V76-RHF-SCHEMA-MISMATCH — useForm<T>(zodResolver(S1)) but
    `type T = z.infer<typeof S2>` and S1 != S2 (error).
  - V76-RHF-NOT-FROM-INFER — useForm<T>(zodResolver(S)) and T is defined
    as a plain ``type T = {...}`` literal in the same file rather than
    ``z.infer<typeof S>`` (error).

Out of scope: T imported from another file. Detecting that requires
cross-file TS analysis; v1 stays single-file. If T isn't defined in
this file at all, V76 silent-passes (no false-positive).

Generated files (``.gen.``, ``__generated__``, ``dist/``, ``build/``,
``.next/``, ``node_modules/``) are excluded.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# ``useForm<T>({ ... resolver: zodResolver(S) ... })`` — extracts (T, S).
# Uses ``[\s\S]*?`` to span newlines/braces non-greedily.
RE_USEFORM_ZOD = re.compile(
    r"useForm<(\w+)>\s*\(\s*\{[\s\S]*?resolver\s*:\s*zodResolver\s*\(\s*(\w+)\s*\)",
)

# ``type T = z.infer<typeof S>`` — extracts (T, S).
RE_ZINFER_TYPE = re.compile(
    r"type\s+(\w+)\s*=\s*z\.infer<\s*typeof\s+(\w+)\s*>",
)

# ``type T = { ... }`` or ``interface T { ... }`` — type names defined as
# plain literals in this file (suggesting drift potential). ``\b`` boundary
# allows declarations that follow a semicolon on the same line, not just
# newline-anchored ones.
RE_TYPE_LITERAL = re.compile(
    r"\b(?:type\s+(\w+)\s*=\s*\{|interface\s+(\w+)\s*(?:extends\s+\w+\s*)?\{)",
)

_EXCLUDE_HINTS: tuple[str, ...] = (
    ".gen.",
    "__generated__",
    "/dist/",
    "/build/",
    "/.next/",
    "/node_modules/",
)


class RhfZodSchemaSyncValidator(BaseValidator):
    """V76: enforce useForm<T> ↔ z.infer<typeof S> sync inside a single file."""

    id = "V76-rhf-zod-schema-sync"
    name = "RHF ↔ Zod Schema Sync"
    file_patterns: list[str] = ["**/*.ts", "**/*.tsx"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        if not path.is_file() or any(h in file_path for h in _EXCLUDE_HINTS):
            return []
        return self._scan_file(path)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        findings: list[Finding] = []
        for ts_file in ctx.file_index.find_by_pattern("*.ts", "*.tsx"):
            if any(h in str(ts_file) for h in _EXCLUDE_HINTS):
                continue
            findings.extend(self._scan_file(ts_file))
        return findings

    def _scan_file(self, file_path: Path) -> list[Finding]:
        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []

        # Cheap fast-path: if the file doesn't even mention useForm + zodResolver,
        # skip the more expensive multi-pattern scan.
        if "useForm" not in src or "zodResolver" not in src:
            return []

        # Build the z.infer map: T → S
        zinfer_map = {m.group(1): m.group(2) for m in RE_ZINFER_TYPE.finditer(src)}
        # Plain type literals defined in this file.
        type_literal_names: set[str] = set()
        for m in RE_TYPE_LITERAL.finditer(src):
            name = m.group(1) or m.group(2)
            if name:
                type_literal_names.add(name)

        findings: list[Finding] = []
        for m in RE_USEFORM_ZOD.finditer(src):
            t_name = m.group(1)
            s_resolver = m.group(2)
            line_no = src.count("\n", 0, m.start()) + 1

            if t_name in zinfer_map:
                s_inferred = zinfer_map[t_name]
                if s_inferred != s_resolver:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(file_path),
                            line=line_no,
                            rule="V76-RHF-SCHEMA-MISMATCH",
                            message=(
                                f"useForm<{t_name}> uses zodResolver({s_resolver}) but "
                                f"{t_name} is defined as z.infer<typeof {s_inferred}>. "
                                "Form fields validated by the resolver may not match the "
                                "TypeScript shape — drift between schema and type."
                            ),
                            fix=(
                                f"Either change `useForm<{t_name}>(zodResolver({s_resolver}))` "
                                f"to use {s_inferred}, or redefine the type as "
                                f"`type {t_name} = z.infer<typeof {s_resolver}>`. "
                                "Pick the schema you trust as the source of truth."
                            ),
                        )
                    )
            elif t_name in type_literal_names:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(file_path),
                        line=line_no,
                        rule="V76-RHF-NOT-FROM-INFER",
                        message=(
                            f"useForm<{t_name}> with zodResolver({s_resolver}), but "
                            f"{t_name} is defined as a plain type/interface literal. "
                            "Drift between the schema and type lets unvalidated fields "
                            "register silently."
                        ),
                        fix=(
                            f"Replace the type definition with "
                            f"`type {t_name} = z.infer<typeof {s_resolver}>` so the schema "
                            "is the single source of truth. The schema gains a field → the "
                            "type sees it automatically."
                        ),
                    )
                )
            # else: T not defined in this file → likely imported. Silent.
        return findings


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = RhfZodSchemaSyncValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
