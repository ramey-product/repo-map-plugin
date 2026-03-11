# Agentic Search: Composite Algorithm Architecture

## Problem Statement

Design a search system where an autonomous AI agent can explore an unknown dataset (e.g., an enterprise repository), incrementally build an index of what it discovers, and use that index to perform efficient lookups — all while interleaving exploration and retrieval in real time.

The four chosen algorithms: **BFS/DFS + Ingress Memoization**, **Radix Trie**, **HNSW**, and **Bloom Filters**. This document analyzes how they compose into a unified architecture.

---

## 1. Layered Architecture Overview

The system decomposes into four cooperating layers, each owning a distinct concern. The layers are not a strict call stack — they interact laterally through a shared **Agent State Bus** that tracks exploration progress, query context, and index health.

```
┌──────────────────────────────────────────────────────────────┐
│                      AGENT QUERY API                         │
│         (receives query, returns results + confidence)       │
└──────────────┬───────────────────────────────┬───────────────┘
               │                               │
               ▼                               ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│     QUERY ROUTER         │   │     EXPLORATION SCHEDULER    │
│  (decides which index    │   │  (decides what to explore    │
│   to consult first)      │   │   next based on query gaps)  │
└──────┬──────┬──────┬─────┘   └──────────────┬───────────────┘
       │      │      │                         │
       ▼      ▼      ▼                         ▼
┌──────┐ ┌──────┐ ┌──────┐          ┌─────────────────────┐
│RADIX │ │ HNSW │ │BLOOM │          │  GRAPH EXPLORER     │
│TRIE  │ │INDEX │ │FILTER│          │  (BFS/DFS + Ingress │
│      │ │      │ │      │          │   Memoization)      │
└──┬───┘ └──┬───┘ └──┬───┘          └──────────┬──────────┘
   │        │        │                          │
   └────────┴────────┴──────────────────────────┘
                         │
              ┌──────────┴──────────┐
              │   AGENT STATE BUS   │
              │  (shared metadata,  │
              │   exploration map,  │
              │   index statistics) │
              └─────────────────────┘
```

### Layer Responsibilities

**Layer 1 — Graph Explorer (BFS/DFS + Ingress Memoization)**
The exploration engine. Treats the dataset as an implicit graph where nodes are discoverable entities (files, database rows, API endpoints, document sections) and edges are relationships between them (imports, references, hyperlinks, foreign keys). The agent doesn't know the full graph upfront — it reveals structure incrementally through traversal.

Ingress memoization governs *what gets remembered* during traversal. The four policies map to agent behaviors:

| Policy | Stores | Space | Use When |
|--------|--------|-------|----------|
| **MF (Memoization-Free)** | Nothing — recomputes on demand | O(1) | The dataset is small enough to re-traverse cheaply, or the agent only needs latest state |
| **MP (Memoization-Path)** | Critical path segments | O(r·\|V_affected\|) | The agent cares about *how* it reached a node (dependency chains, import paths) |
| **MV (Memoization-Vertex)** | Full vertex state at convergence | O(\|V\|) | The agent needs to detect changes to previously-visited nodes (file modification detection) |
| **ME (Memoization-Edge)** | All messages/edges | O(r·\|E\|) | The agent needs complete history (audit trails, full dependency graphs) |

For an agentic context, the system should **start with MP** (path memoization) and **escalate to MV or ME** as the agent's understanding of the dataset deepens. This mirrors how a human would explore — first noting landmarks and paths, then building a detailed mental model.

**Layer 2 — Radix Trie (Structural Index)**
The "exact match" fast path. Indexes the *structural identifiers* discovered during exploration: file paths, class names, function signatures, table names, URL routes. A compressed radix trie collapses shared prefixes, so `src/components/Button.tsx` and `src/components/Modal.tsx` share the `src/components/` prefix node.

Key properties for the agentic context:
- **O(m) lookup** where m is the identifier length — independent of dataset size
- **Prefix-based exploration**: querying `src/components/` returns all known children without re-traversing
- **Incremental construction**: new paths insert in O(m) as the explorer discovers them
- **Namespace awareness**: natural representation of hierarchical structures (directories, packages, schemas)

**Layer 3 — HNSW (Semantic Index)**
The "fuzzy match" engine. When the agent needs to find content *related to* a concept rather than matching an exact identifier, it queries the HNSW index over vector embeddings of discovered content.

Architecture decisions:
- **Incremental insertion**: as the explorer discovers new content, embeddings are computed and inserted into the HNSW graph. Insertion is O(log n) per element.
- **Layer separation**: HNSW's hierarchical layers naturally map to exploration granularity — top layers connect coarse concepts (entire modules, document sections), bottom layers connect fine-grained content (individual functions, paragraphs).
- **Staleness tolerance**: unlike the Radix Trie (which must be exact), HNSW can tolerate slightly stale embeddings. The agent can defer re-embedding changed content until a natural pause in exploration.

**Layer 4 — Bloom Filter (Exploration Guard)**
The membership oracle. Before the explorer visits a node, it checks the Bloom filter: "Have I already explored this?" This prevents redundant traversal and turns O(V+E) exploration into a one-pass operation even when the graph has cycles or when multiple query paths converge on the same nodes.

Configuration: with k=7 hash functions and 10 bits per element, the false positive rate is ~0.82%. For an enterprise repo with 100K entities, the Bloom filter consumes ~122 KB — negligible compared to the other indices. A false positive means the agent *skips* an unexplored node, which is correctable (the node will be discovered through an alternate path or on a subsequent query-driven exploration).

---

## 2. Query Router: The Orchestration Logic

The Query Router is the critical architectural component. It receives a query from the agent and determines which combination of indices to consult, in what order, and whether to trigger additional exploration.

### Routing Decision Tree

```
QUERY ARRIVES
    │
    ├─ Is it a structural/exact query?
    │   (file path, class name, identifier)
    │       │
    │       ├─ YES → Radix Trie lookup [O(m)]
    │       │       ├─ HIT → Return result
    │       │       └─ MISS → Check Bloom Filter
    │       │               ├─ POSSIBLY SEEN → Consult Ingress memo
    │       │               │                  for path to nearest ancestor
    │       │               └─ DEFINITELY NOT SEEN → Trigger targeted
    │       │                                        exploration from
    │       │                                        nearest known prefix
    │       │
    │       └─ NO (semantic/fuzzy query) ↓
    │
    ├─ Is it a semantic/similarity query?
    │   (concept, description, natural language)
    │       │
    │       ├─ YES → HNSW search [O(log n)]
    │       │       ├─ HIGH CONFIDENCE (distance < threshold)
    │       │       │       → Return results
    │       │       └─ LOW CONFIDENCE (distance > threshold OR
    │       │           coverage ratio low)
    │       │               → Return partial results +
    │       │                 trigger exploration in
    │       │                 under-indexed regions
    │       │
    │       └─ NO (hybrid query) ↓
    │
    └─ Hybrid query (structural + semantic)
        → Parallel: Radix Trie prefix scan + HNSW search
        → Merge results by weighted scoring
        → If coverage gap detected → schedule exploration
```

### Coverage-Aware Routing

The router maintains a **coverage estimate** — what fraction of the dataset has been indexed. This is approximated by tracking:

- `|V_explored|` / `|V_estimated|` — nodes explored vs. estimated total (from graph density heuristics)
- HNSW index size vs. trie leaf count — divergence suggests content was found but not embedded, or vice versa
- Bloom filter saturation — as the filter fills, the false positive rate rises, signaling dense exploration

When coverage is low (< 30%), the router biases toward exploration-then-query. When coverage is high (> 70%), it biases toward index-first with exploration as fallback.

---

## 3. Data Flow: Lifecycle of a Query

### Scenario: Agent searches for "authentication middleware" in an unfamiliar codebase

**Step 1 — Query Classification**
The router classifies "authentication middleware" as a semantic query (no exact path or identifier).

**Step 2 — HNSW Probe**
Query embedding is computed. HNSW returns top-k results. If the index is sparse (early exploration), results may be poor quality — distance scores are high, confidence is low.

**Step 3 — Radix Trie Prefix Scan**
Router also performs a prefix scan for common patterns: `*/auth/*`, `*/middleware/*`, `*/security/*`. The trie returns any structural matches discovered during prior exploration.

**Step 4 — Gap Detection**
If neither index produces high-confidence results, the router identifies *where* the gap is. Using the Ingress memoization state (MP policy), it finds the nearest explored region to the likely location. For a codebase, this might be: "I've explored `src/` but not `src/middleware/`."

**Step 5 — Targeted Exploration**
The explorer runs BFS from the nearest known ancestor (`src/`) with a depth limit and a filter biased toward `auth`/`middleware` patterns. The Bloom filter prevents re-visiting already-explored subtrees.

**Step 6 — Index Update**
As new nodes are discovered:
- Paths insert into the Radix Trie → O(m) per path
- Content is embedded and inserted into HNSW → O(log n) per node
- Node IDs are added to the Bloom filter → O(k) per node
- Ingress memoization records the traversal path → O(|ΔG|)

**Step 7 — Re-Query**
The router re-queries HNSW with the now-enriched index. Results should be higher confidence. If still insufficient, the exploration scheduler queues broader traversal for background execution.

**Total cost for this query:**
- Classification: O(1)
- HNSW search: O(log n)
- Trie prefix scan: O(m + |results|)
- Bloom filter checks during exploration: O(k) per node
- BFS exploration of gap: O(|V_gap| + |E_gap|)
- Index insertions: O(|V_new| · log n) for HNSW + O(Σm_i) for trie
- Re-query: O(log n)

**Amortized over many queries:** Each exploration enriches the index permanently. The system converges toward O(log n) per query as coverage increases.

---

## 4. Inter-Layer Communication Protocol

The layers don't communicate through direct function calls — they communicate through **events on the Agent State Bus**. This decoupling is essential for an agentic context where operations are interleaved unpredictably.

### Event Types

```
DISCOVERY_EVENT {
    node_id: string,
    node_type: "file" | "directory" | "function" | "class" | "endpoint" | ...,
    path: string[],           // structural path from root
    content_hash: string,     // for change detection
    parent_id: string,        // for trie insertion
    edges: Edge[],            // discovered relationships
    content: bytes | null     // raw content for embedding (null if too large)
}

QUERY_EVENT {
    query_id: string,
    query_type: "structural" | "semantic" | "hybrid",
    query_text: string,
    query_vector: float[] | null,
    source: "agent" | "router_refinement" | "background_enrichment"
}

INDEX_UPDATE_EVENT {
    index: "trie" | "hnsw" | "bloom" | "memo",
    operation: "insert" | "update" | "delete",
    node_id: string,
    metadata: object
}

COVERAGE_EVENT {
    explored_nodes: int,
    estimated_total: int,
    bloom_saturation: float,
    hnsw_size: int,
    trie_leaves: int,
    coverage_ratio: float
}

EXPLORATION_REQUEST {
    target: string,           // where to start exploring
    strategy: "bfs" | "dfs" | "bidirectional",
    depth_limit: int,
    filter: string | null,    // pattern to prioritize
    priority: "immediate" | "background"
}
```

### Event Flow for Discovery

```
Explorer discovers node
    → emits DISCOVERY_EVENT
        → Bloom filter adds node_id              [O(k)]
        → Radix Trie inserts path                [O(m)]
        → Ingress memoization records traversal   [O(|ΔG|)]
        → HNSW embedding queue receives content   [async, O(log n)]
        → Coverage estimator updates              [O(1)]
```

This event-driven architecture means the embedding step (most expensive per-node) can be **asynchronous** — the agent doesn't block on it. The trie and Bloom filter update synchronously since they're O(m) and O(k) respectively.

---

## 5. Composite Complexity Analysis

### Worst-Case Bounds (Full Exploration + All Queries)

| Operation | Complexity | Dominant Component |
|-----------|-----------|-------------------|
| Full dataset exploration | O(V + E) | BFS/DFS traversal |
| Full index construction | O(V · log V) | HNSW insertions dominate |
| Single structural query | O(m) | Radix Trie |
| Single semantic query | O(log n) | HNSW search |
| Single membership check | O(k) ≈ O(1) | Bloom filter |
| Incremental update (ΔG) | O(\|ΔG\| · log V) | HNSW re-insertion of changed nodes |
| Full session (explore + q queries) | O(V + E + V·log V + q·log V) | Simplifies to O(V·log V + q·log V) when E = O(V) |

### Amortized Per-Query Cost

After exploration converges, the amortized cost per query is:

- **Best case** (exact structural match): **O(m)** — trie lookup, constant in dataset size
- **Typical case** (semantic search over indexed data): **O(log n)** — HNSW
- **Worst case** (query triggers new exploration): **O(|V_gap| + |E_gap| + |V_new|·log n)** — but this enriches the index, so the same worst case cannot repeat for the same region

The key insight: **the system has a natural convergence property**. Each query that triggers exploration makes future queries in the same region cheaper. After sufficient queries, the system operates at O(log n) per query for semantic lookups and O(m) for structural lookups.

### Comparison to Naive Approaches

| Approach | Per-Query Cost | Total Cost (q queries) |
|----------|---------------|----------------------|
| Linear scan (no index) | O(n) | O(q · n) |
| Build full index first, then query | O(n log n) + O(q · log n) | Front-loaded |
| **This architecture (adaptive)** | O(log n) amortized | O(V log V + q · log n) with smooth ramp-up |

The adaptive architecture avoids the cold-start penalty of "build everything first" while converging to the same asymptotic query performance.

---

## 6. Memoization Policy Selection Strategy

The Ingress memoization policy should not be static — it should adapt based on the agent's task:

### Policy Selection Matrix

| Agent Task | Recommended Policy | Reasoning |
|-----------|-------------------|-----------|
| Quick codebase scan | MF (Memoization-Free) | Disposable exploration; agent won't revisit |
| Bug investigation | MP (Memoization-Path) | Agent needs to trace dependency chains back to root cause |
| Codebase understanding / onboarding | MV (Memoization-Vertex) | Agent needs to remember what each module does |
| Audit / compliance review | ME (Memoization-Edge) | Complete traversal history required |
| Long-running assistant (multi-session) | MV → ME escalation | Start lean, accumulate detail over sessions |

The system should expose a `set_memo_policy()` API that the agent can call when its task changes, with automatic migration of existing memoization state where possible (MF→MP→MV→ME is additive; reverse direction discards data).

---

## 7. Failure Modes and Mitigations

### Bloom Filter False Positives
**Risk:** Agent skips an unexplored node, believing it was already visited.
**Mitigation:** Implement a "coverage audit" that periodically samples the Bloom filter's negative space. If the audit finds nodes that should have been discovered but weren't, it marks regions for re-exploration. Additionally, the Radix Trie serves as ground truth — if a trie lookup fails for a path the Bloom filter claims was visited, that's a detected false positive.

### HNSW Staleness
**Risk:** Content changes after initial embedding; HNSW returns outdated results.
**Mitigation:** The Ingress MV policy tracks vertex state at convergence. When a node's content hash changes (detected via `DISCOVERY_EVENT`), the system queues re-embedding. The HNSW index supports update-in-place for existing node IDs.

### Exploration Explosion
**Risk:** The graph is denser than expected; BFS fans out to millions of nodes.
**Mitigation:** Depth limits + the Bloom filter naturally cap exploration. The exploration scheduler should implement a **budget** — maximum nodes per exploration round — and return partial results. The agent can request deeper exploration in subsequent queries if needed.

### Trie Memory Pressure
**Risk:** Enterprise repo with millions of file paths exhausts memory.
**Mitigation:** Radix compression already reduces memory by collapsing shared prefixes. For extreme scale, implement a **tiered trie** — hot paths (frequently queried) in memory, cold paths spilled to disk with an LRU eviction policy. The Bloom filter can serve as a fast pre-filter: check membership before loading a cold trie segment.

---

## 8. Open Design Questions

1. **Embedding Strategy**: Should the agent embed content eagerly (during exploration) or lazily (on first semantic query against that region)? Eager gives better HNSW quality but costs more upfront. Lazy risks cold queries.

2. **Cross-Index Consistency**: When a node is deleted from the source dataset, the trie and HNSW need coordinated removal, but the Bloom filter cannot remove elements (standard Bloom filters don't support deletion). Should we use a Counting Bloom Filter (which supports deletion at 3-4x memory cost)?

3. **Multi-Agent Coordination**: If multiple agents explore the same dataset concurrently, how do their indices merge? The Bloom filter and trie can merge via union. HNSW merge is non-trivial — potentially solved by using separate HNSW graphs per agent and a federated search layer.

4. **Persistence**: Should the index persist across agent sessions? If yes, what's the serialization format? The trie serializes naturally. HNSW has known serialization formats (hnswlib). Ingress memoization state may need a custom format.

---

## References

- [Ingress: Incrementalize Graph Algorithms (GraphScope)](https://graphscope.io/docs/analytical_engine/ingress)
- [Ingress VLDB Paper (Gong et al.)](http://vldb.org/pvldb/vol14/p1613-gong.pdf)
- [Allan-Poe: All-in-One Graph-Based Indexing for Hybrid Search](https://arxiv.org/html/2511.00855)
- [Hybrid Multimodal Graph Index (HMGI)](https://arxiv.org/pdf/2510.10123)
- [TigerVector: Vector Search in Graph Databases](https://arxiv.org/html/2501.11216v3)
- [TigerGraph: Hybrid Graph Architecture for Agentic AI](https://www.tigergraph.com/blog/why-hybrid-graph-architecture-strengthens-agentic-ai/)
- [HNSW: Efficient ANN Search (Pinecone)](https://www.pinecone.io/learn/series/faiss/hnsw/)
- [BloomARROW: Reachability in Large Graphs using Bloom Filters](https://arkasaha.github.io/papers/BloomGraphReach.pdf)

---

## ADDENDUM: Token Economics Reframing

> **Added post-review.** The sections below reframe the entire architecture around the primary constraint for agentic LLM deployment: **token consumption and context window management**. Speed remains a secondary optimization lever. These sections should be read as amendments to the corresponding original sections above.

---

### A1. Primary Constraint: Token Economics

The core problem with deploying agentic LLMs into large repositories is not speed — it is **context window size** and **usage rate limitations**. Every file read, every search result returned, and every re-read of previously seen content consumes tokens from a finite budget. The architecture's value proposition is therefore:

> **Build a compressed, incrementally-enriched reference structure that prevents repeated ingestion of source material, minimizing total token expenditure across an agent session.**

**The token cost model:**

| Operation | Token Cost | Frequency Without Index | Frequency With Index |
|-----------|-----------|------------------------|---------------------|
| Full file read | ~1 token/4 chars (thousands per file) | Every query touching that file | Once (at discovery) |
| Re-read for context | Same as full read | Every time agent loses context | Zero (index serves compressed summary) |
| Search result ingestion | Proportional to result size | Linear in corpus size | Bounded by index granularity |
| Index lookup | Near-zero (structured metadata) | N/A | Every query |

**Key insight:** The memoization layer IS the agent's compressed memory. Without it, an agent working in a 50K-file repository would need to re-read files on every context window reset — burning tokens on content it has already seen. The index converts O(n) repeated reads into O(1) lookups against pre-computed summaries.

**Three token-saving mechanisms:**

1. **Tiered Summarization at Discovery Time** — When the graph explorer first encounters a file, it doesn't just record the path. It generates summaries at multiple granularities: file-level (1-2 sentences), function/class-level (key signatures + purpose), and detail-level (full content hash + byte range pointers). The agent retrieves the minimum tier needed.

2. **Staleness Detection via Content Hashing** — Each memoized node stores a content hash (xxHash64, ~8 bytes). Before re-reading a file, the agent checks the hash. If unchanged, the cached summary is used at zero additional token cost. Re-reads only happen when files actually change.

3. **Compressed Context Reconstruction** — Instead of re-reading source files to answer questions, the agent reconstructs context from index metadata: trie paths give structural location, HNSW neighbors give semantic context, and memoized summaries give content. This "synthetic context" costs 10-50x fewer tokens than reading the source files.

---

### A2. Token-Aware Query Router (Amendment to Section 3)

The Query Router gains a **token budget** dimension. Each query is now classified along two axes:

```
                    ┌─────────────────────────────────────┐
                    │         TOKEN BUDGET ROUTER          │
                    │                                      │
                    │  Input: query + remaining_budget      │
                    │                                      │
                    │  if budget > 80%:                     │
                    │    → full retrieval (source + index)  │
                    │  if budget 30-80%:                    │
                    │    → index-only (summaries + paths)   │
                    │  if budget < 30%:                     │
                    │    → compressed mode (trie paths +    │
                    │      Bloom membership only)           │
                    │  if budget < 10%:                     │
                    │    → emergency: return cached answer  │
                    │      or "insufficient context" signal │
                    └─────────────────────────────────────┘
```

**Budget tracking fields added to Agent State Bus events:**

```
QUERY_EVENT (amended):
  - query_id: string
  - query_text: string
  - query_type: "structural" | "semantic" | "hybrid"
  - token_budget_remaining: int        # NEW
  - token_budget_allocated: int        # NEW — max tokens for this query
  - preferred_tier: "summary" | "detail" | "source"  # NEW
  - results: SearchResult[]
  - tokens_consumed: int               # NEW — actual usage
```

**Routing priority shift:** When token budget is constrained (< 30%), the router MUST prefer:
1. Bloom filter membership check (near-zero tokens) → confirms entity exists
2. Radix trie path lookup (minimal tokens) → gives structural location
3. HNSW neighbor list without content (low tokens) → gives semantic neighborhood
4. Memoized summary at coarsest tier (moderate tokens) → gives compressed content

Source file reads become the **last resort**, not the default.

---

### A3. Memoization Policy Through Token Cost Lens (Amendment to Section 7)

The original memoization policies (MF, MP, MV, ME) are reframed by their **token savings profile:**

| Policy | Token Cost to Build | Token Savings Per Query | Break-Even Point | Best For |
|--------|-------------------|----------------------|-----------------|---------|
| **MF** (Memo Freq) | ~0 (counts only) | Low — no content cached | 1 query | Quick scans, existence checks |
| **MP** (Memo Partial) | Moderate — stores affected subsets | Medium — avoids partial re-reads | 3-5 queries | Targeted investigation (bug hunts) |
| **MV** (Memo Vertex) | High — full vertex summaries | High — eliminates file re-reads | 8-12 queries | Onboarding, broad exploration |
| **ME** (Memo Edge) | Very high — relationship graph | Very high — eliminates traversal re-computation | 15-20 queries | Audit, dependency analysis, multi-session |

**Escalation trigger reframed:** Escalate memoization policy when `cumulative_tokens_saved > 2x * build_cost`. This means:
- Start with MP for first 50 files explored
- Escalate to MV once the agent has queried the same files 3+ times
- Escalate to ME when cross-file relationship queries exceed 30% of total queries

**Multi-session persistence:** ME state is the most valuable to persist because relationship data is the most expensive to re-derive. A serialized ME cache for a 50K-file repo might cost ~5-10 MB of storage but saves millions of tokens across sessions.

---

### A4. Progressive Detail Retrieval Pattern (New Section)

A new retrieval pattern layered on top of the existing architecture:

```
Query: "How does authentication work?"

TIER 1 — Structural Sketch (cost: ~50 tokens)
  └─ Trie lookup: auth* → [src/auth/, src/middleware/auth.ts, src/utils/jwt.ts]
  └─ Returns: file paths + directory structure only

TIER 2 — Compressed Summary (cost: ~200 tokens)
  └─ Memoized summaries for each file:
     "auth.ts — Express middleware, validates JWT, checks role permissions"
     "jwt.ts — Token creation/verification, RS256, 1h expiry"
  └─ Returns: 1-2 sentence summaries per file

TIER 3 — Semantic Neighbors (cost: ~500 tokens)
  └─ HNSW query: "authentication" → top-5 semantically related chunks
  └─ Returns: relevant code snippets (function signatures, key logic)

TIER 4 — Full Source Read (cost: ~2000+ tokens)
  └─ Only triggered if Tiers 1-3 are insufficient
  └─ Reads actual file content from disk
  └─ Immediately generates Tier 2 summary for future queries
```

**The agent always starts at Tier 1 and only descends when the current tier is insufficient.** Each tier costs roughly 4-10x more than the previous. For well-indexed repositories, 60-80% of queries can be answered at Tier 2 without ever reading source files.

**Auto-summarization rule:** Any Tier 4 read MUST generate a Tier 2 summary before returning results. This ensures the next query for the same content costs 10x less.

---

### A5. Amended Complexity Analysis — Token Cost Model (Amendment to Section 6)

The original complexity table measured time. The token cost model adds a parallel dimension:

| Operation | Time Complexity | Token Cost (Without Index) | Token Cost (With Index) | Savings Factor |
|-----------|----------------|---------------------------|------------------------|---------------|
| First query (cold) | O(\|V\|+\|E\|) | O(n · avg_file_size) | O(n · avg_file_size) | 1x (must explore) |
| Repeat query (warm) | O(log n) | O(n · avg_file_size) | O(summary_size) | 10-50x |
| Structural lookup | O(m) | O(file_tree_size) | O(path_length) | 100x+ |
| Semantic search | O(log n) | O(corpus_size) | O(k · chunk_size) | 50-200x |
| Existence check | O(k) | O(grep over corpus) | O(1) bloom bits | 1000x+ |
| Cross-file relationship | O(\|E\|) | O(n² · avg_file_size) | O(\|neighbors\| · summary_size) | 100-500x |

**Amortized token cost per query (steady-state):**
- Best case: ~50 tokens (Bloom + trie path, Tier 1)
- Typical case: ~200 tokens (memoized summary, Tier 2)
- Worst case: ~2000+ tokens (source read + summary generation, Tier 4)
- Without index: ~5000-50,000 tokens per query (file reads + context reconstruction)

**Convergence property (reframed):** As coverage approaches 100%, the token cost per query converges toward Tier 2 costs (~200 tokens) because every file has been summarized. The index effectively converts a O(corpus_size) token cost into a O(index_size) token cost, where index_size is typically 1-5% of corpus_size.

---

### A6. Open Design Questions (Amended)

The following questions from Section 9 gain additional dimensions under the token economics frame:

5. **Summary Granularity Tuning**: How many tiers of summarization should exist? The 4-tier model above (path → summary → snippet → source) may need domain-specific tuning. Code repositories benefit from function-level summaries; documentation repos may benefit from section-level.

6. **Token Budget Allocation Strategy**: When an agent session has a fixed token budget (e.g., 200K tokens for a Claude session), how should the budget be allocated between exploration (building the index) and exploitation (answering queries)? A possible heuristic: 40% exploration / 60% exploitation, shifting to 20/80 as coverage exceeds 70%.

7. **Cross-Session Index Warming**: If the ME memoization state is persisted, how does the agent validate index freshness at session start without burning excessive tokens? Content hashes on a sampled subset (~5% of files) could detect drift with bounded token cost.

8. **Context Window Packing**: When the agent needs to present index-derived context to the LLM, how should multiple summaries be packed into a single context window for maximum information density? Possible approaches: relevance-sorted concatenation, hierarchical nesting (directory summary → file summaries → relevant snippets), or structured JSON with pointer references.
