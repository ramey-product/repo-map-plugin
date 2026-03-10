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

## 3. Update Mode (drift detection)

Run when command is `update`. Detects and processes only what changed since last session.

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
python3 repo-map/scripts/drift.py --meta .repo-map/meta.json --root "$REPO_ROOT" --index .repo-map/index.md > /tmp/drift.json
```

If `summary.no_changes` is true → report "No changes detected since last session" and exit.

If `summary.total_changes > 50` → recommend running `repo-map explore` instead (major refactor detected).

Otherwise, process `actions` array in order:

1. **REMOVE** actions: Delete the stale `details/*.md` file, remove the entry from `index.md`
2. **RENAME** actions: Rename `details/{old-slug}.md` to `details/{new-slug}.md`, update the path in `index.md`
3. **REMAP** actions: Re-read the source file → regenerate T2 summary → overwrite `details/{slug}.md` → update `index.md`
4. **ADD** actions: Add the file to `frontier.md` with high priority (freshness boost)

After processing all actions:
- Update `meta.json`: set `last_commit` to `current_commit` from drift output, update `coverage` counts, set `last_run`
- If remaining budget allows, continue with Warm Start exploration (pop frontier entries)

Slug convention for detail files: replace `/`, `.`, and `\` with `-`, collapse multiple hyphens, strip edges, lowercase. Example: `src/utils/helper.py` → `details/src-utils-helper-py.md`.

## 4. Warm Start (subsequent explore)

1. Load `.repo-map/index.md`, `frontier.md`, `meta.json`
2. Check if index needs compression (token estimate > 20K):
   ```bash
   python3 repo-map/scripts/compress.py --index .repo-map/index.md --details-dir .repo-map/details/ --query-history .repo-map/queries.json
   ```
3. Run drift detection for staleness:
   ```bash
   python3 repo-map/scripts/drift.py --meta .repo-map/meta.json --root "$REPO_ROOT" > /tmp/drift.json
   ```
   Process any REMOVE/REMAP/ADD actions before exploring (same steps as Update Mode §3)
4. Pop highest-priority entries from `frontier.md`
5. For each entry: read file → generate T2 summary → write to `details/` → update `index.md` → add new refs to frontier → check budget
6. Continue until budget hits Red threshold
7. If budget is in Green zone and high-centrality files lack T3:
   ```bash
   python3 repo-map/scripts/enrich.py --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json --batch 3
   ```
   Process the top 3 candidates: read source → generate T3 → write to `deep/`

## 5. Handoff Protocol

When budget reaches Red (>= 80% consumed), **stop exploring immediately** and serialize:

1. Flush all in-progress T2 summaries to `details/*.md`
2. Regenerate `frontier.md` — reprioritize remaining entries, add new discoveries
3. Update `meta.json`: increment `sessions_completed`, set `last_commit` to current HEAD, update `last_run`, recalculate `coverage`
4. Update `index.md` with all new structural entries from this session
5. If index.md token estimate > 20K, run compression:
   ```bash
   python3 repo-map/scripts/compress.py --index .repo-map/index.md --details-dir .repo-map/details/ --query-history .repo-map/queries.json
   ```
   Update `meta.json`: set `index_compressed` to true, `compression_level` to number of strategies applied.
6. Report to user:
   > Session N complete. Mapped X new files (Y/Z total, P%).
   > Next session priorities: [top 3 frontier entries].
   > Estimated N sessions remaining for 80% coverage.

## 6. Query Mode

When `.repo-map/index.md` exists and no explore/update command was given:

1. Load `index.md` (~10-25K tokens) — this is the structural map
2. Answer structural questions directly from the index ("where is X?", "what modules exist?")
3. For deeper questions, load the relevant `details/*.md` file (~200-800 tokens each)
3a. If T2 is insufficient, check for `deep/*.md` file (~500-2000 tokens) before falling back to source
4. Only fall through to raw source reads (T4) when T2 and T3 detail are both insufficient
5. **Every T4 raw read MUST produce a T2 summary as a side effect** — write it to `details/` and update `index.md`. No read should be wasted.
6. **Track queries**: Append each query to `.repo-map/queries.json` for hot-path preservation during compression:
   ```json
   {"query": "user's question", "paths_accessed": ["files/read.py"], "timestamp": "ISO-8601"}
   ```
   Create the file if it doesn't exist with `{"queries": []}` as the initial content.
7. **Opportunistic enrichment**: If a T4 raw read was performed and budget allows:
   a. Generate T3 deep-dive content (function index, logic flow, patterns)
   b. Write to `.repo-map/deep/{path-slug}.md`
   c. Update meta.json: increment deep_files count
   Run: `python3 repo-map/scripts/enrich.py --file {path} --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json`

## 7. Summary Guidelines

T2 summaries follow this format:
- **Purpose**: What this file/module does (1 sentence)
- **Key interfaces**: Exported classes, functions, endpoints, or APIs
- **Relationships**: What it imports from / is imported by

Constraints:
- 2-4 sentences per file, 200-800 tokens
- Never include secrets, API keys, credentials, or sensitive data
- Use relative paths for cross-references
- Prefer specifics ("handles JWT auth for /api/users") over vague descriptions ("utility module")

T3 deep-dives follow this format:
- **Function/Class Index**: Every exported function with signature and 1-line description
- **Logic Flow**: Prose description of the main execution path
- **Notable Patterns**: Design patterns, error handling approach, thread-safety
- **Internal Dependencies**: What this file calls and what calls it

T3 Constraints:
- 500-2000 tokens per file
- Function signatures must be exact (copied from source)
- Logic flow in prose, not code blocks
