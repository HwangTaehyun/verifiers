"""V62: Go typed env — restrict os.Getenv / os.LookupEnv to config layer.

Direct ``os.Getenv("APP_FOO")`` calls scattered through the codebase
make environment surface impossible to reason about: a new env var
must be remembered in N places, missing it produces silent
``""``-fallback bugs at runtime, and tests can't isolate config.
The canonical fix is a typed config struct loaded ONCE at startup
in ``internal/config/`` and dependency-injected; every other layer
reads ``cfg.FooBar``, never ``os.Getenv(...)``.

V62 enforces this by flagging any ``os.Getenv`` / ``os.LookupEnv``
call outside an allowed directory (default: ``internal/config/`` and
``cmd/*/main.go``).

Rules:
  - V62-DIRECT-ENV — ``os.Getenv`` / ``os.LookupEnv`` outside allowed dirs (warning)

Configuration (``.verifiers/config.yaml``)::

    go:
      config_dirs:
        - "internal/config"
        - "cmd"

If ``go.config_dirs`` is empty, V62 falls back to the default allowlist.

Escape hatch: same-line ``// verifier:env-direct-ok REASON`` comment.

V62 complements V01-ENV-MISSING (which checks .env.example completeness):
V01 = "is this var documented?", V62 = "is this var read in the right place?".

Reference: [12-Factor App III. Config](https://12factor.net/config) (published
2011, retrieved 2026-05-03). Typed-env libraries: [caarlos0/env](https://github.com/caarlos0/env)
(continuously developed since 2015), [kelseyhightower/envconfig](https://github.com/kelseyhightower/envconfig)
(deprecated 2020 but pattern still standard).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# os.Getenv("KEY") OR os.LookupEnv("KEY") — capture the env name for a
# better error message.
RE_OS_GETENV = re.compile(r'\bos\.(?:Getenv|LookupEnv)\s*\(\s*"([^"]+)"\s*\)')

# Same-line escape hatch.
RE_VERIFIER_OK = re.compile(r"//\s*verifier:env-direct-ok\b")

# Default config-dir allowlist when .verifiers/config.yaml doesn't override.
_DEFAULT_CONFIG_DIRS: tuple[str, ...] = ("internal/config", "cmd")

_SKIP_FILE_SUFFIX = "_test.go"


def _path_under_any(path_parts: list[str], allowed: tuple[str, ...]) -> bool:
    """Return True if ``path_parts`` lies under any allowed directory.

    Each allowed entry is a slash-separated prefix (e.g. ``internal/config``).
    Match requires the segments to appear consecutively from any anchor
    in the path so a server/cmd/api/main.go inside a project under
    server/ counts as being under cmd.
    """
    for allowed_path in allowed:
        seg = [p for p in allowed_path.split("/") if p]
        if not seg:
            continue
        n = len(seg)
        for i in range(len(path_parts) - n + 1):
            if path_parts[i : i + n] == seg:
                return True
    return False


class GoTypedEnvValidator(BaseValidator):
    """V62: forbid ``os.Getenv`` outside the config layer."""

    id = "V62-go-typed-env"
    name = "Go Typed Env"
    file_patterns: list[str] = ["**/*.go"]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        path = Path(file_path)
        if not path.is_file() or file_path.endswith(_SKIP_FILE_SUFFIX):
            return []
        return self._scan_file(path, ctx)

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        if not (ctx.server_dir and ctx.server_dir.exists()):
            return []
        server_resolved = ctx.server_dir.resolve()
        findings: list[Finding] = []
        for go_file in ctx.file_index.find_by_pattern("*.go"):
            try:
                go_file.resolve().relative_to(server_resolved)
            except (ValueError, OSError):
                continue
            if str(go_file).endswith(_SKIP_FILE_SUFFIX):
                continue
            findings.extend(self._scan_file(go_file, ctx))
        return findings

    def _scan_file(self, file_path: Path, ctx: ProjectContext) -> list[Finding]:
        # Allowed dirs come from config or fall back to defaults.
        allowed = tuple(ctx.config.go.config_dirs) if ctx.config.go.config_dirs else _DEFAULT_CONFIG_DIRS

        # Use project-root-relative path so config_dir matching works.
        try:
            rel = file_path.resolve().relative_to(ctx.project_root.resolve())
        except (ValueError, OSError):
            rel = file_path
        rel_parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
        if _path_under_any(rel_parts, allowed):
            return []  # File is in the config layer — direct env access is OK.

        try:
            src = file_path.read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        lines = src.splitlines()
        for match in RE_OS_GETENV.finditer(src):
            env_name = match.group(1)
            line_no = src.count("\n", 0, match.start()) + 1
            line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            if RE_VERIFIER_OK.search(line_text):
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=str(file_path),
                    line=line_no,
                    rule="V62-DIRECT-ENV",
                    message=(
                        f"Direct os.Getenv(\"{env_name}\") outside config layer "
                        f"({', '.join(allowed)}). Scattering env reads makes the "
                        "configuration surface untracked and untestable."
                    ),
                    fix=(
                        f"Move \"{env_name}\" into the typed config struct in "
                        "internal/config/ (e.g. with caarlos0/env tags) and inject "
                        f"the config into this layer. If this read genuinely belongs "
                        f"here, add `// verifier:env-direct-ok REASON` to the same line."
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
    validator = GoTypedEnvValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
