#!/usr/bin/env python3
# Blocks Claude attribution (PR footer, Co-Authored-By trailer) so it never reaches commits, PR/MR bodies or files.
import json, re, sys

PATTERN = re.compile(r"generated\s+with\s+\[?claude\s+code|co-authored-by:\s*claude", re.I)

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
inp = data.get("tool_input") or {}
texts = [inp.get(k) for k in ("command", "content", "new_string")]
texts += [e.get("new_string") for e in inp.get("edits") or [] if isinstance(e, dict)]
if any(isinstance(t, str) and PATTERN.search(t) for t in texts):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Remove Claude attribution (the PR/MR footer line and the Co-Authored-By Claude trailer); user rule: never add them to commits, PR/MR descriptions or files. Retry without them.",
        }
    }))
sys.exit(0)
