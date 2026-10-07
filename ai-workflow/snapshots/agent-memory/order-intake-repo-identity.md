---
name: order-intake-repo-identity
description: Git identity and GitHub remote for the Provectus take-home repo order-intake-ai
metadata:
  node_type: memory
  type: project
  originSessionId: c3abb670-8405-4b50-bf61-0cd3b9dedd84
  modified: 2026-10-07T17:35:11.721Z
---

The take-home repo (this repository) commits as `Volodymyr <volod.shu.work@gmail.com>`, set in the repo-local git config. History was rewritten on 2026-10-07 to replace the global identity. The remote is the **public** repo https://github.com/volsSha/order-intake-ai, owned by the gh account volsSha.

**Why:** the user wants the employer to see this identity rather than their personal global git identity, and chose a public repo. The brief only requires "a Git repository with no sensitive data"; it does not say private.

**How to apply:**
- Never commit with the global identity in this repo.
- Every push is public, so scan for secrets before pushing: `.env` and `*.swp` are gitignored.
