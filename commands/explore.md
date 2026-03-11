---
description: Build or extend the repository map
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

Build or extend the repository map for the current git repository. Load the repo-map skill and execute in Cold Start mode (if no `.repo-map/` exists) or Warm Start mode (if it does).

Set `R=$(git rev-parse --show-toplevel)` and use scripts from `${CLAUDE_PLUGIN_ROOT}/skills/repo-map/scripts/`.

Consume the full available token budget to map as many files as possible. Follow the Handoff Protocol (§5 in the skill) when budget reaches red.
