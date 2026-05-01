"""V27: Connect-RPC handler completeness.

V03 covers ``buf lint`` + a basic proto→Go RPC mapping. V27 enforces
the *handler-side* contract that Connect-RPC handlers actually
follow:

  (a) **Every proto Service.Method has a Go handler with the right
      shape.** ``func (s *FooServer) Bar(ctx context.Context,
      req *connect.Request[pb.BarRequest]) (*connect.Response[pb.BarResponse], error)``
      is the canonical signature. Missing handlers or wrong shapes
      mean runtime "method not found" errors.

  (b) **Handlers are registered with auth + logging + validation
      interceptors.** A NewXxxHandler call without
      ``connect.WithInterceptors(...)`` is a security regression —
      one missing handler is the back-door auth bypass.

  (c) **Errors are returned via ``connect.NewError(connect.Code*,
      ...)``.** A sentinel error returned from a handler ends up as
      HTTP 500 with no gRPC status code mapping; clients can't tell
      "not found" from "internal".

V27 fires only when Connect-RPC is detected (a Go file imports
``connectrpc.com/connect`` or ``github.com/bufbuild/connect-go``).
Projects without Connect get zero V27 cost.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Detection — any Go file in server/ imports a Connect-RPC package
_CONNECT_IMPORTS = (
    "connectrpc.com/connect",
    "github.com/bufbuild/connect-go",
)

# proto: extract `service Foo { rpc Bar(...) returns (...) }`
_PROTO_SERVICE = re.compile(r"service\s+(\w+)\s*\{([^}]*)\}", re.DOTALL)
_PROTO_RPC = re.compile(r"^\s*rpc\s+(\w+)\s*\(", re.MULTILINE)

# Go handler method: `func (s *FooServer) Bar(ctx context.Context, req *connect.Request[...]) (*connect.Response[...], error)`
_GO_HANDLER = re.compile(
    r"""
    func\s+\(\s*\w+\s+\*(?P<recv>\w+)\s*\)\s+
    (?P<name>\w+)\s*
    \(\s*[^)]*\bcontext\.Context\b
    """,
    re.VERBOSE,
)

# Strict shape check — connect.Request / connect.Response wrappers
_GO_HANDLER_STRICT = re.compile(
    r"""
    func\s+\(\s*\w+\s+\*\w+\s*\)\s+\w+\s*\(
    \s*\w+\s+context\.Context\s*,
    \s*\w+\s+\*connect\.Request\[
    """,
    re.VERBOSE,
)

# Handler registration call: `usersv1connect.NewUserServiceHandler(impl, ...)`
_HANDLER_REGISTER = re.compile(
    r"\b(?P<connect_pkg>\w+)\.New(?P<service>\w+)Handler\s*\(",
)

# Interceptor option detection
_WITH_INTERCEPTORS = re.compile(r"connect\.WithInterceptors\s*\(")

# Required interceptor name keywords (case-insensitive)
_REQUIRED_INTERCEPTORS = ("auth", "logging", "validation")

# Sentinel-error return shapes (NOT wrapped in connect.NewError)
_RAW_ERROR_RETURN = re.compile(
    r"""
    \breturn\s+
    (?:nil\s*,\s*)?         # leading nil for a 2-tuple return
    (?:
        (?:fmt\.)?Errorf\s*\( |
        errors\.New\s*\( |
        Err[A-Z]\w+ |        # ErrXxx sentinel
        err\b                # bare `err`
    )
    """,
    re.VERBOSE,
)

# Acceptable wrapper around the same line
_CONNECT_WRAP = re.compile(r"\bconnect\.NewError\s*\(")


def _has_connect_import(go_file: Path) -> bool:
    try:
        text = go_file.read_text(errors="replace")
    except OSError:
        return False
    return any(imp in text for imp in _CONNECT_IMPORTS)


def _detect_connect(ctx: ProjectContext) -> bool:
    """Project-wide gate: any .go file in server/ imports Connect.

    Phase 71: query the shared file_index instead of an independent rglob.
    The detection short-circuits on the first hit so the saving is small,
    but it's the cheapest migration to do alongside the other three.
    """
    if ctx.server_dir is None:
        return False
    server_resolved = ctx.server_dir.resolve()
    for go_file in ctx.file_index.find_by_pattern("*.go"):
        try:
            go_file.resolve().relative_to(server_resolved)
        except (ValueError, OSError):
            continue
        if _has_connect_import(go_file):
            return True
    return False


def _go_files_under(ctx: ProjectContext, root: Path) -> list[Path]:
    """Phase 71: shared helper for V27's three "walk all .go in <root>"
    sites. Replaces ``root.rglob("*.go")`` with file_index + prefix filter
    so we share the single project walk instead of running three more.
    """
    root_resolved = root.resolve()
    out: list[Path] = []
    for go_file in ctx.file_index.find_by_pattern("*.go"):
        try:
            go_file.resolve().relative_to(root_resolved)
        except (ValueError, OSError):
            continue
        out.append(go_file)
    return out


def _proto_dirs(ctx: ProjectContext) -> list[Path]:
    if ctx.proto_dir and ctx.proto_dir.is_dir():
        return [ctx.proto_dir]
    if ctx.server_dir is not None:
        sd = ctx.server_dir / "proto"
        if sd.is_dir():
            return [sd]
    return []


def _server_internal(ctx: ProjectContext) -> Path | None:
    if ctx.server_dir is not None:
        internal = ctx.server_dir / "internal"
        if internal.is_dir():
            return internal
        cmd = ctx.server_dir / "cmd"
        if cmd.is_dir():
            return ctx.server_dir
    return None


class ConnectHandlerValidator(BaseValidator):
    """V27: Connect-RPC handler completeness."""

    id = "V27-connect-handler"
    name = "Connect-RPC Handler Completeness"
    file_patterns: list[str] = [
        "**/*.go",
        "**/proto/**/*.proto",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: project-level checks, retriggered by any relevant edit."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        return self._all_checks(ctx)

    # ── core ─────────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        if not _detect_connect(ctx):
            return []
        findings: list[Finding] = []
        findings.extend(self._check_handler_completeness(ctx))
        findings.extend(self._check_interceptors(ctx))
        findings.extend(self._check_error_returns(ctx))
        return findings

    # ── (a) proto Service.Method ↔ Go handler ───────────────────────

    def _collect_proto_rpcs(self, proto_dirs: list[Path]) -> set[tuple[str, str]]:
        declared: set[tuple[str, str]] = set()
        for proto_dir in proto_dirs:
            for proto_file in proto_dir.rglob("*.proto"):
                try:
                    src = proto_file.read_text(errors="replace")
                except OSError:
                    continue
                for svc_match in _PROTO_SERVICE.finditer(src):
                    svc, body = svc_match.group(1), svc_match.group(2)
                    for rpc_match in _PROTO_RPC.finditer(body):
                        declared.add((svc, rpc_match.group(1)))
        return declared

    def _collect_go_handlers(self, ctx: ProjectContext, internal_root: Path) -> set[tuple[str, str]]:
        implemented: set[tuple[str, str]] = set()
        for go_file in _go_files_under(ctx, internal_root):
            try:
                src = go_file.read_text(errors="replace")
            except OSError:
                continue
            for m in _GO_HANDLER.finditer(src):
                # `*FooServer` → service `Foo`
                recv, name = m.group("recv"), m.group("name")
                if recv.endswith("Server"):
                    service = recv[: -len("Server")]
                    implemented.add((service, name))
        return implemented

    def _check_handler_completeness(self, ctx: ProjectContext) -> list[Finding]:
        proto_dirs = _proto_dirs(ctx)
        internal_root = _server_internal(ctx)
        if not proto_dirs or internal_root is None:
            return []
        declared = self._collect_proto_rpcs(proto_dirs)
        implemented = self._collect_go_handlers(ctx, internal_root)

        findings: list[Finding] = []
        for svc, rpc in sorted(declared - implemented):
            findings.append(
                Finding(
                    severity="error",
                    file=str(internal_root),
                    rule="V27-UNIMPLEMENTED-RPC",
                    message=(
                        f"Proto service {svc}.{rpc} has no matching Go handler "
                        f"(expected `func (s *{svc}Server) {rpc}(ctx, req)`)."
                    ),
                    fix=(
                        f"Implement the handler in internal/.../{svc.lower()}.go "
                        f"or wherever the {svc}Server lives. Signature: "
                        f"`func (s *{svc}Server) {rpc}(ctx context.Context, "
                        f"req *connect.Request[...]) (*connect.Response[...], error)`."
                    ),
                )
            )
        return findings

    # ── (b) NewXxxHandler must use connect.WithInterceptors(...) ────

    def _check_interceptors(self, ctx: ProjectContext) -> list[Finding]:
        internal_root = _server_internal(ctx)
        if internal_root is None:
            return []
        # We only care about main.go / server.go / wiring files where
        # NewXxxHandler is invoked. Walk every .go file and inspect.
        # Phase 71: file_index instead of own rglob.
        findings: list[Finding] = []
        for go_file in _go_files_under(ctx, internal_root):
            try:
                src = go_file.read_text(errors="replace")
            except OSError:
                continue
            for m in _HANDLER_REGISTER.finditer(src):
                # Inspect the next ~3 lines after the call's first paren
                start = m.start()
                # Window after the call — capture up to the closing paren of
                # the NewXxxHandler call. Simple heuristic: 2000 chars.
                window = src[start : start + 2000]
                close_paren = window.find(")\n", window.find("("))
                if close_paren < 0:
                    close_paren = 1500
                window = window[:close_paren]
                if not _WITH_INTERCEPTORS.search(window):
                    line = src[: m.start()].count("\n") + 1
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(go_file),
                            line=line,
                            rule="V27-NO-INTERCEPTORS",
                            message=(
                                f"`{m.group('connect_pkg')}.New{m.group('service')}Handler` "
                                "registered without `connect.WithInterceptors(...)`. "
                                "Auth + logging + validation must wrap every handler."
                            ),
                            fix=(
                                "Wrap the handler call: "
                                "`connect.WithInterceptors(AuthInterceptor(...), "
                                "LoggingInterceptor(...), ValidationInterceptor())`."
                            ),
                        )
                    )
                    continue
                # Has WithInterceptors — check that the auth/logging/validation
                # keywords are mentioned inside the window (loose name match).
                lower_window = window.lower()
                missing = [name for name in _REQUIRED_INTERCEPTORS if name not in lower_window]
                for name in missing:
                    line = src[: m.start()].count("\n") + 1
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(go_file),
                            line=line,
                            rule=f"V27-MISSING-{name.upper()}-INTERCEPTOR",
                            message=(
                                f"Handler registration uses connect.WithInterceptors but "
                                f"no '{name}' interceptor is referenced. Each handler "
                                "should at minimum get auth + logging + validation."
                            ),
                            fix=(
                                f"Add an interceptor whose name contains '{name}' to the "
                                "WithInterceptors list. If your project intentionally "
                                f"skips one (e.g. public RPC, no validation needed), "
                                "disable this rule via .verifiers/config.yaml."
                            ),
                        )
                    )
        return findings

    # ── (c) connect.NewError vs raw sentinel returns ────────────────

    def _check_error_returns(self, ctx: ProjectContext) -> list[Finding]:
        internal_root = _server_internal(ctx)
        if internal_root is None:
            return []
        findings: list[Finding] = []
        # Phase 71: file_index instead of own rglob.
        for go_file in _go_files_under(ctx, internal_root):
            try:
                src = go_file.read_text(errors="replace")
            except OSError:
                continue
            # Only inspect handler functions (those matching the strict shape).
            # Find each handler-method body and scan for raw error returns.
            for handler_match in _GO_HANDLER_STRICT.finditer(src):
                body, end_line = self._extract_body(src, handler_match.end())
                if body is None:
                    continue
                for m in _RAW_ERROR_RETURN.finditer(body):
                    # If the same line contains connect.NewError, skip.
                    line_start = body.rfind("\n", 0, m.start()) + 1
                    line_end = body.find("\n", m.end())
                    if line_end == -1:
                        line_end = len(body)
                    line_text = body[line_start:line_end]
                    if _CONNECT_WRAP.search(line_text):
                        continue
                    rel_line_no = body[: m.start()].count("\n")
                    abs_line_no = src[: handler_match.end()].count("\n") + 1 + rel_line_no
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(go_file),
                            line=abs_line_no,
                            rule="V27-RAW-ERROR-RETURN",
                            message=(
                                "Handler returns a raw error without "
                                "`connect.NewError(connect.Code*, err)`. "
                                "Clients see HTTP 500 with no gRPC status code."
                            ),
                            fix=(
                                "Wrap the error: e.g. "
                                "`return nil, connect.NewError(connect.CodeNotFound, "
                                'fmt.Errorf("user %q not found", id))`. '
                                "Match the Connect code to the failure (NotFound, "
                                "InvalidArgument, PermissionDenied, Internal)."
                            ),
                        )
                    )
        return findings

    @staticmethod
    def _extract_body(src: str, after: int) -> tuple[str | None, int]:
        """Given source + position after the function header's `)`, find
        the matching `{...}` body. Returns (body_text, end_line) or (None, -1)."""
        i = src.find("{", after)
        if i < 0:
            return None, -1
        depth = 1
        j = i + 1
        while j < len(src) and depth > 0:
            ch = src[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            j += 1
        if depth != 0:
            return None, -1
        return src[i + 1 : j - 1], j


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = ConnectHandlerValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
