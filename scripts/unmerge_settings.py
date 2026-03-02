#!/usr/bin/env python3
"""Remove verifier hooks from Claude Code settings.json.

Removes Tier 1 and Tier 3 hooks while preserving other user hooks.

Usage:
    uv run scripts/unmerge_settings.py                           # global (~/.claude/settings.json)
    uv run scripts/unmerge_settings.py --settings-path /path/to/settings.json  # project-specific
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

# Marker to identify our hooks
MARKER = "verifiers/"


def is_our_hook(hook_entry: dict) -> bool:
    """Check if a hook entry belongs to verifiers (by command path)."""
    for h in hook_entry.get("hooks", []):
        cmd = h.get("command", "")
        if MARKER in cmd:
            return True
    return False


def main() -> None:
    if not SETTINGS_PATH.exists():
        print(f"No settings file at {SETTINGS_PATH}")
        return

    try:
        settings = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError:
        print(f"Error: {SETTINGS_PATH} has invalid JSON")
        return

    hooks = settings.get("hooks", {})
    removed = 0

    for event_type in ["PreToolUse", "PostToolUse", "Stop", "SessionStart"]:
        if event_type in hooks:
            original_count = len(hooks[event_type])
            hooks[event_type] = [h for h in hooks[event_type] if not is_our_hook(h)]
            removed += original_count - len(hooks[event_type])

            # Clean up empty arrays
            if not hooks[event_type]:
                del hooks[event_type]

    # Clean up empty hooks object
    if not hooks:
        del settings["hooks"]

    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    print(f"✅ Removed {removed} verifier hook(s) from {SETTINGS_PATH}")


if __name__ == "__main__":
    main()
