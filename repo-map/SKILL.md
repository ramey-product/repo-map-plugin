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

All scripts in `repo-map/scripts/`. Set `R=$(git rev-parse --show-toplevel)` before running.

## 1. Mode Detection

Check for `.repo-map/index.md`:

| .repo-map? | Command | Mode |
|---|---|---|
| No | any | Cold Start (§2) |
| Yes | explore | Warm Start (§4) |
| Yes | update | Update (§3) |
| Yes | none | Query (§6) |

## 2. Cold Start

Run: `scan.py "$R" > /tmp/scan.json && frontier.py --scan /tmp/scan.json > /tmp/frontier.json && init.py --scan /tmp/scan.json --frontier /tmp/frontier.json --root "$R"`

Then explore: load `.repo-map/index.md` and `frontier.md`. For each entry point file:
1. Generate T2 summary → write to `details/{slug}.md`
2. Add discovered references to frontier
3. Update `index.md` with 1-line description
4. Check budget via `budget.py --budget 150000 --consumed <N>` — continue if green, structural-only if yellow, stop if red

## 3. Update Mode

Run: `drift.py --meta .repo-map/meta.json --root "$R" --index .repo-map/index.md > /tmp/drift.json`

If `summary.no_changes` → report and exit. If `summary.total_changes > 50` → recommend full explore instead.

Process `actions` array: **REMOVE** → delete stale detail + index entry. **RENAME** → rename detail file + update index path. **REMAP** → re-read source, regenerate T2, overwrite detail + index. **ADD** → add to frontier with high priority.

After actions: update `meta.json` (set `last_commit`, `coverage`, `last_run`). If budget allows, continue with Warm Start exploration.

## 4. Warm Start

1. Load `index.md`, `frontier.md`, `meta.json`
2. If index > 20K tokens: `compress.py --index .repo-map/index.md --details-dir .repo-map/details/ --query-history .repo-map/queries.json`
3. Run drift detection + process actions (§3)
4. Pop highest-priority frontier entries → read file → generate T2 → write detail → update index → add refs to frontier → check budget
5. Continue until budget hits red
6. If green and high-centrality files lack T3: `enrich.py --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json --batch 3` — process top 3 candidates

## 5. Handoff Protocol

When budget reaches red, stop and serialize:
1. Flush in-progress T2 summaries to `details/`
2. Regenerate `frontier.md` with reprioritized entries
3. Update `meta.json`: increment `sessions_completed`, set `last_commit`, `last_run`, recalculate `coverage`
4. Update `index.md` with new structural entries
5. If index > 20K tokens, run compress.py. Set `meta.json` `index_compressed` and `compression_level`.
6. Report: Session N complete. X new files (Y/Z total, P%). Top 3 frontier priorities. Estimated sessions remaining.

## 6. Query Mode

When `.repo-map/index.md` exists and no explore/update command:
1. Load `index.md` — answer structural questions directly
2. For deeper questions, load relevant `details/*.md`
3. If T2 insufficient, check `deep/*.md` before raw source
4. Only read raw source (T4) when T2+T3 insufficient
5. Every T4 read MUST produce a T2 summary as side effect
6. Track queries: append `{query, paths_accessed, timestamp}` to `.repo-map/queries.json` (create with `{"queries": []}` if missing)
7. Opportunistic enrichment: if T4 read + budget allows, generate T3 via `enrich.py --file {path} --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json`

## 7. Summary Guidelines

**T2** (`details/*.md`): Purpose, key interfaces, relationships. 2-4 sentences, 200-800 tokens. No secrets. Relative paths. Prefer specifics over vague descriptions.

**T3** (`deep/*.md`): Function index (exact signatures), logic flow (prose), notable patterns, internal deps. 500-2000 tokens.
