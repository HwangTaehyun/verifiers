#!/usr/bin/env python3
"""Merge verifier hooks into Claude Code settings.json.

Adds Tier 1 (PostToolUse → security_hook.py) and Tier 3 (Stop → stop_validator.py)
to the global ~/.claude/settings.json while preserving existing hooks.

Usage:
    uv run scripts/merge_settings.py                           # global (~/.claude/settings.json)
    uv run scripts/merge_settings.py --settings-path /path/to/settings.json  # project-specific
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Default: global settings
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Allow override via --settings-path argument
for i, arg in enumerate(sys.argv[1:], 1):
    if arg == "--settings-path" and i < len(sys.argv):
        SETTINGS_PATH = Path(sys.argv[i + 1])
        break

# Verifiers directory (parent of scripts/)
VERIFIERS_DIR = Path(__file__).parent.parent.resolve()

# Hook definitions to add
TIER1_HOOK = {
    "matcher": "Edit|Write|MultiEdit",
    "hooks": [
        {
            "type": "command",
            "command": f"uv run --script {VERIFIERS_DIR}/hooks/security_hook.py",
            "timeout": 10,
        }
    ],
}

TIER3_HOOK = {
    "hooks": [
        {
            "type": "command",
            "command": f"uv run --script {VERIFIERS_DIR}/hooks/stop_validator.py",
            "timeout": 120,
        }
    ],
}

# Marker to identify our hooks for uninstall
MARKER = "verifiers/"


def load_settings() -> dict:
    """Load existing settings or create empty dict."""
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            print(f"Warning: {SETTINGS_PATH} has invalid JSON, creating backup")
            backup = SETTINGS_PATH.with_suffix(".json.bak")
            SETTINGS_PATH.rename(backup)
    return {}


def save_settings(settings: dict) -> None:
    """Save settings with pretty formatting."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def is_our_hook(hook_entry: dict) -> bool:
    """Check if a hook entry belongs to verifiers (by command path)."""
    for h in hook_entry.get("hooks", []):
        cmd = h.get("command", "")
        if MARKER in cmd:
            return True
    return False


def main() -> None:
    settings = load_settings()

    # Ensure hooks structure exists
    hooks = settings.setdefault("hooks", {})

    # ── Add Tier 1: PostToolUse ──
    post_tool_use = hooks.setdefault("PostToolUse", [])

    # Remove any existing verifier PostToolUse hooks
    post_tool_use[:] = [h for h in post_tool_use if not is_our_hook(h)]

    # Add our hook
    post_tool_use.append(TIER1_HOOK)

    # ── Add Tier 3: Stop ──
    stop = hooks.setdefault("Stop", [])

    # Remove any existing verifier Stop hooks
    stop[:] = [h for h in stop if not is_our_hook(h)]

    # Add our hook
    stop.append(TIER3_HOOK)

    save_settings(settings)
    print(f"✅ Hooks merged into {SETTINGS_PATH}")
    print("   Tier 1: PostToolUse → security_hook.py")
    print("   Tier 3: Stop → stop_validator.py")


if __name__ == "__main__":
    main()
