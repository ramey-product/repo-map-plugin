---
name: repo-map
description: Build and query a persistent, token-efficient repository map across sessions.
commands:
  - name: explore
    description: Build or extend the repository map. Consumes full token budget.
  - name: update
    description: Detect drift via git diff. Re-explore only changed/new files.
---

# Repo Map

Persistent repository map that builds across sessions and answers structural questions cheaply.

## 1. Mode Detection

Check for `.repo-map/index.md` in the repository root:

| Condition | Mode |
|-----------|------|
| Missing `.repo-map/` | **Cold Start** — run full init pipeline |
| Exists + command is `explore` | **Warm Start** — load state, continue exploring |
| Exists + command is `update` | **Update** — drift detection, re-map changed files only |
| Exists + no explicit command | **Query** — answer from map, enrich opportunistically |

## 2. Cold Start (first run)

Run the init pipeline from `repo-map/scripts/`:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
python3 repo-map/scripts/scan.py "$REPO_ROOT" > /tmp/scan.json
python3 repo-map/scripts/frontier.py --scan /tmp/scan.json > /tmp/frontier.json
python3 repo-map/scripts/init.py --scan /tmp/scan.json --frontier /tmp/frontier.json --root "$REPO_ROOT"
```

Then explore. Load `.repo-map/index.md` and `.repo-map/frontier.md`. Read entry points listed in the index. For each file read:
1. Generate a T2 summary (purpose, key interfaces, relationships — 2-4 sentences, 200-800 tokens)
2. Write it to `.repo-map/details/{path-slug}.md`
3. Add newly discovered references to the frontier
4. Update `index.md` structure section with the file's 1-line description
5. Check budget — continue if Green (< 60%), structural-only if Yellow (60-80%), stop if Red (>= 80%)

Track consumed tokens with: `python3 repo-map/scripts/budget.py --budget 150000 --consumed <TOKENS>`

## 3. Warm Start (subsequent explore)

1. Load `.repo-map/index.md`, `frontier.md`, `meta.json`
2. Run `git diff $(jq -r .last_commit .repo-map/meta.json)..HEAD --name-only` for drift
   - Changed files with existing summaries → mark stale, re-prioritize
   - New files → add to frontier with high freshness score
   - Deleted files → remove from index
3. Pop highest-priority entries from `frontier.md`
4. For each entry: read file → generate T2 summary → write to `details/` → update `index.md` → add new refs to frontier → check budget
5. Continue until budget hits Red threshold

## 4. Handoff Protocol

When budget reaches Red (>= 80% consumed), **stop exploring immediately** and serialize:

1. Flush all in-progress T2 summaries to `details/*.md`
2. Regenerate `frontier.md` — reprioritize remaining entries, add new discoveries
3. Update `meta.json`: increment `sessions_completed`, set `last_commit` to current HEAD, update `last_run`, recalculate `coverage`
4. Update `index.md` with all new structural entries from this session
5. Report to user:
   > Session N complete. Mapped X new files (Y/Z total, P%).
   > Next session priorities: [top 3 frontier entries].
   > Estimated N sessions remaining for 80% coverage.

## 5. Query Mode

When `.repo-map/index.md` exists and no explore/update command was given:

1. Load `index.md` (~10-25K tokens) — this is the structural map
2. Answer structural questions directly from the index ("where is X?", "what modules exist?")
3. For deeper questions, load the relevant `details/*.md` file (~200-800 tokens each)
4. Only fall through to raw source reads (T4) when T2 detail is insufficient
5. **Every T4 raw read MUST produce a T2 summary as a side effect** — write it to `details/` and update `index.md`. No read should be wasted.

## 6. Summary Guidelines

T2 summaries follow this format:
- **Purpose**: What this file/module does (1 sentence)
- **Key interfaces**: Exported classes, functions, endpoints, or APIs
- **Relationships**: What it imports from / is imported by

Constraints:
- 2-4 sentences per file, 200-800 tokens
- Never include secrets, API keys, credentials, or sensitive data
- Use relative paths for cross-references
- Prefer specifics ("handles JWT auth for /api/users") over vague descriptions ("utility module")
