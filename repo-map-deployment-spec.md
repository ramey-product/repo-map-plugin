# Repo-Map: Deployment Specification

> **Companion to:** `agentic-search-architecture.md`
> **Scope:** Productization of the composite search algorithm as a Claude Code / Cowork plugin that builds a persistent, token-efficient repository map across multiple agent sessions.
> **Status:** Draft — Design Specification

---

## 1. Operational Model

### 1.1 Core Loop

The system operates in bounded sessions, each constrained by the LLM's context window. A single session cannot map an entire large repository. Instead, the agent performs one exploration pass, builds as much of the map as the token budget allows, then serializes state for the next session to continue.

```
Session N:
  1. Load compact index + frontier queue from working directory
  2. Resume exploration from highest-priority frontier entry
  3. Explore → summarize → index (repeat until budget threshold)
  4. Serialize updated map + new frontier to disk
  5. Write coverage report to handoff document

Session N+1:
  1. Load updated index + frontier
  2. (Optional) Detect repo drift via git diff since last session
  3. Re-prioritize frontier based on drift + remaining coverage gaps
  4. Continue exploration → summarize → index cycle
  ...
```

The map converges over sessions. Early sessions yield the highest marginal value (structural understanding, entry points, core business logic). Later sessions fill in long-tail details with diminishing returns.

### 1.2 Three Operating Modes

| Mode | Trigger | Description |
|------|---------|-------------|
| **Explore** | `repo-map explore` or first run | Build/extend the map. Consumes full token budget. Produces updated map + frontier. |
| **Query** | Automatic when map exists in working directory | Use existing map to answer questions. Loads compact index; pulls detail tiers on demand. No exploration. |
| **Update** | `repo-map update` or scheduled run | Detect drift via `git diff`. Re-explore only changed/new files. Invalidate stale entries. Minimal token cost when repo is stable. |

Query mode should activate transparently — any Claude Code / Cowork session that detects a map in the working directory should use it for context without the user having to invoke anything. Explore and Update are explicit actions.

---

## 2. Context Window Budget Partitioning

### 2.1 The Budget Problem

A 200K-token context window is not 200K tokens of usable map space. Multiple consumers compete for the same budget:

```
┌─────────────────────────────────────────────┐
│              200K Token Window               │
├──────────────────┬──────────────────────────┤
│ System Prompt    │  ~3-5K tokens            │
│ Skill/Plugin     │  ~2-4K tokens            │
│ Instructions     │                          │
├──────────────────┼──────────────────────────┤
│ Map: Compact     │  ~10-25K tokens          │
│ Index (always    │  (scales with repo size) │
│ loaded)          │                          │
├──────────────────┼──────────────────────────┤
│ Map: Detail      │  ~0-40K tokens           │
│ Tiers (on-demand)│  (loaded per query)      │
├──────────────────┼──────────────────────────┤
│ Exploration      │  ~80-140K tokens         │
│ Budget (reads,   │  (shrinks as map grows)  │
│ tool calls,      │                          │
│ summaries)       │                          │
├──────────────────┼──────────────────────────┤
│ Conversation     │  ~10-20K tokens          │
│ History          │  (user messages, output) │
├──────────────────┼──────────────────────────┤
│ Safety Margin    │  ~10-15K tokens          │
│ (handoff write)  │                          │
└──────────────────┴──────────────────────────┘
```

### 2.2 Budget Accounting

The agent must track token consumption in real time during exploration. Key thresholds:

| Threshold | % of Usable Budget | Action |
|-----------|-------------------|--------|
| Green | 0-60% consumed | Full exploration — read files, generate summaries, index |
| Yellow | 60-80% consumed | Shift to structural-only exploration — directory listings, file metadata, skip deep reads |
| Red | 80-90% consumed | Stop exploring. Begin serialization of map + frontier. |
| Critical | 90%+ consumed | Emergency serialize. Write minimal frontier. Abort gracefully. |

**Usable budget** = Total context window − system prompt − skill instructions − safety margin.

For a 200K window, usable budget is approximately **150-165K tokens** depending on skill verbosity and conversation history.

### 2.3 The Shrinking Exploration Window

Each session, the compact index grows as more of the repo is mapped. This means the exploration budget shrinks over sessions:

```
Session 1:  Index ~5K   → Exploration budget ~145K  (best exploration session)
Session 3:  Index ~12K  → Exploration budget ~138K  (still productive)
Session 8:  Index ~22K  → Exploration budget ~128K  (diminishing but useful)
Session 15: Index ~25K  → Exploration budget ~125K  (near convergence)
```

The compact index should plateau at 20-30K tokens for most repos due to the tiered design — only structural metadata lives in the always-loaded tier. If the index threatens to exceed this, the agent must compress older entries (merge sibling summaries, drop low-value paths).

---

## 3. Map Artifact Design

### 3.1 File Structure

The map lives in the user's working directory and persists across sessions:

```
.repo-map/
├── index.md              # Tier 1: Compact structural index (always loaded)
├── meta.json             # Session metadata, coverage stats, config
├── frontier.md           # Exploration queue for next session
├── .gitignore            # Exclude from version control
├── details/
│   ├── src-core.md       # Tier 2: Module-level summaries
│   ├── src-api.md
│   ├── src-auth.md
│   ├── config.md
│   └── ...               # One file per logical module/directory subtree
└── deep/
    ├── src-core-userservice.md    # Tier 3: Function-level detail
    ├── src-api-endpoints.md
    └── ...               # Created on-demand during query resolution
```

### 3.2 Tier Definitions

| Tier | File | Token Budget | Content | When Loaded |
|------|------|-------------|---------|-------------|
| **T1: Structural Index** | `index.md` | 10-25K | Directory tree, file list with 1-line descriptions, dependency graph edges, entry points, tech stack summary | Always — every session |
| **T2: Module Summaries** | `details/*.md` | 200-800 per file | 2-4 sentence summary per file, key exports/classes, relationships to other modules, change frequency | On-demand — when query touches that module |
| **T3: Deep Detail** | `deep/*.md` | 500-2000 per file | Function signatures, logic flow summaries, inline documentation, notable patterns | On-demand — when T2 is insufficient for the query |
| **T4: Source Read** | (not stored) | 2000+ per file | Direct file read from repo | Last resort — triggers auto-generation of T2/T3 summary |

**Critical design principle:** T4 reads (raw source) should always produce a T2 or T3 summary as a side effect. Every token spent reading source material should permanently enrich the map. No read should be "wasted."

### 3.3 Compact Index Format (`index.md`)

The index must be token-dense and machine-parseable while remaining human-readable. Proposed format:

```markdown
# Repo Map: {repo-name}
> Generated: 2026-03-09 | Commit: a3f8c2d | Coverage: 412/1847 files (22%)
> Tech: ASP.NET WebForms (VB.NET) + .NET Core Razor (C#) | DB: SQL Server
> Entry points: Works/Default.aspx, WorksCore.Web/Program.cs

## Structure
works/
  Works/                          # Legacy WebForms app (VB.NET) [1247 files]
    App_Code/                     # Shared server code
      Controllers/                # API controllers (~70) → details/controllers.md
      Data/                       # DataProvider classes (~12) → details/data.md
      Models/                     # DTOs and view models
      BasePage.vb                 # Page base class — auth, context, feature flags
    OrderProcessor/               # Role: full WO feature set → details/order-processor.md
    OrderApprover/                # Role: WO approval (parity w/ OrderProcessor)
    ...
    UFP/                          # S&R sub-module (~249 pages) → details/ufp.md
    Inspector/                    # FIT sub-app (~78 pages) → details/inspector.md
  WorksCore.Web/                  # .NET Core Razor Pages (C#) [migrated features]
  Database/sprocs/                # 610 stored procedures → details/sprocs.md
  components/works-search/        # React/TS Smart Search → details/smart-search.md

## Key Patterns
- Role-based folder parity: WO pages duplicated across 6+ role folders
- DataHelpers.vb: Central SQL gateway — sproc calls distributed, not centralized
- Dual-framework: Legacy WebForms + .NET Core running simultaneously
- ...

## Dependency Edges
BasePage.vb → {all .aspx pages}
DataHelpers.vb → {all controllers, code-behinds, reports}
MCP.dbml → {LINQ-to-SQL consumers}
FwDbContext.cs → {EF Core consumers}
```

This format gives the agent a structural map of the entire repo in ~10-15K tokens, with pointers to detail files for deeper exploration.

### 3.4 Meta File (`meta.json`)

```json
{
  "repo_name": "facilitron-works",
  "repo_root": "/path/to/works",
  "last_commit": "a3f8c2d",
  "last_run": "2026-03-09T14:30:00Z",
  "sessions_completed": 3,
  "coverage": {
    "files_mapped": 412,
    "files_total": 1847,
    "directories_fully_explored": 38,
    "directories_total": 142,
    "estimated_sessions_remaining": 4
  },
  "index_token_estimate": 14200,
  "detail_files": 12,
  "detail_total_tokens": 8400,
  "config": {
    "ignore_patterns": ["node_modules/", "*.min.js", "bin/", "obj/"],
    "priority_patterns": ["*.vb", "*.cs", "*.aspx", "*.cshtml", "*.tsx"],
    "max_file_read_tokens": 3000,
    "budget_green_threshold": 0.60,
    "budget_yellow_threshold": 0.80,
    "budget_red_threshold": 0.90
  }
}
```

---

## 4. Handoff Protocol

### 4.1 Frontier Document (`frontier.md`)

The frontier is the exploration queue — a prioritized list of unexplored paths with estimated token cost and priority score. It is consumed at the start of each session and regenerated at the end.

```markdown
# Exploration Frontier
> Session 3 → Session 4 | Remaining: ~1,435 files unmapped

## High Priority (core business logic, frequently referenced)
- [ ] Works/App_Code/Controllers/WorkOrdersController.vb  ~800 tok  P:95
- [ ] Works/App_Code/Controllers/ApiEquipmentController.vb  ~600 tok  P:90
- [ ] Database/sprocs/tom_*.sql (batch: 260 sprocs)  ~12000 tok  P:85
- [ ] Works/OrderProcessor/WorkOrderDetailForm.aspx.vb  ~1200 tok  P:82

## Medium Priority (supporting modules, less referenced)
- [ ] Works/UserControls/ (batch: 30+ .ascx files)  ~4000 tok  P:60
- [ ] Works/Reports/ (batch)  ~3000 tok  P:55
- [ ] WorksCore.Infrastructure/DataProviders/  ~2500 tok  P:50

## Low Priority (long-tail, rarely referenced)
- [ ] Works/Locksmith/ (6 pages)  ~800 tok  P:20
- [ ] Works/ToolRoom/ (7 pages)  ~900 tok  P:18
- [ ] Works/Anonymous/ (2 pages)  ~300 tok  P:10

## Skipped (binary, generated, vendored)
- Works/Js/lib/  (vendored libraries — skip)
- Works/bin/  (build output — skip)
- Works/Images/  (binary assets — skip)
```

### 4.2 Priority Scoring

Frontier entries are scored on a 0-100 scale using four factors:

| Factor | Weight | Signal |
|--------|--------|--------|
| **Structural centrality** | 30% | How many other files reference or depend on this file |
| **Query relevance** | 25% | Has the user (or prior sessions) asked about nearby content |
| **Freshness** | 25% | Recently modified files in git log are more likely to be relevant |
| **Coverage gap** | 20% | Files in unmapped directories score higher than isolated unmapped files |

Priority is recalculated at the start of each session based on git diff output and any user queries from the current conversation.

### 4.3 Handoff Sequence

When the budget hits the Red threshold (80-90% consumed):

```
1. STOP exploration immediately
2. Flush any in-progress summaries to detail files
3. Regenerate frontier.md with updated priorities
4. Update meta.json (coverage stats, commit hash, timestamp)
5. Update index.md with any new structural entries
6. Write session summary to stdout:
   "Session 3 complete. Mapped 127 new files (412/1847 total, 22%).
    Next session will prioritize: WorkOrdersController.vb, sprocs batch.
    Estimated 4-6 sessions remaining for 80% coverage."
```

---

## 5. Exploration Heuristics

### 5.1 Session 1: Cold Start Strategy

The first session has no map and no context. It must maximize structural understanding per token:

```
Phase 1: Structural Scan (~5-10K tokens)
  - `find . -type f | head -2000` → file listing
  - `find . -type d` → directory tree
  - Read package.json / *.csproj / Makefile / Dockerfile (build config)
  - Read README.md / CONTRIBUTING.md (project docs)
  - `git log --oneline -50` → recent activity
  - `git shortlog -sn` → contributor map

Phase 2: Entry Point Analysis (~10-20K tokens)
  - Identify and read entry points (main files, startup configs, routing)
  - Read base classes / shared utilities (high fan-out files)
  - Build initial dependency graph from imports/references

Phase 3: Priority Exploration (~80-100K tokens)
  - Deep read highest-centrality files identified in Phase 2
  - Generate T2 summaries for each file read
  - Build frontier queue from references found in explored files
```

### 5.2 Subsequent Sessions: Warm Start

```
1. Load index.md + frontier.md + meta.json
2. Run `git diff {last_commit}..HEAD --name-only`
   → If changes detected: re-prioritize frontier, mark stale entries
   → If no changes: proceed with existing frontier
3. Pop highest-priority entries from frontier
4. For each entry:
   a. Read file (consume T4 tokens)
   b. Generate T2 summary (cheap — agent produces this from the read)
   c. If file is high-centrality, generate T3 detail
   d. Update index.md with new structural entries
   e. Add newly-discovered references to frontier
   f. Check budget threshold → continue or begin handoff
```

### 5.3 File Prioritization Heuristics

**Explore first (high value per token):**
- Entry points and startup files
- Base classes and shared utilities (high fan-out)
- Configuration files (low token cost, high context value)
- Files with many inbound references (imported/called by many others)
- Recently modified files (git recency)
- Files matching user's query history

**Explore later (lower value per token):**
- Test files (unless user specifically asks about testing)
- Generated code (migrations, compiled output)
- Vendored dependencies
- Documentation files (often redundant with code understanding)
- Files with no inbound references (orphaned or rarely used)

**Skip entirely:**
- Binary files (images, fonts, compiled assets)
- `node_modules/`, `vendor/`, `bin/`, `obj/`, `.git/`
- Minified/bundled JavaScript
- Files matching `.gitignore` patterns

---

## 6. Drift Detection and Incremental Updates

### 6.1 Staleness Model

Every map entry stores the content hash (xxHash64) of the source file at the time it was mapped. Staleness is detected by comparing the stored hash to the current file hash.

```json
{
  "path": "Works/App_Code/Controllers/WorkOrdersController.vb",
  "hash": "a3f8c2d91e4b",
  "mapped_at": "2026-03-08T10:15:00Z",
  "mapped_commit": "b7e2f1a",
  "tier": 2,
  "summary_tokens": 340
}
```

### 6.2 Update Mode Flow

```
1. Load meta.json → get last_commit
2. Run `git diff {last_commit}..HEAD --name-only`
3. Categorize changes:
   - Modified files with existing map entries → STALE (re-read and re-summarize)
   - New files → ADD to frontier with high freshness priority
   - Deleted files → REMOVE from index and detail files
   - Renamed files → UPDATE path references in index
4. For STALE entries:
   a. Re-read file
   b. Compare new hash to stored hash
   c. If changed: regenerate T2/T3 summary
   d. If unchanged (e.g., whitespace-only change): update hash, keep summary
5. Update meta.json with new commit hash
```

### 6.3 Efficiency Guarantee

Update mode should be dramatically cheaper than Explore mode when the repo is stable:

| Scenario | Token Cost |
|----------|-----------|
| No changes since last run | ~500 tokens (load meta, run git diff, short-circuit) |
| 5 files changed | ~3,000-5,000 tokens (re-read + re-summarize changed files) |
| Major refactor (100+ files) | Falls back to Explore mode priorities |

---

## 7. Plugin / Skill Architecture

### 7.1 Deployment Target

The repo-map ships as a **skill** (not a full plugin) for both Claude Code and Cowork. This means:

- A `SKILL.md` file containing the agent's instructions for all three modes
- A supporting script (Python or TypeScript) for file I/O, hashing, git operations
- No external dependencies beyond what's available in the Claude Code / Cowork runtime

### 7.2 Skill Structure

```
repo-map/
├── SKILL.md              # Skill instructions (mode routing, exploration protocol,
│                         #   budget management, handoff protocol)
├── scripts/
│   ├── scan.py           # Structural scan (file listing, directory tree, git ops)
│   ├── hash.py           # xxHash64 content hashing for staleness detection
│   ├── budget.py         # Token estimation and budget tracking
│   └── frontier.py       # Priority queue management and scoring
└── templates/
    ├── index-template.md  # Blank index template for Session 1
    ├── frontier-template.md
    └── meta-template.json
```

### 7.3 SKILL.md Responsibilities

The SKILL.md is the primary instruction set the agent reads. It must be lean (under 2K tokens itself) to minimize overhead. It should contain:

1. **Mode detection logic** — Check for `.repo-map/` existence → Query mode if present, Explore mode if not
2. **Budget tracking protocol** — How to estimate tokens consumed, when to check thresholds
3. **Exploration protocol** — Cold start vs warm start decision tree
4. **Summary generation guidelines** — What a good T2 summary looks like (2-4 sentences, key exports, relationships)
5. **Handoff protocol** — Exact steps to serialize state when budget threshold is hit
6. **Drift detection** — When and how to run git diff checks

### 7.4 Query Mode Integration

When the agent detects `.repo-map/index.md` in the working directory, it should:

1. Load the compact index into context (~10-25K tokens)
2. Use the index to answer structural questions directly ("where is the auth logic?")
3. For deeper questions, identify the relevant `details/*.md` file and load it (~200-800 tokens)
4. Only fall through to raw file reads (T4) when T2/T3 are insufficient
5. Any T4 read should produce a T2/T3 summary as a side effect (opportunistic enrichment)

This means the map passively improves over time, even during Query mode sessions, as long as raw reads trigger summary generation.

---

## 8. Scheduling and Operational Considerations

### 8.1 Scheduled Runs

Users can schedule recurring exploration sessions (e.g., nightly, weekly) to keep the map fresh:

- **Nightly (active development):** Update mode — detect drift, re-summarize changed files. Cheap when changes are incremental (~500-5,000 tokens).
- **Weekly (stable repos):** Update mode with frontier extension — detect drift + explore a batch of frontier entries to improve coverage.
- **On-demand:** Full Explore mode — user triggers when they want to significantly extend coverage.

### 8.2 Idempotency

Running the same mode twice on an unchanged repo should be a near-no-op:

- Update mode: `git diff` returns empty → short-circuit, update only `last_run` timestamp
- Explore mode: all frontier entries already mapped → mark as converged, report 100% coverage
- Lock file (`.repo-map/.lock`) prevents concurrent writes if multiple agents run simultaneously

### 8.3 Convergence Detection

The system should report convergence state to the user:

| Coverage | Status | Recommendation |
|----------|--------|----------------|
| 0-20% | **Early** | Run 2-3 more Explore sessions for useful structural coverage |
| 20-50% | **Developing** | Map is useful for common queries. Run 1-2 more for good coverage. |
| 50-80% | **Mature** | Most queries answerable from map. Switch to scheduled Update mode. |
| 80-95% | **Near-complete** | Diminishing returns. Only explore if specific gaps are identified. |
| 95%+ | **Converged** | Map is comprehensive. Update mode only. |

Most repos reach "Mature" (50-80%) within 3-5 Explore sessions, which covers the files that matter for 80%+ of practical queries.

---

## 9. Open Design Decisions

### 9.1 Token Estimation Strategy

How does the agent estimate tokens consumed mid-session? Options:
- **Heuristic:** ~4 characters per token (English text), ~3 characters per token (code). Fast but imprecise.
- **Tiktoken library:** Exact BPE tokenization. Accurate but adds a dependency and computation cost.
- **Model self-report:** Ask the model to estimate its own consumption. Unreliable.

**Recommendation:** Heuristic with a conservative safety margin (assume 3 chars/token for code). The 10-15K safety margin in the budget handles estimation error.

### 9.2 Index Compression at Scale

What happens when the compact index itself approaches 30K+ tokens for very large repos (10,000+ files)?

Options:
- **Hierarchical collapse:** Merge sibling directories into single entries when all children are mapped. `src/utils/{a,b,c,d,e}.ts` → `src/utils/ [5 utility modules, see details/src-utils.md]`
- **LRU eviction:** Drop least-recently-queried entries from the compact index, preserve them in detail files only.
- **Adaptive depth:** Top-level directories get 1 line each; only directories the user has queried get expanded.

**Recommendation:** Hierarchical collapse as default, with adaptive depth expansion based on query history. This keeps the index at a stable 15-25K regardless of repo size.

### 9.3 Multi-Repo Support

Should the skill support mapping multiple repos simultaneously?

- Separate `.repo-map/` per repo root (simple, no cross-contamination)
- Shared index with repo prefixes (allows cross-repo queries but adds complexity)

**Recommendation:** Start with single-repo. Each repo gets its own `.repo-map/`. Cross-repo is a v2 feature.

### 9.4 Summary Quality Validation

How do we ensure T2 summaries are accurate and useful? A bad summary is worse than no summary (it misleads the agent into wrong conclusions).

Options:
- **Self-validation:** Agent re-reads a sample of summarized files and compares summary to source.
- **User feedback loop:** User corrects summaries during Query mode; corrections persist.
- **Hash-based drift:** If the source file hash changes, the summary is automatically invalidated.

**Recommendation:** Hash-based invalidation (automatic) + user corrections (opportunistic). Self-validation is too token-expensive to run routinely.

### 9.5 Privacy and Security

The map contains compressed representations of potentially proprietary source code.

- `.gitignore` the `.repo-map/` directory by default
- Never include secrets, API keys, or credentials in summaries — the summary generation prompt must explicitly exclude sensitive patterns
- Users should be warned if the map is in a shared or synced directory

---

## 10. Estimated Performance Characteristics

### 10.1 Session Economics

| Metric | Session 1 (Cold) | Session 3 (Warm) | Session 5+ (Mature) |
|--------|------------------|-------------------|---------------------|
| Files mapped | 80-150 | 100-200 | 50-100 (diminishing) |
| Tokens consumed | ~150K | ~140K | ~130K |
| Map growth | 5K → 12K | 12K → 18K | 18K → 22K |
| Coverage gain | 0% → 8% | 15% → 25% | 40% → 50% |

### 10.2 Query Mode Savings

| Query Type | Without Map | With Map (Mature) | Savings |
|------------|-------------|-------------------|---------|
| "Where is the auth logic?" | ~5,000 tok (grep + read) | ~50 tok (index lookup) | **100x** |
| "How does WO creation work?" | ~15,000 tok (read 4-5 files) | ~800 tok (T2 summaries) | **19x** |
| "What calls this sproc?" | ~8,000 tok (grep + trace) | ~200 tok (dependency edges) | **40x** |
| "Summarize the OrderProcessor module" | ~25,000 tok (read all files) | ~600 tok (T2 module summary) | **42x** |

### 10.3 Full Convergence Estimate

For a repo with ~1,800 files (like WORKS):
- **Sessions to 50% coverage:** 3-5 Explore sessions (~450-750K total tokens)
- **Sessions to 80% coverage:** 6-10 Explore sessions (~900K-1.5M total tokens)
- **Steady-state maintenance:** ~500-5,000 tokens per Update session

The break-even point — where cumulative token savings from map queries exceed cumulative tokens spent on exploration — is typically reached after 10-20 queries against a map with 30%+ coverage.

---

## References

- `agentic-search-architecture.md` — Algorithmic foundations (BFS/DFS, Radix Trie, HNSW, Bloom Filter, memoization policies, token economics addendum)
- Gong et al., "Ingress: Efficient Incremental Graph Processing," VLDB 2021
- Malkov & Yashunin, "Efficient and Robust Approximate Nearest Neighbor using HNSW Graphs," IEEE TPAMI 2020
- Claude Code documentation — Plugin and skill architecture
