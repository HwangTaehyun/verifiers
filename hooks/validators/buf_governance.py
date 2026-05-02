"""V23: Buf governance — lock drift, breaking changes, protovalidate enforcement.

V03 (proto_connect) covers ``buf lint`` + handler coverage. V23 sits
on the *contract governance* layer:

  1. **buf.yaml ↔ buf.lock drift.** ``buf.yaml`` declares deps; ``buf.lock``
     pins the actual fetched commits. When a developer adds a dep to
     buf.yaml without running ``buf dep update``, the lock falls behind
     — same source produces different outputs depending on cache state.

  2. **buf breaking against main.** A wire-incompatible proto change
     breaks every running client (mobile apps, third-party API users).
     V03 has the same check; V23 makes it always-on at Stop and uses
     ``git rev-parse --git-common-dir`` so it works inside git worktrees.

  3. **protovalidate enforcement.** A proto field marked ``required``
     in semantic terms (e.g. an ID, an email) but with no
     ``(buf.validate.field).required = true`` rule lets clients send
     empty values; the server rejects later with a confusing 500 instead
     of returning ``InvalidArgument`` early.

V23 fires only when ``buf.yaml`` exists in the project (no Buf =
no findings, zero cost).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml  # noqa: E402

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output  # noqa: E402
from lib.json_logger import log_exception  # noqa: E402
from lib.project_context import ProjectContext  # noqa: E402

# Field names that *strongly imply* required even without an explicit
# rule — the heuristic for V23-PROTOVALIDATE-MISSING. Empirically these
# are the field names where "value can be empty" is almost always a bug.
_REQUIRED_HINT_NAMES: tuple[str, ...] = (
    "id",
    "user_id",
    "account_id",
    "tenant_id",
    "email",
    "phone",
    "phone_number",
    "name",
    "username",
)

# Buf lint output line: file:line:col:RULE_NAME message
_BUF_LINE = re.compile(r"^([^:]+):(\d+):(\d+):([A-Z_]+)\s+(.+)$")
_PROTO_FIELD = re.compile(
    r"""^\s*
        (?:repeated\s+|optional\s+)?
        (?:[\w.]+)\s+               # type
        (\w+)\s*=\s*\d+              # field name + tag
        ([^;]*)                     # rest of line including options
        ;""",
    re.VERBOSE,
)


def _find_buf_dir(ctx: ProjectContext) -> Path | None:
    """Locate the directory containing buf.yaml.

    Most projects have it in ``server/`` or root; V23 prefers the
    server-side one when both exist (matches the user's monorepo).
    """
    candidates: list[Path] = []
    if ctx.server_dir is not None:
        candidates.append(ctx.server_dir)
    candidates.append(ctx.project_root)
    for d in candidates:
        if (d / "buf.yaml").is_file():
            return d
    return None


class BufGovernanceValidator(BaseValidator):
    """V23: Buf governance."""

    id = "V23-buf-governance"
    name = "Buf Governance"
    file_patterns: list[str] = [
        "**/buf.yaml",
        "**/buf.lock",
        "**/buf.gen.yaml",
        "**/proto/**/*.proto",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Tier 2: lock drift + protovalidate + ts_nocheck (cheap)."""
        buf_dir = _find_buf_dir(ctx)
        findings: list[Finding] = []
        if buf_dir is not None:
            findings.extend(self._check_lock_drift(buf_dir))
            findings.extend(self._check_protovalidate(buf_dir))
        # ts_nocheck check is project-wide (web/buf.gen.yaml may live
        # outside buf_dir) — runs even when buf_dir is None.
        findings.extend(self._check_ts_nocheck_disabled(ctx))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Tier 3: lock drift + protovalidate + breaking + ts_nocheck."""
        buf_dir = _find_buf_dir(ctx)
        findings: list[Finding] = []
        if buf_dir is not None:
            findings.extend(self._check_lock_drift(buf_dir))
            findings.extend(self._check_protovalidate(buf_dir))
            findings.extend(self._check_breaking(ctx, buf_dir))
        findings.extend(self._check_ts_nocheck_disabled(ctx))
        return findings

    # ── (a) buf.yaml ↔ buf.lock drift ─────────────────────────────────

    def _check_lock_drift(self, buf_dir: Path) -> list[Finding]:
        yaml_path = buf_dir / "buf.yaml"
        lock_path = buf_dir / "buf.lock"

        try:
            yaml_doc = yaml.safe_load(yaml_path.read_text(errors="replace")) or {}
        except (yaml.YAMLError, OSError):
            return []

        declared = set()
        for dep in yaml_doc.get("deps") or []:
            if isinstance(dep, str):
                declared.add(dep)

        # No deps declared → no lock to compare. Some projects legitimately
        # have buf.yaml without external deps.
        if not declared:
            return []

        if not lock_path.exists():
            return [
                Finding(
                    severity="warning",
                    file=str(yaml_path),
                    rule="V23-LOCK-DRIFT",
                    message=(f"buf.yaml declares {len(declared)} dep(s) but buf.lock is missing."),
                    fix=f"Run 'cd {buf_dir} && buf dep update' to generate buf.lock.",
                )
            ]

        try:
            lock_doc = yaml.safe_load(lock_path.read_text(errors="replace")) or {}
        except (yaml.YAMLError, OSError):
            return []

        locked = set()
        for dep in lock_doc.get("deps") or []:
            if isinstance(dep, dict):
                name = dep.get("name") or dep.get("module")
                if isinstance(name, str):
                    locked.add(name)

        findings: list[Finding] = []
        for missing in sorted(declared - locked):
            findings.append(
                Finding(
                    severity="warning",
                    file=str(yaml_path),
                    rule="V23-LOCK-DRIFT",
                    message=(f"Dep '{missing}' is declared in buf.yaml but not pinned in buf.lock."),
                    fix=f"Run 'cd {buf_dir} && buf dep update'.",
                )
            )
        for stale in sorted(locked - declared):
            findings.append(
                Finding(
                    severity="warning",
                    file=str(lock_path),
                    rule="V23-LOCK-DRIFT",
                    message=(f"Dep '{stale}' is in buf.lock but no longer declared in buf.yaml."),
                    fix=f"Run 'cd {buf_dir} && buf dep update' to prune the stale entry.",
                )
            )
        return findings

    # ── (b) buf breaking against main ─────────────────────────────────

    def _check_breaking(self, ctx: ProjectContext, buf_dir: Path) -> list[Finding]:
        # Resolve the common-dir so worktree checkouts work.
        try:
            cd_result = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=str(ctx.project_root),
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []
        if cd_result.returncode != 0:
            return []
        common_dir = cd_result.stdout.strip()

        # Subdir-relative is what `buf breaking --against` expects.
        try:
            subdir = buf_dir.relative_to(ctx.project_root)
        except ValueError:
            subdir = Path(".")
        subdir_str = str(subdir).replace("\\", "/")

        try:
            result = subprocess.run(
                [
                    "buf",
                    "breaking",
                    str(buf_dir),
                    "--against",
                    f"git#{common_dir}#branch=main,subdir={subdir_str}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            log_exception(source="V23/buf_breaking", error=exc, context={"buf_dir": str(buf_dir)})
            return []

        if result.returncode == 0:
            return []

        findings: list[Finding] = []
        for line in (result.stdout + result.stderr).splitlines():
            if m := _BUF_LINE.match(line):
                findings.append(
                    Finding(
                        severity="error",
                        file=m.group(1),
                        line=int(m.group(2)),
                        rule=f"V23-BREAKING-{m.group(4)}",
                        message=m.group(5),
                        fix=(
                            "Wire-incompatible change against main. Either add a "
                            "new field/method instead of modifying, or coordinate "
                            "a major version bump with all clients."
                        ),
                    )
                )
        return findings

    # ── (c) protovalidate enforcement ─────────────────────────────────

    def _check_protovalidate(self, buf_dir: Path) -> list[Finding]:
        proto_dir = buf_dir / "proto"
        if not proto_dir.is_dir():
            return []

        findings: list[Finding] = []
        for proto_file in proto_dir.rglob("*.proto"):
            try:
                src = proto_file.read_text(errors="replace")
            except OSError:
                continue
            for line_no, line in enumerate(src.splitlines(), 1):
                # Skip comments/empty
                stripped = line.strip()
                if not stripped or stripped.startswith(("//", "/*", "*")):
                    continue
                m = _PROTO_FIELD.match(line)
                if not m:
                    continue
                field_name, rest = m.group(1), m.group(2)
                if field_name not in _REQUIRED_HINT_NAMES:
                    continue
                # Already validated?
                if "buf.validate.field" in rest:
                    continue
                if "(validate.rules)" in rest:  # legacy protoc-gen-validate
                    continue
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(proto_file),
                        line=line_no,
                        rule="V23-PROTOVALIDATE-MISSING",
                        message=(
                            f"Field '{field_name}' looks load-bearing but has no "
                            "protovalidate rule. Empty / zero values will pass "
                            "schema and fail later in handler logic with a 500."
                        ),
                        fix=(
                            "Add `[(buf.validate.field).required = true]` (and/or "
                            "format-specific rules like `.string.email = true`)."
                        ),
                    )
                )
        return findings


    # ── (d) ts_nocheck=false enforcement (Phase 71 follow-up) ────────

    def _check_ts_nocheck_disabled(self, ctx: ProjectContext) -> list[Finding]:
        """Require ``ts_nocheck=false`` on TS-targeting plugins in buf.gen.yaml.

        The buf-build/es and connectrpc/es plugins default to emitting
        ``// @ts-nocheck`` at the top of every generated file, which
        silently disables strict-mode TS checking for the entire
        Connect-RPC client surface. Mistypes / mis-shaped messages
        flow through untyped and only fail at runtime.

        Adding ``- ts_nocheck=false`` to the plugin's ``opt`` list flips
        the generator off — generated files become part of the strict-
        TS pipeline. This rule enforces that flip on any plugin that
        targets TypeScript (``target=ts`` in opt OR plugin name ending
        in ``/es:*``).

        Walks every ``buf.gen.yaml`` in the project via Phase 65
        ``ctx.file_index`` so web/buf.gen.yaml and server/buf.gen.yaml
        get checked together.

        Reference: ``ts_nocheck`` plugin option documented at
        https://github.com/bufbuild/protobuf-es/blob/main/docs/runtime_api.md
        """
        findings: list[Finding] = []
        for buf_gen in ctx.file_index.find_by_pattern("buf.gen.yaml"):
            try:
                doc = yaml.safe_load(buf_gen.read_text(errors="replace"))
            except (yaml.YAMLError, OSError):
                continue
            if not isinstance(doc, dict):
                continue
            plugins = doc.get("plugins") or []
            if not isinstance(plugins, list):
                continue
            for idx, plugin in enumerate(plugins):
                if not isinstance(plugin, dict):
                    continue
                opt = plugin.get("opt") or []
                if not isinstance(opt, list):
                    continue
                opt_strs = [str(x) for x in opt]
                # Identify TS-targeting plugins.
                plugin_name = str(plugin.get("remote") or plugin.get("local") or plugin.get("name") or "")
                is_ts = (
                    "target=ts" in opt_strs
                    or "/es:" in plugin_name
                    or plugin_name.endswith("/es")
                )
                if not is_ts:
                    continue
                # Look for ts_nocheck=true (bad) or absence (default-true, also bad).
                has_false = "ts_nocheck=false" in opt_strs
                has_true = "ts_nocheck=true" in opt_strs
                if has_false:
                    continue  # Explicitly disabled — good.
                # Either missing or set to true — emit finding.
                rule = "V23-TS-NOCHECK-ENABLED" if has_true else "V23-TS-NOCHECK-MISSING"
                msg_state = "set to true" if has_true else "not set (default true)"
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(buf_gen),
                        rule=rule,
                        message=(
                            f"Plugin '{plugin_name or f'#{idx}'}' targets TS but ts_nocheck is "
                            f"{msg_state}. Generated files start with `// @ts-nocheck`, "
                            "silently bypassing strict-TS checking of the Connect-RPC client "
                            "surface."
                        ),
                        fix=(
                            f"Add `- ts_nocheck=false` to the plugin's opt list in "
                            f"{buf_gen}. Then re-generate (e.g. `bun run generate:buf`) so "
                            "the @ts-nocheck headers are dropped and tsc actually checks the "
                            "generated code."
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
    validator = BufGovernanceValidator()
    result = validator.run(ctx, file_path=None, mode="stop")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
