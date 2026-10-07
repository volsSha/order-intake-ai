<!-- Excerpt of ~/.claude/CLAUDE.md (user-level instructions). Omitted: one section about an unrelated knowledge-graph skill. -->
# Safety notice

Always verify with user FIRST before any destructive database operations.
Do not run DB cleanup, Tinker cleanup, destructive SQL, migrate:fresh, migrate:reset, db:wipe, or db:drop without my explicit written approval.
Do not run DROP/truncate for database tables without my explicit written approval.
Do not git commit unless I ask.

# Code comments

Write fewer comments overall — only add one when truly needed (non-obvious WHY, hidden constraint, workaround). Max 1 short line. Never write multi-line explanation blocks — drop background and restatements of the code.

# Shell PATH (WSL, Claude desktop app)

The Bash tool's PATH comes from the desktop app's shell snapshot and lacks ~/.local/bin, nvm Node, ~/.cargo/bin, ~/go/bin, ~/.bun/bin, ~/bin, /snap/bin.
- Before using user-installed tools (glab, uv, uvx, nvm node/npm, cargo, go-installed, bun, claude), prefix the command with `source ~/.zshenv 2>/dev/null;` or call the tool by absolute path.

# Attribution

Never add Claude attribution anywhere: no PR/MR footer line and no "Co-Authored-By" trailer naming Claude in commit messages. Enforced by `attribution.commit = ""`, `attribution.pr = ""` and the PreToolUse hook `~/.claude/hooks/strip-claude-attribution.py`.
