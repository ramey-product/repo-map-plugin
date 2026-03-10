# Changelog

All notable changes to this project will be documented in this file.

## [1.0.0] - 2026-03-09

Initial public release. Full-featured repository mapping skill for Claude Code.

### Core Pipeline
- **scan.py** — Recursive directory scanner with entry-point detection and tech-stack inference
- **hash.py** — Content hashing for change detection and cache invalidation
- **frontier.py** — Priority queue that ranks files by centrality (import graph + entry points)
- **init.py** — Cold-start initializer that scaffolds `.repo-map/` with index, frontier, and metadata
- **budget.py** — Token budget tracker with green/yellow/red/critical zone thresholds

### Drift Detection
- **drift.py** — Git-based change detection comparing current state against last mapped commit
- Generates ADD/REMOVE/RENAME/REMAP actions for incremental updates
- Recommends full re-explore when > 50 files changed

### Index Compression
- **compress.py** — Multi-strategy index compressor (detail stripping, low-value pruning, structure folding, aggressive trimming)
- Query history preservation — hot paths protected from compression
- Automatic trigger when index exceeds 20K token estimate

### Deep-Dive Enrichment
- **enrich.py** — T3 deep-dive generator for high-centrality files
- Function/class index, logic flow analysis, pattern detection
- Batch mode for proactive enrichment during green-budget sessions
- Opportunistic T3 generation on T4 raw reads

### Skill Orchestration
- **SKILL.md** — Complete skill definition with 4 operating modes (Cold Start, Warm Start, Update, Query)
- Token-optimized instruction set (1023 tokens, 47% reduction from initial draft)
- Handoff protocol for multi-session continuity

### Quality & Testing
- **bench.py** — Deterministic benchmark suite covering tiny through xl repositories
- All 8 pipeline scripts validated across 5 repo size tiers
- 59-99.6% token savings demonstrated vs. naive full-read approach
