"""V50: Health endpoint split (livez/readyz).

Kubernetes uses two distinct probes — liveness (is the process alive?) and
readiness (can the process handle traffic?). A single ``/health`` endpoint
mapped to both probes causes cascading failures when a downstream dependency
(e.g. the database) goes down: the liveness probe kills the pod when the pod
is perfectly fine; the readiness probe never drops traffic before the kill.

V50 walks all ``cmd/**/*.go`` files and verifies:
  (a) A ``/livez`` route is registered somewhere.
  (b) A ``/readyz`` route is registered somewhere.
  (c) The file that registers ``/readyz`` also references a database ping
      operation (imports pgx or calls Ping/Query/Exec/Select).

The check fires only when at least one HTTP route registration was found,
so background worker binaries (which have no HTTP server at all) are ignored.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Route registration patterns — capture the path string from common Go HTTP
# multiplexer / router call forms.
_ROUTE_RE = re.compile(
    r"(?:mux|router|r|http|s|srv)"
    r"\.(?:HandleFunc|Handle|Get|Post|Put|Delete|Patch|Mount|Method)"
    r'\s*\(\s*"([^"]+)"',
)

# Alternative: bare http.HandleFunc("...", ...)
_HTTP_HANDLE_RE = re.compile(r'http\.(?:HandleFunc|Handle)\s*\(\s*"([^"]+)"')

# /livez variants
_LIVEZ_RE = re.compile(r"^/(?:livez|healthz/live|healthz/liveness)/?$")
# /readyz variants
_READYZ_RE = re.compile(r"^/(?:readyz|healthz/ready|healthz/readiness)/?$")

# DB-awareness signals in the readyz handler's source file
_DB_PACKAGES = ("pgx", "database/sql", "sql/driver", "gorm", "sqlx", "bun")
_DB_CALL_RE = re.compile(r"\b(?:Ping|Query|Exec|Select|BeginTx|QueryRow)\s*\(")


def _cmd_dir(ctx: ProjectContext) -> Path | None:
    """Return the ``cmd/`` directory, preferring ``server/cmd/``."""
    # Check ctx.server_dir first (set at ProjectContext init time)
    if ctx.server_dir is not None:
        candidate = ctx.server_dir / "cmd"
        if candidate.is_dir():
            return candidate
    # Also check project_root/server/cmd directly — handles the case where
    # server/ was created after ProjectContext was constructed (e.g. in tests).
    candidate = ctx.project_root / "server" / "cmd"
    if candidate.is_dir():
        return candidate
    candidate = ctx.project_root / "cmd"
    if candidate.is_dir():
        return candidate
    return None


def _collect_routes(go_src: str) -> list[str]:
    """Return all route path strings found in a Go source file."""
    paths: list[str] = []
    for m in _ROUTE_RE.finditer(go_src):
        paths.append(m.group(1))
    for m in _HTTP_HANDLE_RE.finditer(go_src):
        paths.append(m.group(1))
    return paths


def _has_db_awareness(go_src: str) -> bool:
    """Return True if the file imports a DB package or calls a DB operation."""
    has_import = any(pkg in go_src for pkg in _DB_PACKAGES)
    has_call = bool(_DB_CALL_RE.search(go_src))
    return has_import and has_call


class HealthEndpointSplitValidator(BaseValidator):
    """V50: Health endpoint split (livez/readyz)."""

    id = "V50-health-endpoint-split"
    name = "Health Endpoint Split (livez/readyz)"
    file_patterns: list[str] = ["server/cmd/**/*.go", "**/cmd/**/*.go"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: any cmd/**/*.go edit triggers the project-wide check."""
        return self._all_checks(ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: full sweep of cmd/ directory."""
        return self._all_checks(ctx)

    # ── core ─────────────────────────────────────────────────────────

    def _all_checks(self, ctx: ProjectContext) -> list[Finding]:
        cmd = _cmd_dir(ctx)
        if cmd is None:
            return []

        # Collect all non-test Go files under cmd/
        go_files = [f for f in cmd.rglob("*.go") if not f.name.endswith("_test.go")]
        if not go_files:
            return []

        # Read all sources; track which file registers each path
        all_routes: set[str] = set()
        readyz_files: list[Path] = []
        first_file = go_files[0]

        for go_file in go_files:
            try:
                src = go_file.read_text(errors="replace")
            except OSError:
                continue
            routes = _collect_routes(src)
            all_routes.update(routes)
            for path in routes:
                if _READYZ_RE.match(path):
                    readyz_files.append(go_file)

        # If no HTTP routes at all — background worker, not an HTTP server.
        if not all_routes:
            return []

        has_livez = any(_LIVEZ_RE.match(p) for p in all_routes)
        has_readyz = any(_READYZ_RE.match(p) for p in all_routes)

        findings: list[Finding] = []

        if not has_livez or not has_readyz:
            findings.append(
                Finding(
                    severity="error",
                    file=str(first_file),
                    rule="V50-HEALTH-NOT-SPLIT",
                    message=(
                        f"Server has no /livez and/or /readyz route. "
                        f"Found routes: {sorted(all_routes)}. "
                        "K8s probes can't distinguish liveness from readiness."
                    ),
                    fix=(
                        'Register `mux.HandleFunc("/livez", ...)` for process-only liveness '
                        'check and `mux.HandleFunc("/readyz", ...)` for DB-aware readiness. '
                        "See skills/V50-health-endpoint-split/SKILL.md for the canonical pattern."
                    ),
                )
            )

        # Check that /readyz handler has DB awareness
        if has_readyz:
            db_aware = False
            for go_file in readyz_files:
                try:
                    src = go_file.read_text(errors="replace")
                except OSError:
                    continue
                if _has_db_awareness(src):
                    db_aware = True
                    break
            if not db_aware:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(readyz_files[0]) if readyz_files else str(first_file),
                        rule="V50-READYZ-NO-DB-PING",
                        message=(
                            "/readyz route is registered but the handler file does not "
                            "import a DB package (pgx, database/sql, gorm, sqlx) or call "
                            "Ping/Query/Exec. A /readyz that always returns 200 defeats K8s "
                            "readiness probing."
                        ),
                        fix=(
                            "Add a DB ping inside the /readyz handler: "
                            "`if err := db.Ping(ctx); err != nil { w.WriteHeader(503); return }`. "
                            "See skills/V50-health-endpoint-split/SKILL.md for the canonical pattern."
                        ),
                    )
                )

        return findings


# ── Standalone execution ─────────────────────────────────────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = HealthEndpointSplitValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
