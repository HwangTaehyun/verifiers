"""V77: React Hook Form defaultValues type-match — same-file heuristic.

When ``useForm<FormData>({ defaultValues: { email: '' } })`` declares
``FormData`` with more fields than ``defaultValues`` covers, RHF starts
those inputs as *uncontrolled* and switches to *controlled* on first
keystroke — that's the canonical "A component is changing an
uncontrolled input to be controlled" warning. The form's
``form.reset()`` also misses fields that have no default, so closing
and reopening the form leaves stale data.

V77 enforces that ``defaultValues`` covers every key declared in the
``useForm<T>`` generic, where ``T`` is defined in the same file.

Rules:
  - V77-RHF-DEFAULTS-INCOMPLETE — defaultValues missing one or more
    keys present in T (error)

Out of scope: T imported from another file, ``Partial<T>`` generic,
nested key paths (``user.address.city``). v1 stays single-file +
top-level key.

Reference: [React Hook Form `defaultValues` API](https://react-hook-form.com/docs/useform#defaultValues)
(continuously updated, retrieved 2026-05-03). [React "Controlling an
input with state"](https://react.dev/reference/react-dom/components/input#controlling-an-input-with-a-state-variable).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

RE_USEFORM = re.compile(r"useForm<(\w+)>\s*\(")
RE_DEFAULT_VALUES = re.compile(r"\bdefaultValues\s*:\s*")
RE_ZINFER_TYPE = re.compile(r"type\s+(\w+)\s*=\s*z\.infer<\s*typeof\s+(\w+)\s*>")
RE_TYPE_LITERAL = re.compile(r"\btype\s+(\w+)\s*=\s*\{")
RE_INTERFACE = re.compile(r"\binterface\s+(\w+)(?:\s+extends\s+\w+)?\s*\{")
RE_ZOD_OBJECT_DECL = re.compile(r"\b(?:const|let|var)\s+(\w+)\s*=\s*z\.object\s*\(\s*\{")
# An identifier optionally followed by `?` then `:` — for top-level keys.
RE_TOPLEVEL_KEY = re.compile(r"(?:^|[,;{(\s])(\w+)\s*\??\s*:")

_EXCLUDE_HINTS: tuple[str, ...] = (
    ".gen.",
    "__generated__",
    "/dist/",
    "/build/",
    "/.next/",
    "/node_modules/",
)


def _extract_balanced(src: str, brace_pos: int) -> tuple[str, int]:
    """Return (body_inside_braces, position_after_close) for a ``{...}``
    block starting at ``brace_pos`` (which must point at ``{``). Returns
    ``("", -1)`` on imbalance.
    """
    if brace_pos >= len(src) or src[brace_pos] != "{":
        return "", -1
    depth = 1
    j = brace_pos + 1
    while j < len(src) and depth > 0:
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        j += 1
    if depth != 0:
        return "", -1
    return src[brace_pos + 1 : j - 1], j


def _strip_nested(text: str) -> str:
    """Remove nested {...}, [...], (...) regions from ``text`` so a
    flat regex can find top-level identifiers without descending into
    sub-objects, function args, etc.
    """
    out: list[str] = []
    brace = bracket = paren = 0
    in_str: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and i + 1 < len(text):
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            i += 1
            continue
        if ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket -= 1
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif brace == 0 and bracket == 0 and paren == 0:
            out.append(ch)
        i += 1
    return "".join(out)


def _top_level_keys(body: str) -> set[str]:
    """Extract top-level identifier keys from a ``{...}`` body.

    Strips nested objects/arrays/calls, then matches ``identifier:`` (or
    ``identifier?:`` for optional fields).
    """
    flat = _strip_nested(body)
    return {m.group(1) for m in RE_TOPLEVEL_KEY.finditer(flat)}


def _find_defaults_body(src: str, useform_match_end: int) -> str | None:
    """Starting at the end of a ``useForm<T>(`` match, find the
    ``defaultValues: { ... }`` body within the same call expression.
    Returns the inner body string, or None if defaultValues is absent.
    """
    # Locate `defaultValues:` within the next ~2000 chars (heuristic).
    window_end = min(len(src), useform_match_end + 2000)
    sub = src[useform_match_end:window_end]
    m = RE_DEFAULT_VALUES.search(sub)
    if not m:
        return None
    # Skip whitespace after `defaultValues:` to find the next `{`
    pos = useform_match_end + m.end()
    while pos < len(src) and src[pos] in " \t\n\r":
        pos += 1
    if pos >= len(src) or src[pos] != "{":
        return None
    body, _end = _extract_balanced(src, pos)
    return body if _end != -1 else None


def _type_keys(src: str, type_name: str) -> set[str] | None:
    """Find the keys defined by ``type type_name`` in ``src``.

    Resolution order:
      1. ``type T = z.infer<typeof S>`` → keys come from ``z.object({...})``
         body for ``S`` in this file
      2. ``type T = { ... }`` → keys from the literal body
      3. ``interface T { ... }`` (or ``interface T extends X { ... }``) → keys
         from the body

    Returns ``None`` if no in-file definition was found (caller should
    silent-pass). Returns an empty set if a definition was found but
    no keys were extractable (rare but should not fire).
    """
    # 1. z.infer<typeof S>
    for m in RE_ZINFER_TYPE.finditer(src):
        if m.group(1) == type_name:
            schema_name = m.group(2)
            decl = re.search(
                r"\b(?:const|let|var)\s+" + re.escape(schema_name) + r"\s*=\s*z\.object\s*\(\s*\{",
                src,
            )
            if not decl:
                return set()
            body, _end = _extract_balanced(src, decl.end() - 1)
            return _top_level_keys(body) if _end != -1 else set()

    # 2. type T = { ... }
    for m in RE_TYPE_LITERAL.finditer(src):
        if m.group(1) == type_name:
            body, _end = _extract_balanced(src, m.end() - 1)
            return _top_level_keys(body) if _end != -1 else set()

    # 3. interface T { ... }
    for m in RE_INTERFACE.finditer(src):
        if m.group(1) == type_name:
            body, _end = _extract_balanced(src, m.end() - 1)
            return _top_level_keys(body) if _end != -1 else set()

    return None


class RhfDefaultValuesValidator(BaseValidator):
    """V77: enforce useForm<T> defaultValues covers every T key."""

    id = "V77-rhf-default-values"
    name = "RHF Default Values Type Match"
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
        if "useForm" not in src or "defaultValues" not in src:
            return []

        findings: list[Finding] = []
        for m in RE_USEFORM.finditer(src):
            t_name = m.group(1)
            line_no = src.count("\n", 0, m.start()) + 1

            defaults_body = _find_defaults_body(src, m.end())
            if defaults_body is None:
                continue  # No defaultValues — out of V77's scope.

            type_keys = _type_keys(src, t_name)
            if type_keys is None:
                continue  # T not defined in this file — silent.
            if not type_keys:
                continue  # Found definition but couldn't extract — be conservative.

            default_keys = _top_level_keys(defaults_body)
            missing = sorted(type_keys - default_keys)
            if missing:
                findings.append(
                    Finding(
                        severity="error",
                        file=str(file_path),
                        line=line_no,
                        rule="V77-RHF-DEFAULTS-INCOMPLETE",
                        message=(
                            f"useForm<{t_name}> defaultValues is missing keys: "
                            f"{', '.join(missing)}. RHF will start these inputs as "
                            "uncontrolled and switch to controlled on first keystroke "
                            "— React warning + form.reset() leaves them stale."
                        ),
                        fix=(
                            f"Add explicit defaults for each key in defaultValues, e.g. "
                            f"`{{ {', '.join(f'{k}: ...' for k in missing)} }}`. "
                            "If your defaults intentionally cover only a subset, change "
                            f"the generic to `useForm<Partial<{t_name}>>` or "
                            "`useForm<DefaultValues<...>>`."
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
    validator = RhfDefaultValuesValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
