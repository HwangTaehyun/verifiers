"""V07: TypeScript quality validator — any type, hardcoded colors, ESLint, tsc.

PostToolUse checks (fast, <5s):
  V07-NO-ANY: Explicit 'any' type usage
  V07-HARDCODED-COLOR: Hardcoded color values instead of theme.palette
  V07-NO-CONSOLE: console.log/debug/info in production code
  V07-DEPRECATED-MUI: MUI v4 deprecated patterns (makeStyles, @material-ui/)
  V07-ESLINT-*: ESLint single-file findings

Stop checks (slow, comprehensive):
  V07-TSC-*: TypeScript compilation errors (tsc --noEmit)
  V07-ESLINT-*: ESLint full project findings
  V07-CIRCULAR-IMPORT: Circular dependencies (madge)
  V07-UNUSED-CODE: Unused exports/files/dependencies (knip)
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Add parent directories to path so we can import lib/
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hooks.validators.base import BaseValidator, Finding, read_hook_input, write_hook_output
from lib.project_context import ProjectContext

# ── Hardcoded color pattern ──────────────────────────────────────────────────

COLOR_PATTERN = re.compile(
    r"""(?:color|backgroundColor|background|borderColor|fill|stroke)\s*[:=]\s*['"]#[0-9a-fA-F]{3,8}['"]"""
    r"""|(?:color|backgroundColor|background|borderColor|fill|stroke)\s*[:=]\s*['"](?:rgb|rgba|hsl)\("""
)

# ── Deprecated MUI v4 patterns ───────────────────────────────────────────────

DEPRECATED_MUI: list[tuple[str, str]] = [
    (r"\bmakeStyles\b", "makeStyles is deprecated in MUI v5. Use 'sx' prop or 'styled()' instead."),
    (r"\bwithStyles\b", "withStyles is deprecated in MUI v5. Use 'sx' prop or 'styled()' instead."),
    (r"from\s+['\"]@material-ui/", "@material-ui/ is MUI v4 import. Use @mui/material/ instead."),
]


class TsQualityValidator(BaseValidator):
    """V07: TypeScript Quality Validator."""

    id = "V07-ts-quality"
    name = "TypeScript Quality Validator"
    file_patterns: list[str] = [
        "**/*.ts",
        "**/*.tsx",
        "**/package.json",
        "**/tsconfig.json",
    ]

    def validate_file(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Phase29+ API: per-edit TS/TSX checks (Tier 2)."""
        if not ctx.web_dir or not ctx.web_dir.exists():
            return []
        if not file_path.endswith((".ts", ".tsx")):
            return []
        findings: list[Finding] = []
        findings.extend(self._check_any_type(file_path))
        findings.extend(self._check_hardcoded_colors(file_path))
        findings.extend(self._check_console_log(file_path))
        findings.extend(self._check_deprecated_mui(file_path))
        findings.extend(self._check_eslint_single(ctx, file_path))
        return findings

    def validate_project(self, ctx: ProjectContext) -> list[Finding]:
        """Phase29+ API: project-wide TS quality sweep (Tier 3)."""
        if not ctx.web_dir or not ctx.web_dir.exists():
            return []
        findings: list[Finding] = []
        findings.extend(self._check_tsc(ctx))
        findings.extend(self._check_eslint_full(ctx))
        findings.extend(self._check_circular_imports(ctx))
        findings.extend(self._check_unused_code(ctx))
        findings.extend(self._check_vite_env_typed(ctx))
        return findings

    # ── Check 1: any type ────────────────────────────────────────────────

    def _check_any_type(self, file_path: str) -> list[Finding]:
        """Detect explicit 'any' type usage."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(r":\s*any\b|as\s+any\b|<any>", line):
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*")):
                    continue
                findings.append(
                    Finding(
                        severity="error",
                        file=file_path,
                        rule="V07-NO-ANY",
                        message=f"Explicit 'any' type found: {stripped[:80]}",
                        fix=(
                            f"Replace 'any' with a specific type at {file_path}:{i}. "
                            f"Use 'unknown' if type is truly unknown, or define a proper interface."
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 2: Hardcoded colors ────────────────────────────────────────

    def _check_hardcoded_colors(self, file_path: str) -> list[Finding]:
        """Detect hardcoded color values instead of theme.palette."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            if COLOR_PATTERN.search(line):
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*")):
                    continue
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V07-HARDCODED-COLOR",
                        message="Hardcoded color value found — use theme.palette instead",
                        fix=(
                            f"Replace hardcoded color at {file_path}:{i} with "
                            f"theme.palette.* (e.g., theme.palette.primary.main)"
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 3: console.log ─────────────────────────────────────────────

    def _check_console_log(self, file_path: str) -> list[Finding]:
        """Detect console.log/debug/info in production code."""
        if any(exc in file_path for exc in [".test.", ".stories.", "__tests__", ".spec."]):
            return []

        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(r"\bconsole\.(log|debug|info)\b", line):
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        rule="V07-NO-CONSOLE",
                        message="console.log/debug/info found in production code",
                        fix=(
                            f"Remove console.log at {file_path}:{i}. "
                            f"Use console.warn/error for actual warnings, or a proper logger."
                        ),
                        line=i,
                    )
                )

        return findings

    # ── Check 4: Deprecated MUI v4 patterns ──────────────────────────────

    def _check_deprecated_mui(self, file_path: str) -> list[Finding]:
        """Detect MUI v4 deprecated patterns."""
        try:
            content = Path(file_path).read_text(errors="replace")
        except OSError:
            return []

        findings: list[Finding] = []
        for i, line in enumerate(content.split("\n"), 1):
            for pattern, msg in DEPRECATED_MUI:
                if re.search(pattern, line):
                    findings.append(
                        Finding(
                            severity="error",
                            file=file_path,
                            rule="V07-DEPRECATED-MUI",
                            message=msg,
                            fix=f"Update the import/usage at {file_path}:{i}. {msg}",
                            line=i,
                        )
                    )

        return findings

    # ── Cache helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _cache_disabled() -> bool:
        return os.environ.get("VERIFIERS_NO_CACHE", "0") == "1"

    def _resolve_eslint_command(self, ctx: ProjectContext) -> list[str]:
        """Phase 67: prefer the project's local ESLint binary over ``bun run eslint``.

        ``bun run eslint`` invokes the project's ``package.json`` script
        for ``eslint``, which on real-world projects (e.g. ax-finance-project)
        already includes args like ``"src/**/*.{ts, tsx}" --fix --no-warn-ignored``.
        Appending V07's args duplicates options (``--no-warn-ignored
        --no-warn-ignored``) which ESLint v8 rejects with
        ``Invalid option '--warn-ignored'``, exiting in ~150 ms before
        any linting happens. Result: V07 was silently producing zero
        findings on every run.

        Calling ``<web>/node_modules/.bin/eslint`` directly bypasses
        the script wrapper. The local binary is what ``bun run eslint``
        would have invoked anyway (resolved via the same node_modules),
        so behavior is otherwise identical.

        Falls back to ``bun run eslint`` when the local binary is not
        present (rare — implies node_modules wasn't installed yet).
        """
        web_dir = ctx.web_dir if ctx.web_dir else (Path(ctx.project_root) / "web")
        local_bin = web_dir / "node_modules" / ".bin" / "eslint"
        if local_bin.is_file():
            return [str(local_bin)]
        return ["bun", "run", "eslint"]

    def _invalidate_eslint_cache_if_lock_changed(self, ctx: ProjectContext) -> Path:
        """Return cache **file** path, deleting it first if package lockfile changed.

        Phase 67: returns a FILE (``.eslintcache``) inside
        ``.verifiers/cache/eslint/``, not a directory. ESLint v8's
        ``--cache-location`` accepts either, but with a directory
        target ESLint creates ``.lock-hash`` (a 0-byte sentinel) and
        we measured the actual cache file never being written —
        possibly an interaction with cache-strategy=content. Using
        a plain file path avoids that path entirely.

        The lockfile-hash invalidation logic (so a ``bun.lockb`` /
        ``package-lock.json`` / ``yarn.lock`` change wipes stale
        cache entries from a different plugin set) is preserved.
        """
        cache_dir = Path(ctx.project_root) / ".verifiers" / "cache" / "eslint"
        cache_file = cache_dir / ".eslintcache"
        lock_hash_file = cache_dir / ".lock-hash"

        web_dir = ctx.web_dir if ctx.web_dir else (Path(ctx.project_root) / "web")
        lock_candidates = [
            web_dir / "bun.lockb",
            web_dir / "package-lock.json",
            web_dir / "yarn.lock",
        ]

        current_hash = ""
        for lock in lock_candidates:
            if lock.is_file():
                current_hash = hashlib.sha256(lock.read_bytes()).hexdigest()
                break

        if cache_dir.exists() and lock_hash_file.is_file():
            stored = lock_hash_file.read_text(errors="replace").strip()
            if stored != current_hash:
                # Wipe both the cache file and the lock-hash sentinel so
                # the next run starts fresh.
                shutil.rmtree(cache_dir, ignore_errors=True)

        cache_dir.mkdir(parents=True, exist_ok=True)
        lock_hash_file.write_text(current_hash)
        return cache_file

    def _supports_incremental(self, ctx: ProjectContext) -> bool:
        """Check if TypeScript >= 5.0 (incremental + noEmit safe).

        Phase 70: delegate to ``lib.subprocess_cache.detect_tool_version``
        which is now lru_cached per process. cProfile measured this at
        76 ms per Stop hook before — small but pure waste since the
        TypeScript version doesn't change mid-process.
        """
        from lib.subprocess_cache import detect_tool_version

        web_dir = ctx.web_dir if ctx.web_dir else (Path(ctx.project_root) / "web")
        try:
            version_str = detect_tool_version(["bun", "run", "tsc", "--version"], cwd=web_dir)
            m = re.search(r"Version (\d+)\.", version_str)
            if m and int(m.group(1)) >= 5:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False

    # ── Check 5: ESLint single file ──────────────────────────────────────

    def _check_eslint_single(self, ctx: ProjectContext, file_path: str) -> list[Finding]:
        """Run ESLint on a single file."""
        findings: list[Finding] = []

        # Phase 67: bypass ``bun run eslint`` (which fold-merges with
        # the project's package.json eslint script and creates duplicate
        # options) by calling the local node_modules eslint binary
        # directly.
        # Phase 67: ``--no-warn-ignored`` was an ESLint v9+ flag — v8 (still
        # the most common) emits ``Invalid option '--warn-ignored'`` and
        # exits with code 2 in <200 ms, producing zero findings silently.
        # Drop it so V07 actually runs ESLint. The ignored-file warnings
        # the flag would have suppressed go to stderr in v8 and don't
        # affect the JSON ``messages[]`` we parse.
        cmd = self._resolve_eslint_command(ctx) + [
            "--max-warnings",
            "0",
            "--format",
            "json",
        ]
        if not self._cache_disabled():
            cache_file = self._invalidate_eslint_cache_if_lock_changed(ctx)
            cmd += [
                "--cache",
                "--cache-strategy",
                "content",
                "--cache-location",
                str(cache_file),
            ]
        cmd.append(file_path)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ctx.web_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return findings

            for file_result in data:
                for msg in file_result.get("messages", []):
                    rule_id = msg.get("ruleId") or "unknown"
                    findings.append(
                        Finding(
                            severity="error" if msg.get("severity") == 2 else "warning",
                            file=file_path,
                            rule=f"V07-ESLINT-{rule_id}",
                            message=msg.get("message", ""),
                            fix=(
                                f"Fix ESLint error '{rule_id}' at "
                                f"{file_path}:{msg.get('line')}: {msg.get('message', '')}"
                            ),
                            line=msg.get("line"),
                        )
                    )

        return findings

    # ── Check 6: tsc --noEmit (Stop mode) ────────────────────────────────

    def _check_tsc(self, ctx: ProjectContext) -> list[Finding]:
        """Full TypeScript type checking."""
        findings: list[Finding] = []

        cmd = ["bun", "run", "tsc", "--noEmit", "--pretty"]
        if not self._cache_disabled() and self._supports_incremental(ctx):
            buildinfo = Path(ctx.project_root) / ".verifiers" / "cache" / "tsc.tsbuildinfo"
            buildinfo.parent.mkdir(parents=True, exist_ok=True)
            cmd += ["--incremental", "--tsBuildInfoFile", str(buildinfo)]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ctx.web_dir),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0:
            for line in result.stdout.strip().split("\n"):
                match = re.search(r"(.+)\((\d+),\d+\): error (TS\d+): (.+)", line)
                if match:
                    findings.append(
                        Finding(
                            severity="error",
                            file=str(ctx.web_dir / match.group(1)),
                            rule=f"V07-TSC-{match.group(3)}",
                            message=match.group(4),
                            fix=f"Fix TypeScript error {match.group(3)}: {match.group(4)}",
                            line=int(match.group(2)),
                        )
                    )

        return findings

    # ── Check 7: ESLint full project (Stop mode) ────────────────────────

    def _check_eslint_full(self, ctx: ProjectContext) -> list[Finding]:
        """Run ESLint on entire project (Tier 3 stop mode).

        Phase 67: same fixes as ``_check_eslint_single`` — direct
        binary call avoids ``bun run eslint`` script fold-merge,
        and ``--cache-location`` is a file path so the cache file
        actually gets written.
        """
        findings: list[Finding] = []

        # Phase 67: ``--no-warn-ignored`` was an ESLint v9+ flag — v8 (still
        # the most common) emits ``Invalid option '--warn-ignored'`` and
        # exits with code 2 in <200 ms, producing zero findings silently.
        # Drop it so V07 actually runs ESLint. The ignored-file warnings
        # the flag would have suppressed go to stderr in v8 and don't
        # affect the JSON ``messages[]`` we parse.
        cmd = self._resolve_eslint_command(ctx) + [
            "--max-warnings",
            "0",
            "--format",
            "json",
        ]
        if not self._cache_disabled():
            cache_file = self._invalidate_eslint_cache_if_lock_changed(ctx)
            cmd += [
                "--cache",
                "--cache-strategy",
                "content",
                "--cache-location",
                str(cache_file),
            ]
        cmd.append("src/")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(ctx.web_dir),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout:
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return findings

            for file_result in data:
                file_name = file_result.get("filePath", "")
                for msg in file_result.get("messages", []):
                    rule_id = msg.get("ruleId") or "unknown"
                    findings.append(
                        Finding(
                            severity="error" if msg.get("severity") == 2 else "warning",
                            file=file_name,
                            rule=f"V07-ESLINT-{rule_id}",
                            message=msg.get("message", ""),
                            fix=(
                                f"Fix ESLint error '{rule_id}' at "
                                f"{file_name}:{msg.get('line')}: {msg.get('message', '')}"
                            ),
                            line=msg.get("line"),
                        )
                    )

        return findings

    # ── Check 8: Circular imports (Stop mode) ────────────────────────────

    def _check_circular_imports(self, ctx: ProjectContext) -> list[Finding]:
        """Detect circular dependencies using madge.

        Use --json so we get deterministic parseable output: an empty array
        `[]` means no cycles, any other array means cycles were found. The
        previous implementation checked `stdout.strip()` which matched
        madge's success banner ("✔ No circular dependency found!") and
        produced a guaranteed false positive on every successful run.

        Phase 68: wrapped with ``lib.subprocess_cache.cached_run`` since
        madge has no native cache and a single invocation costs ~1.5 s
        on a typical web project (it walks all .ts/.tsx files + builds
        an import graph). Cache key = (.ts/.tsx files via Phase 65
        ``ctx.file_index`` + tsconfig.json + madge version). 7-day FIFO.
        Cycles being deterministic from inputs makes the cache safe.
        """
        findings: list[Finding] = []
        web_dir = ctx.web_dir if ctx.web_dir else (Path(ctx.project_root) / "web")
        if not web_dir.exists():
            return findings

        # Phase 68 + Phase 65: build the input-file list from the shared
        # project index. This restricts the hash to .ts/.tsx files under
        # web_dir and inherits the index's exclude.paths pruning.
        ts_files = [
            p
            for p in ctx.file_index.find_by_pattern("*.ts", "*.tsx")
            if str(p).startswith(str(web_dir / "src"))
        ]
        if not ts_files:
            return findings

        config_files = [p for p in (web_dir / "tsconfig.json", web_dir / "package.json") if p.is_file()]

        try:
            from lib.subprocess_cache import cached_run, detect_tool_version

            madge_version = detect_tool_version(["bunx", "madge", "--version"], cwd=web_dir)
            result = cached_run(
                project_root=ctx.project_root,
                label="V07-madge-circular",
                cmd=["bunx", "madge", "--circular", "--json", "--extensions", "ts,tsx", "src/"],
                cwd=web_dir,
                input_files=ts_files,
                tool_version=madge_version,
                config_files=config_files,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        # madge --json writes `[]` on success and an array of cycle arrays
        # on failure. Parse it; on parse failure, fall through to the legacy
        # regex-based extraction so we don't regress older madge versions.
        cycles: list[str] = []
        stdout = result.stdout.strip()

        if stdout.startswith("["):
            try:
                data = json.loads(stdout)
                if isinstance(data, list):
                    for cycle in data:
                        if isinstance(cycle, list) and cycle:
                            cycles.append(" > ".join(str(x) for x in cycle))
                        elif isinstance(cycle, str) and cycle.strip():
                            cycles.append(cycle.strip())
            except json.JSONDecodeError:
                pass
        else:
            # Legacy text fallback: real cycles look like `a.ts > b.ts > a.ts`.
            # Filter out madge's own status banners so we don't count them as
            # cycles (the root cause of the false positive we're fixing).
            banner_markers = (
                "No circular dependency found",
                "Processed ",
                "Finding files",
                "Skipped",
            )
            for line in stdout.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if any(marker in line for marker in banner_markers):
                    continue
                cycles.append(line)

        if cycles:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(ctx.web_dir / "src"),
                    rule="V07-CIRCULAR-IMPORT",
                    message=f"Circular imports detected: {len(cycles)} cycles found",
                    fix=(
                        f"Break circular dependencies. Cycles: "
                        f"{'; '.join(cycles[:3])}{'...' if len(cycles) > 3 else ''}"
                    ),
                )
            )

        return findings

    # ── Check 9: Unused code (Stop mode) ─────────────────────────────────

    def _check_unused_code(self, ctx: ProjectContext) -> list[Finding]:
        """Detect unused exports/files/dependencies using knip.

        Phase 70: wrap with ``lib.subprocess_cache.cached_run`` (Phase 61
        infrastructure, same pattern as Phase 68 madge). knip reads
        every .ts/.tsx file under web/src + tsconfig.json + package.json
        + knip.json (if present) to compute unused exports/files. Output
        is deterministic from those inputs; caching is safe.

        cProfile measured this at 722 ms warm — 20% of V07's wall.
        """
        findings: list[Finding] = []
        web_dir = ctx.web_dir if ctx.web_dir else (Path(ctx.project_root) / "web")
        if not web_dir.exists():
            return findings

        ts_files = [
            p
            for p in ctx.file_index.find_by_pattern("*.ts", "*.tsx")
            if str(p).startswith(str(web_dir / "src"))
        ]
        if not ts_files:
            return findings

        config_files = [
            p
            for p in (
                web_dir / "tsconfig.json",
                web_dir / "package.json",
                web_dir / "knip.json",
                web_dir / "knip.config.js",
                web_dir / "knip.config.ts",
            )
            if p.is_file()
        ]

        try:
            from lib.subprocess_cache import cached_run, detect_tool_version

            knip_version = detect_tool_version(["bunx", "knip", "--version"], cwd=web_dir)
            result = cached_run(
                project_root=ctx.project_root,
                label="V07-knip-unused",
                cmd=["bunx", "knip", "--no-progress"],
                cwd=web_dir,
                input_files=ts_files,
                tool_version=knip_version,
                config_files=config_files,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return findings

        if result.returncode != 0 and result.stdout.strip():
            sections = result.stdout.strip().split("\n\n")
            for section in sections:
                if section.strip():
                    findings.append(
                        Finding(
                            severity="warning",
                            file=str(ctx.web_dir),
                            rule="V07-UNUSED-CODE",
                            message=f"Unused code detected: {section.strip()[:200]}",
                            fix="Remove unused exports/files/dependencies reported by knip",
                        )
                    )

        return findings

    # ── Check 9: Vite env.d.ts typed enforcement (Phase48) ────────────────

    def _check_vite_env_typed(self, ctx: ProjectContext) -> list[Finding]:
        """V07-VITE-ENV-TYPED: every ``import.meta.env.VITE_*`` referenced in
        the codebase must be declared in ``web/src/vite-env.d.ts`` (or
        ``env.d.ts``) so TypeScript narrows the access type.

        Without the typing, ``import.meta.env.VITE_FOO`` silently has type
        ``string | undefined`` (or worse, ``any``) and the user's code can
        cast / coerce in ways that hide undefined-at-runtime.

        Phase27 audit (V07 보강): the user's project uses Vite + a single
        ``vite-env.d.ts`` declaration; without enforcement, new VITE_*
        env vars are added in code without the corresponding type entry.
        """
        if not ctx.web_dir or not ctx.web_dir.exists():
            return []

        # Locate the env.d.ts file (Vite default name first, then env.d.ts)
        env_dts: Path | None = None
        for candidate_name in ("vite-env.d.ts", "env.d.ts"):
            candidate = ctx.web_dir / "src" / candidate_name
            if candidate.is_file():
                env_dts = candidate
                break

        # Collect every VITE_* reference across web/src
        vite_refs: dict[str, tuple[str, int]] = {}  # name → (file, line)
        VITE_REF = re.compile(r"\bimport\.meta\.env\.(VITE_[A-Z0-9_]+)")
        src_root = ctx.web_dir / "src"
        if not src_root.is_dir():
            return []

        for ts_file in list(src_root.rglob("*.ts")) + list(src_root.rglob("*.tsx")):
            try:
                src = ts_file.read_text(errors="replace")
            except OSError:
                continue
            # Skip the env.d.ts itself
            if env_dts and ts_file.resolve() == env_dts.resolve():
                continue
            for line_no, line in enumerate(src.splitlines(), 1):
                for m in VITE_REF.finditer(line):
                    name = m.group(1)
                    if name not in vite_refs:
                        vite_refs[name] = (str(ts_file), line_no)

        if not vite_refs:
            return []

        findings: list[Finding] = []

        # Case 1: no env.d.ts at all but VITE_* references exist
        if env_dts is None:
            for name, (file_path, line_no) in vite_refs.items():
                findings.append(
                    Finding(
                        severity="warning",
                        file=file_path,
                        line=line_no,
                        rule="V07-VITE-ENV-TYPED",
                        message=(
                            f"`import.meta.env.{name}` is referenced but no "
                            "vite-env.d.ts / env.d.ts exists in web/src/. "
                            "TypeScript can't narrow the access type."
                        ),
                        fix=(
                            "Create web/src/vite-env.d.ts with an "
                            "`interface ImportMetaEnv { readonly "
                            f"{name}: string }}` declaration."
                        ),
                    )
                )
            return findings

        # Case 2: env.d.ts exists but is missing some references
        try:
            env_src = env_dts.read_text(errors="replace")
        except OSError:
            return findings

        # Collect declared keys: any line that looks like `readonly VITE_X:` or `VITE_X:`
        DECL = re.compile(r"\b(VITE_[A-Z0-9_]+)\s*[:?]")
        declared = {m.group(1) for m in DECL.finditer(env_src)}

        for name, (file_path, line_no) in vite_refs.items():
            if name in declared:
                continue
            findings.append(
                Finding(
                    severity="warning",
                    file=file_path,
                    line=line_no,
                    rule="V07-VITE-ENV-TYPED",
                    message=(
                        f"`import.meta.env.{name}` is referenced but not "
                        f"typed in {env_dts.relative_to(ctx.web_dir)}. "
                        "TypeScript falls back to `string | undefined` / `any`."
                    ),
                    fix=(
                        f"Add `readonly {name}: string` to "
                        f"`interface ImportMetaEnv` in "
                        f"{env_dts.relative_to(ctx.web_dir)}."
                    ),
                )
            )
        return findings


# ── Standalone execution ─────────────────────────────────────────────────────


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
    validator = TsQualityValidator()

    if not validator.should_run(file_path):
        write_hook_output({})
        return

    result = validator.run(ctx, file_path, mode="post_tool_use")

    from hooks.validators.base import format_output

    output = format_output(result.findings, mode="post_tool_use")
    write_hook_output(output)


if __name__ == "__main__":
    main()
