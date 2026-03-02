"""V12: Commit Discipline — Kent Beck's commit hygiene principles.

"Only commit when ALL tests pass, ALL warnings resolved, change is single logical unit."

Checks:
  V12-MIXED-CHANGE: Structural changes (rename/move) mixed with behavioral changes
  V12-LARGE-DIFF: Too many files modified in a single session
  V12-NO-TEST-IN-FEATURE: Feature code changed without corresponding test changes
  V12-UNSTAGED-CHANGES: Uncommitted changes exist at session end
  V12-COMMIT-MSG-FORMAT: Recent commit doesn't follow Conventional Commits format
"""
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///

from __future__ import annotations

import subprocess
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

import re as _re  # imported here to avoid shadowing at module level

LARGE_DIFF_THRESHOLD = 15  # Files modified

# Conventional Commits prefixes
CONVENTIONAL_COMMIT_PATTERN = _re.compile(
    r"^(feat|fix|refactor|docs|test|chore|style|perf|ci|build|revert)"
    r"(\(.+\))?!?:\s+.+",
)

# ── File classification helpers ─────────────────────────────────────────────

SOURCE_EXTENSIONS = {
    ".go",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".rb",
    ".c",
    ".cpp",
    ".h",
}

TEST_PATTERNS = [
    "_test.go",
    "test_",
    "_test.py",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
    "__tests__/",
]

# Config/infrastructure patterns (not "feature" code)
NON_FEATURE_PATTERNS = [
    "go.mod",
    "go.sum",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "pyproject.toml",
    "uv.lock",
    "Dockerfile",
    "docker-compose",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".md",
    ".txt",
    "Makefile",
    "justfile",
    ".gitignore",
    ".env",
]


def _is_test_file(file_path: str) -> bool:
    """Check if a file is a test file."""
    return any(pattern in file_path for pattern in TEST_PATTERNS)


def _is_source_file(file_path: str) -> bool:
    """Check if a file is a source code file (not test, not config)."""
    if _is_test_file(file_path):
        return False
    ext = Path(file_path).suffix
    if ext not in SOURCE_EXTENSIONS:
        return False
    # Exclude config/infrastructure files
    name = Path(file_path).name
    if any(pattern in name for pattern in NON_FEATURE_PATTERNS):
        return False
    return True


def _run_git(args: list[str], cwd: str) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


class CommitDisciplineValidator(BaseValidator):
    """V12: Commit Discipline — clean commit practices."""

    id = "V12-commit-discipline"
    name = "Commit Discipline"
    file_patterns: list[str] = []  # Stop mode only — all files

    def validate(
        self,
        ctx: ProjectContext,
        file_path: str | None = None,
        mode: str = "post_tool_use",
    ) -> ValidationResult:
        findings: list[Finding] = []

        # Only run in stop mode
        if mode != "stop":
            return ValidationResult(validator_id=self.id, findings=findings)

        cwd = str(ctx.project_root)

        # Get git status
        status = _run_git(["status", "--porcelain"], cwd)
        if not status:
            return ValidationResult(validator_id=self.id, findings=findings)

        status_lines = [line for line in status.split("\n") if line.strip()]

        # Parse status lines
        all_files: list[tuple[str, str]] = []  # (status_code, file_path)
        for line in status_lines:
            if len(line) < 4:
                continue
            code = line[:2].strip()
            file_name = line[3:].strip()
            # Handle renamed files: "R  old -> new"
            if " -> " in file_name:
                file_name = file_name.split(" -> ")[-1]
            all_files.append((code, file_name))

        # ── V12-UNSTAGED-CHANGES ──
        unstaged = [f for code, f in all_files if code and code != "??"]
        untracked = [f for code, f in all_files if code == "??"]
        if unstaged or untracked:
            total = len(unstaged) + len(untracked)
            findings.append(
                Finding(
                    severity="info",
                    file=str(ctx.project_root),
                    rule="V12-UNSTAGED-CHANGES",
                    message=f"{total} file(s) have uncommitted changes ({len(unstaged)} modified, {len(untracked)} untracked)",
                    fix="Review and commit changes before ending the session. Use atomic commits with clear messages.",
                )
            )

        # ── V12-LARGE-DIFF ──
        changed_files = [f for _, f in all_files if _ != "??"]
        if len(changed_files) >= LARGE_DIFF_THRESHOLD:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(ctx.project_root),
                    rule="V12-LARGE-DIFF",
                    message=f"{len(changed_files)} files modified (recommended max {LARGE_DIFF_THRESHOLD}). Consider splitting into atomic commits.",
                    fix="Split changes into smaller, focused commits. Each commit should represent a single logical change.",
                )
            )

        # ── V12-MIXED-CHANGE ──
        findings.extend(self._check_mixed_changes(all_files, cwd))

        # ── V12-NO-TEST-IN-FEATURE ──
        findings.extend(self._check_test_coverage(all_files, ctx))

        # ── V12-COMMIT-MSG-FORMAT ──
        findings.extend(self._check_commit_msg_format(cwd))

        return ValidationResult(validator_id=self.id, findings=findings)

    def _check_mixed_changes(
        self, all_files: list[tuple[str, str]], cwd: str
    ) -> list[Finding]:
        """Check if structural and behavioral changes are mixed.

        Structural: file renames (R), deletes (D) + adds (A) of similar files
        Behavioral: content modifications (M)
        """
        findings: list[Finding] = []

        # Get more detailed status from git diff
        diff_status = _run_git(["diff", "--name-status", "HEAD"], cwd)
        if not diff_status:
            # Try against empty tree (new repo)
            return findings

        renames: list[str] = []
        modifications: list[str] = []

        for line in diff_status.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0]
            if status.startswith("R"):
                renames.append(parts[-1] if len(parts) > 2 else parts[1])
            elif status == "M":
                modifications.append(parts[1])

        # Also check status lines for rename patterns
        for code, fp in all_files:
            if code.startswith("R"):
                if fp not in renames:
                    renames.append(fp)

        if renames and modifications:
            # Filter: only flag if modifications are to source files
            src_mods = [f for f in modifications if _is_source_file(f)]
            if src_mods and renames:
                findings.append(
                    Finding(
                        severity="warning",
                        file=str(Path(cwd)),
                        rule="V12-MIXED-CHANGE",
                        message=(
                            f"Structural changes ({len(renames)} rename(s)) mixed with "
                            f"behavioral changes ({len(src_mods)} source modification(s)). "
                            "Kent Beck recommends separating these."
                        ),
                        fix=(
                            "Commit structural changes (renames, moves, extracts) separately "
                            "from behavioral changes (feature additions, bug fixes). "
                            "This makes git history easier to review."
                        ),
                    )
                )

        return findings

    def _check_test_coverage(
        self, all_files: list[tuple[str, str]], ctx: ProjectContext
    ) -> list[Finding]:
        """Check if feature code changes have corresponding test changes."""
        findings: list[Finding] = []

        source_changes: list[str] = []
        test_changes: list[str] = []

        for _, fp in all_files:
            if _is_test_file(fp):
                test_changes.append(fp)
            elif _is_source_file(fp):
                source_changes.append(fp)

        if source_changes and not test_changes:
            findings.append(
                Finding(
                    severity="warning",
                    file=str(ctx.project_root),
                    rule="V12-NO-TEST-IN-FEATURE",
                    message=(
                        f"{len(source_changes)} source file(s) modified but no test files changed. "
                        "Feature changes should include corresponding tests."
                    ),
                    fix=(
                        "Add or update tests for the changed source files: "
                        + ", ".join(source_changes[:5])
                        + ("..." if len(source_changes) > 5 else "")
                    ),
                )
            )

        return findings

    def _check_commit_msg_format(self, cwd: str) -> list[Finding]:
        """V12-COMMIT-MSG-FORMAT: Check if recent commits follow Conventional Commits.

        Only checks the most recent commit. Uses 'info' severity since this is
        a stylistic recommendation, not a correctness issue.
        """
        findings: list[Finding] = []

        # Get the most recent commit message (subject line only)
        msg = _run_git(["log", "-1", "--format=%s"], cwd)
        if not msg:
            return findings

        # Skip merge commits and initial commits
        if msg.startswith("Merge ") or msg.startswith("Initial commit"):
            return findings

        # Check against Conventional Commits pattern
        if not CONVENTIONAL_COMMIT_PATTERN.match(msg):
            findings.append(
                Finding(
                    severity="info",
                    file=str(cwd),
                    rule="V12-COMMIT-MSG-FORMAT",
                    message=f"Recent commit message doesn't follow Conventional Commits format: \"{msg[:60]}{'...' if len(msg) > 60 else ''}\"",
                    fix=(
                        "Use Conventional Commits format: '<type>(<scope>): <description>'. "
                        "Types: feat, fix, refactor, docs, test, chore, style, perf, ci, build, revert."
                    ),
                )
            )

        return findings


# ── Standalone execution (for skill frontmatter / run_single.py) ─────────────


def main() -> None:
    """Run as standalone Stop hook script."""
    input_data = read_hook_input()
    if not input_data:
        write_hook_output({"decision": "approve"})
        return

    cwd = input_data.get("cwd", ".")
    ctx = ProjectContext(cwd)
    validator = CommitDisciplineValidator()
    result = validator.run(ctx, file_path=None, mode="stop")
    output = format_output(result.findings, mode="stop")
    write_hook_output(output)


if __name__ == "__main__":
    main()
