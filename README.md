# repo-map

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)

A Claude Code / Cowork plugin that builds persistent, token-efficient repository maps across sessions. Instead of re-reading source files every conversation, the plugin maintains a tiered map that grows incrementally — giving the agent structural understanding of your entire codebase at a fraction of the token cost.

## The Problem

Every time an AI agent starts a new session on a large codebase, it has to re-read source files to understand the project. For a 650-file repo, that's ~640K tokens *per session* — most of the context window consumed before the agent can even start working.

## The Solution

Repo-map builds a layered, persistent index that lives in your working directory (`.repo-map/`). The agent loads a compact structural map (~10K tokens) instead of raw source, pulling deeper detail only when needed. The map persists across sessions — no re-reading, no wasted tokens.

## Features

- **Tiered summaries** — T1 (compact index), T2 (file summaries), T3 (deep-dives), T4 (raw source). Claude reads the cheapest tier that answers the question.
- **Frontier-based exploration** — Prioritizes high-centrality files first. Entry points and heavily-imported modules get mapped before leaf files.
- **Drift detection** — On update, diffs against the last mapped commit. Only re-maps changed files.
- **Index compression** — When the index grows too large, automatically compresses low-value entries while preserving hot paths from query history.
- **Budget-aware** — Tracks token consumption with green/yellow/red zones. Stops exploring before exhausting the context window.
- **Zero dependencies** — Pure Python 3.9+ stdlib. No pip install required.

## Requirements

- Python 3.9+
- Git
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the skill runs inside Claude Code sessions)

## Installation

### Option A: Install as a plugin (Cowork / Claude Code)

Download the `.plugin` file from the [latest release](https://github.com/ramey-product/repo-map-plugin/releases) and install it via the plugin manager.

### Option B: Clone into your skills directory (Claude Code)

```bash
cd ~/.claude/skills
git clone https://github.com/ramey-product/repo-map-plugin.git repo-map-plugin
```

### Usage

Navigate to any Git repository and use the commands:

   ```
   /explore    # Build or extend the repository map
   /update     # Detect drift and re-map changed files only
   ```

   Or just ask questions — if a `.repo-map/` directory exists, Claude uses it automatically for context (Query mode).

## How It Works

### Tiered Architecture

| Tier | Location | Size | Content | Loaded |
|------|----------|------|---------|--------|
| **T1** | `index.md` | 10-25K tokens | Directory tree, file descriptions, entry points, tech stack | Always |
| **T2** | `details/*.md` | 200-800 tokens each | Module summaries, key interfaces, relationships | On-demand |
| **T3** | `deep/*.md` | 500-2K tokens each | Function signatures, logic flow, patterns | On-demand |
| **T4** | *(source)* | 2K+ tokens each | Raw file read — always produces T2/T3 as side effect | Last resort |

Every raw source read permanently enriches the map. No token is spent without building lasting context.

### Operating Modes

| Mode | Trigger | What Happens |
|------|---------|--------------|
| **Cold Start** | First run (no `.repo-map/`) | Scan repo, build initial index + frontier, begin exploration |
| **Warm Start** | `/explore` (map exists) | Load existing map, detect drift, continue exploring from frontier |
| **Update** | `/update` | Detect changes via `git diff`, remap only modified files |
| **Query** | Automatic (map exists) | Answer questions using tiered map — load T2/T3 as needed |

### Pipeline

Eight standalone scripts chained via JSON:

```
scan.py → frontier.py → init.py     (Cold Start)
drift.py                             (Update / staleness check)
budget.py                            (Token tracking)
compress.py                          (Index compression)
enrich.py                            (T3 deep-dive generation)
hash.py                              (File hashing utilities)
```

Each script reads JSON from stdin or files and writes JSON to stdout. They're composable via pipes and designed to be invoked by the SKILL.md orchestration logic.

### Generated Artifacts

When repo-map runs, it creates a `.repo-map/` directory in the repository root:

```
.repo-map/
├── index.md          # T1: Compact structural index (~10-25K tokens)
├── frontier.md       # Exploration queue, prioritized by centrality
├── meta.json         # Session metadata, coverage stats, config
├── queries.json      # Query history for hot-path preservation
├── details/          # T2: Per-file summaries (200-800 tokens each)
│   ├── src-auth-login-py.md
│   ├── src-api-routes-py.md
│   └── ...
└── deep/             # T3: Deep-dive analysis (500-2000 tokens each)
    ├── src-auth-login-py.md
    └── ...
```

Add `.repo-map/` to your project's `.gitignore` — it's generated output, not source.

## Benchmark Results

Measured with `bench.py` across 5 synthetic repo sizes, comparing naive (reload all source every session) vs plugin (tiered persistent map). Default: 5 queries per session.

| Repo Size | Files | Naive/Session | Plugin Warm | Break-even | 5-Session Savings |
|-----------|-------|---------------|-------------|------------|-------------------|
| Tiny | 20 | 6.8K | 9.2K | — | -35% |
| Small | 65 | 24.7K | 10.0K | Session 1 | 59% |
| Medium | 200 | 110K | 9.9K | Session 1 | 91% |
| Large | 650 | 640K | 10.4K | Session 1 | 98% |
| XL | 2,000 | 2.4M | 10.5K | Session 1 | 99.6% |

The plugin's warm-session cost stays nearly flat (~10K tokens) regardless of repo size, while naive cost scales linearly with file count.

### Multi-Session Projection

```
         5 sessions (seed=42, 5 queries/session)

tiny    naive  |█                                       | 34K
        plugin |█                                       | 46K

small   naive  |█                                       | 124K
        plugin |█                                       | 50K

medium  naive  |██                                      | 550K
        plugin |█                                       | 50K

large   naive  |██████████                              | 3.2M
        plugin |█                                       | 53K

xl      naive  |████████████████████████████████████████| 11.9M
        plugin |█                                       | 53K
```

### Key Takeaways

- For repos with **65+ files**, the plugin pays for itself on the **first session**
- At **200 files**, warm sessions use only **9% of naive cost**
- At **2,000 files**, the plugin delivers **99.6% token savings** — turning a 2.4M token reload into a 10.5K token index load
- Tiny repos (< 30 files) don't benefit — the plugin's overhead exceeds the source size

### Reproduce

```bash
python3 repo-map/scripts/bench.py --seed 42
python3 repo-map/scripts/bench.py --tiers medium,large --output report.md
```

Output is deterministic with the same seed.

## File Structure

```
repo-map/
├── SKILL.md                    # Claude Code skill definition (~1,023 tokens)
├── scripts/
│   ├── scan.py                 # Repo structure scanner
│   ├── hash.py                 # Content hashing + change detection
│   ├── frontier.py             # Exploration priority scoring
│   ├── budget.py               # Token budget estimation
│   ├── init.py                 # Bootstrap .repo-map/ directory
│   ├── drift.py                # Git-based change detection
│   ├── compress.py             # Index compression strategies
│   ├── enrich.py               # T3 deep-dive enrichment
│   └── bench.py                # Benchmark suite
└── templates/
    ├── index-template.md       # T1 index template
    ├── frontier-template.md    # Frontier queue template
    └── meta-template.json      # Session metadata template
```

## Architecture

For detailed technical documentation, see:

- [Deployment Specification](repo-map-deployment-spec.md) — Operational model, budget partitioning, tier definitions
- [Agentic Search Architecture](agentic-search-architecture.md) — Algorithmic foundations, search strategies

## License

[MIT](LICENSE)
