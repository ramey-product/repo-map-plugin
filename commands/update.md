---
description: Detect drift and re-map changed files only
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

Detect changes since the last repo-map session via git diff and re-map only modified files. Load the repo-map skill and execute in Update mode (§3).

Set `R=$(git rev-parse --show-toplevel)` and use scripts from `${CLAUDE_PLUGIN_ROOT}/skills/repo-map/scripts/`.

If no `.repo-map/` directory exists, inform the user to run `/explore` first. If total changes exceed 50, recommend a full `/explore` instead.
