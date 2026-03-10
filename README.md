# Repo-Map Plugin

A Claude Code skill that builds persistent, token-efficient repository maps across sessions. Instead of re-reading source files every conversation, the plugin maintains a tiered map that grows incrementally — giving the agent structural understanding of your entire codebase at a fraction of the token cost.

## The Problem

Every time an AI agent starts a new session on a large codebase, it has to re-read source files to understand the project. For a 650-file repo, that's ~640K tokens *per session* — most of the context window consumed before the agent can even start working.

## The Solution

Repo-Map builds a layered, persistent index that lives in your working directory (`.repo-map/`). The agent loads a compact structural map (~10K tokens) instead of raw source, pulling deeper detail only when needed. The map persists across sessions — no re-reading, no wasted tokens.

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
| **Cold Start** | First run / `repo-map explore` | Scan repo, build initial index + frontier, begin exploration |
| **Warm Start** | `repo-map explore` (map exists) | Load existing map, detect drift, continue exploring from frontier |
| **Update** | `repo-map update` | Detect changes via `git diff`, remap only modified files |
| **Query** | Automatic (map exists) | Answer questions using tiered map — load T2/T3 as needed |

### Pipeline

Eight standalone scripts chained via JSON:

```
scan.py → hash.py → frontier.py → budget.py → init.py → drift.py → compress.py → enrich.py
```

- **scan.py** — Walk the repo, classify files, detect tech stack
- **hash.py** — Content hashing for change detection
- **frontier.py** — Priority queue scoring (centrality, relevance, freshness, coverage gaps)
- **budget.py** — Token budget tracking with zone-based throttling
- **init.py** — Bootstrap `.repo-map/` with index, frontier, and metadata
- **drift.py** — Detect repo changes since last session via git or hash comparison
- **compress.py** — Hierarchical collapse, depth limiting, sibling merging when index exceeds 20K tokens
- **enrich.py** — T3 deep-dive generation for high-value files with cross-reference analysis

All scripts are Python 3.9+ stdlib-only — no pip dependencies.

## Benchmark Results

Measured with `bench.py` across 5 synthetic repo sizes, comparing naive (reload all source every session) vs plugin (tiered persistent map). Default: 5 queries per session.

### Summary

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

## Installation

Copy the `repo-map/` directory into your project's `.claude/skills/` folder (or wherever your Claude Code skills are configured). The SKILL.md file registers the `explore` and `update` commands automatically.

## Requirements

- Python 3.9+
- Git (for drift detection)
- No external dependencies
