#!/usr/bin/env python3
"""T3 deep-dive manager and enrichment orchestrator.

Identifies files that need T3 deep-dive summaries, prioritizes them,
and manages file state. Does NOT read source files or generate summaries —
the LLM does the actual summarization based on this script's output.

Usage:
    python enrich.py --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json
    python enrich.py --file src/auth/jwt.ts --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json
    python enrich.py --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json --batch 5
    python enrich.py --details-dir .repo-map/details/ --deep-dir .repo-map/deep/ --meta .repo-map/meta.json --report

Output (JSON to stdout):
    {
        "mode": "batch|single|report",
        "files_enriched": [...],
        "files_skipped": [...],
        "total_enriched": 0,
        "total_skipped": 0,
        "deep_files_total": 0,
        "deep_total_tokens": 0
    }
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Token estimation ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count: ~4 chars/token for markdown/prose."""
    return max(1, len(text) // 4)


# ── Slug convention (matches compress.py, drift.py, init.py) ─────────────────

def path_to_slug(path: str) -> str:
    """Convert a file/dir path to a slug.

    src/utils/helper.py → src-utils-helper-py
    src/utils/ → src-utils
    """
    slug = path.replace("/", "-").replace("\\", "-").replace(".", "-")
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip("-").lower()
    return slug


def slug_to_path_candidates(slug: str) -> list[str]:
    """Reverse a slug back to likely source path candidates.

    src-utils-helper-py → src/utils/helper.py (best guess)
    This is heuristic — slugs are lossy.
    """
    # Replace hyphens with / as a starting point, but extensions need dots
    # We can't perfectly invert, so return the slug for matching purposes
    return [slug]


# ── Scanning ─────────────────────────────────────────────────────────────────

def scan_detail_files(details_dir: str) -> list[dict]:
    """List all T2 summary files with metadata.

    Returns [{path, slug, size, estimated_tokens, source_path_hint}, ...]
    """
    details_path = Path(details_dir)
    if not details_path.is_dir():
        return []

    results = []
    for f in sorted(details_path.iterdir()):
        if not f.is_file() or not f.name.endswith(".md"):
            continue
        slug = f.stem  # filename without .md
        content = f.read_text(encoding="utf-8", errors="replace")
        size = len(content)

        # Try to extract source path from the detail file header
        source_path = _extract_source_path(content, slug)

        results.append({
            "path": str(f),
            "slug": slug,
            "size": size,
            "estimated_tokens": estimate_tokens(content),
            "source_path_hint": source_path,
        })
    return results


def scan_deep_files(deep_dir: str) -> list[dict]:
    """List all T3 deep-dive files with metadata.

    Returns [{path, slug, size, estimated_tokens}, ...]
    """
    deep_path = Path(deep_dir)
    if not deep_path.is_dir():
        return []

    results = []
    for f in sorted(deep_path.iterdir()):
        if not f.is_file() or not f.name.endswith(".md"):
            continue
        slug = f.stem
        content = f.read_text(encoding="utf-8", errors="replace")
        results.append({
            "path": str(f),
            "slug": slug,
            "size": len(content),
            "estimated_tokens": estimate_tokens(content),
        })
    return results


def _extract_source_path(detail_content: str, slug: str) -> str:
    """Extract the source file path from a T2 detail file.

    Looks for '# path/to/file' header pattern or falls back to slug.
    """
    for line in detail_content.splitlines()[:5]:
        line = line.strip()
        if line.startswith("# ") and not line.startswith("# Summary"):
            candidate = line[2:].strip()
            # Looks like a path if it has / or a file extension
            if "/" in candidate or "." in candidate:
                return candidate
    return slug


# ── Cross-reference extraction ───────────────────────────────────────────────

def extract_cross_references(detail_content: str) -> list[str]:
    """Parse a T2 summary to find referenced file paths.

    Look for patterns like imports, relative paths, or explicit references.
    """
    refs: list[str] = []
    # Match patterns: imports from X, imported by Y, relative paths
    path_pattern = re.compile(
        r'(?:imports?\s+(?:from\s+)?|imported\s+by\s+|calls?\s+|used\s+by\s+)'
        r'[`\'"]*([a-zA-Z0-9_./-]+\.[a-zA-Z]+)[`\'"]*',
        re.IGNORECASE,
    )
    # Also match backtick-quoted paths that look like files
    backtick_path = re.compile(r'`([a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})`')
    # Match relative path references like ./foo.ts or ../bar/baz.py
    rel_path = re.compile(r'(?:\.\.?/[a-zA-Z0-9_./-]+\.[a-zA-Z]{1,5})')

    for line in detail_content.splitlines():
        for m in path_pattern.finditer(line):
            refs.append(m.group(1))
        for m in backtick_path.finditer(line):
            val = m.group(1)
            if "/" in val:  # only paths, not function names
                refs.append(val)
        for m in rel_path.finditer(line):
            refs.append(m.group(0))

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


# ── Prioritization ───────────────────────────────────────────────────────────

def prioritize_by_query_history(
    candidates: list[dict], query_history_path: str
) -> list[dict]:
    """Boost candidates that appear in query history paths_accessed."""
    try:
        with open(query_history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return candidates

    # Count how many times each path appears in queries
    path_counts: dict[str, int] = {}
    for entry in data.get("queries", []):
        for path in entry.get("paths_accessed", []):
            path_counts[path] = path_counts.get(path, 0) + 1

    for c in candidates:
        source = c.get("source_path", "")
        count = path_counts.get(source, 0)
        # Also try slug-based matching
        if count == 0:
            slug = c.get("slug", "")
            for qpath, qcount in path_counts.items():
                if path_to_slug(qpath) == slug:
                    count = qcount
                    break
        c["query_frequency"] = count
        # Query frequency is 40% of the score
        c["priority_score"] = c.get("priority_score", 0) + count * 40

    return candidates


def find_enrichment_candidates(
    details: list[dict],
    deep: list[dict],
    query_history_path: str | None,
    details_dir: str,
    meta: dict | None = None,
) -> list[dict]:
    """Cross-reference T2 vs T3, prioritize by query frequency + centrality.

    Returns [{source_path, detail_path, slug, priority_score, reason}, ...]
    """
    deep_slugs = {d["slug"] for d in deep}
    candidates: list[dict] = []

    # Build cross-reference counts: how many T2 files reference each slug
    ref_counts: dict[str, int] = {}
    for d in details:
        content = Path(d["path"]).read_text(encoding="utf-8", errors="replace")
        refs = extract_cross_references(content)
        for ref in refs:
            ref_slug = path_to_slug(ref)
            ref_counts[ref_slug] = ref_counts.get(ref_slug, 0) + 1

    for d in details:
        slug = d["slug"]

        # Skip if T3 already exists
        if slug in deep_slugs:
            continue

        source_path = d.get("source_path_hint", slug)
        detail_tokens = d.get("estimated_tokens", 0)

        # Start scoring
        score = 0.0

        # Cross-reference count: 30% weight
        xref_count = ref_counts.get(slug, 0)
        # Normalize: cap at 10 references = max 30 points
        score += min(xref_count, 10) * 3.0

        # File size/complexity: 20% weight
        # Larger T2 summaries suggest more complex source files
        # Normalize: 400 tokens = 20 points (max)
        score += min(detail_tokens / 400.0, 1.0) * 20.0

        # Recency: 10% weight (recent detail files scored higher)
        try:
            mtime = os.path.getmtime(d["path"])
            age_days = (datetime.now(timezone.utc).timestamp() - mtime) / 86400
            # Files modified within last 7 days get full 10 points
            recency_score = max(0, 10.0 - age_days)
            score += recency_score
        except OSError:
            pass

        candidates.append({
            "source_path": source_path,
            "detail_path": d["path"],
            "slug": slug,
            "priority_score": round(score, 1),
            "cross_references": xref_count,
            "detail_tokens": detail_tokens,
        })

    # Apply query history boost (40% weight)
    if query_history_path:
        candidates = prioritize_by_query_history(candidates, query_history_path)

    # Sort by priority score descending
    candidates.sort(key=lambda c: c.get("priority_score", 0), reverse=True)
    return candidates


def estimate_deep_tokens(detail_content: str) -> int:
    """Estimate how many tokens a T3 deep-dive would consume.

    Heuristic: T3 is typically 2-3x the T2 summary length.
    """
    t2_tokens = estimate_tokens(detail_content)
    return int(t2_tokens * 2.5)


# ── Report mode ──────────────────────────────────────────────────────────────

def generate_report(
    details: list[dict],
    deep: list[dict],
    candidates: list[dict],
) -> dict:
    """Generate enrichment status report."""
    t2_count = len(details)
    t3_count = len(deep)
    coverage = round(t3_count / t2_count * 100, 1) if t2_count > 0 else 0.0
    total_deep_tokens = sum(d.get("estimated_tokens", 0) for d in deep)

    return {
        "mode": "report",
        "t2_files": t2_count,
        "t3_files": t3_count,
        "coverage_pct": coverage,
        "top_candidates": candidates[:10],
        "deep_files_total": t3_count,
        "deep_total_tokens": total_deep_tokens,
    }


# ── Single-file mode ────────────────────────────────────────────────────────

def enrich_single(
    file_path: str,
    details_dir: str,
    deep_dir: str,
) -> dict:
    """Process a single file for T3 enrichment.

    Returns result dict with status and instructions.
    """
    slug = path_to_slug(file_path)
    detail_path = Path(details_dir) / f"{slug}.md"
    deep_path = Path(deep_dir) / f"{slug}.md"

    # Check if T3 already exists
    if deep_path.exists():
        return {
            "mode": "single",
            "files_enriched": [],
            "files_skipped": [{"source_path": file_path, "reason": "deep_exists"}],
            "total_enriched": 0,
            "total_skipped": 1,
            "deep_files_total": len(list(Path(deep_dir).glob("*.md"))) if Path(deep_dir).is_dir() else 0,
            "deep_total_tokens": _count_deep_tokens(deep_dir),
        }

    # Check if T2 exists
    if not detail_path.exists():
        return {
            "mode": "single",
            "files_enriched": [],
            "files_skipped": [{"source_path": file_path, "reason": "needs_t2_first"}],
            "total_enriched": 0,
            "total_skipped": 1,
            "deep_files_total": len(list(Path(deep_dir).glob("*.md"))) if Path(deep_dir).is_dir() else 0,
            "deep_total_tokens": _count_deep_tokens(deep_dir),
        }

    detail_content = detail_path.read_text(encoding="utf-8", errors="replace")
    estimated = estimate_deep_tokens(detail_content)

    return {
        "mode": "single",
        "files_enriched": [{
            "source_path": file_path,
            "detail_path": str(detail_path),
            "deep_path": str(deep_path),
            "status": "ready",
            "estimated_tokens": estimated,
        }],
        "files_skipped": [],
        "total_enriched": 1,
        "total_skipped": 0,
        "deep_files_total": len(list(Path(deep_dir).glob("*.md"))) if Path(deep_dir).is_dir() else 0,
        "deep_total_tokens": _count_deep_tokens(deep_dir),
        "instruction": {
            "action": "generate_t3",
            "source_path": file_path,
            "detail_path": str(detail_path),
            "deep_path": str(deep_path),
            "format": "deep-dive",
            "max_tokens": 2000,
        },
    }


# ── Batch mode ───────────────────────────────────────────────────────────────

def enrich_batch(
    details_dir: str,
    deep_dir: str,
    query_history_path: str | None,
    batch_size: int,
    meta: dict | None = None,
) -> dict:
    """Identify and return top N candidates for T3 enrichment."""
    details = scan_detail_files(details_dir)
    deep = scan_deep_files(deep_dir)

    candidates = find_enrichment_candidates(
        details, deep, query_history_path, details_dir, meta,
    )

    top = candidates[:batch_size]
    skipped_existing = [
        {"source_path": d.get("source_path_hint", d["slug"]), "reason": "deep_exists"}
        for d in details
        if d["slug"] in {dd["slug"] for dd in deep}
    ]

    enrichment_list = []
    for c in top:
        deep_path = str(Path(deep_dir) / f"{c['slug']}.md")
        enrichment_list.append({
            "source_path": c["source_path"],
            "detail_path": c["detail_path"],
            "deep_path": deep_path,
            "status": "ready",
            "estimated_tokens": estimate_deep_tokens(
                Path(c["detail_path"]).read_text(encoding="utf-8", errors="replace")
            ),
            "priority_score": c["priority_score"],
        })

    return {
        "mode": "batch",
        "files_enriched": enrichment_list,
        "files_skipped": skipped_existing,
        "total_enriched": len(enrichment_list),
        "total_skipped": len(skipped_existing),
        "deep_files_total": len(deep),
        "deep_total_tokens": sum(d.get("estimated_tokens", 0) for d in deep),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _count_deep_tokens(deep_dir: str) -> int:
    """Sum token estimates for all T3 files."""
    deep_path = Path(deep_dir)
    if not deep_path.is_dir():
        return 0
    total = 0
    for f in deep_path.iterdir():
        if f.is_file() and f.name.endswith(".md"):
            total += estimate_tokens(f.read_text(encoding="utf-8", errors="replace"))
    return total


def update_meta(meta_path: str, deep_dir: str) -> None:
    """Update meta.json with T3 tracking fields."""
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return

    deep_files = scan_deep_files(deep_dir)
    meta["deep_files"] = len(deep_files)
    meta["deep_total_tokens"] = sum(d.get("estimated_tokens", 0) for d in deep_files)

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    details_dir: str | None = None
    deep_dir: str | None = None
    meta_path: str | None = None
    index_path: str | None = None
    file_path: str | None = None
    batch_size = 5
    report_mode = False
    query_history: str | None = None

    i = 0
    while i < len(args):
        if args[i] == "--details-dir" and i + 1 < len(args):
            details_dir = args[i + 1]
            i += 2
        elif args[i] == "--deep-dir" and i + 1 < len(args):
            deep_dir = args[i + 1]
            i += 2
        elif args[i] == "--meta" and i + 1 < len(args):
            meta_path = args[i + 1]
            i += 2
        elif args[i] == "--index" and i + 1 < len(args):
            index_path = args[i + 1]
            i += 2
        elif args[i] == "--file" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
        elif args[i] == "--batch" and i + 1 < len(args):
            batch_size = int(args[i + 1])
            i += 2
        elif args[i] == "--report":
            report_mode = True
            i += 1
        elif args[i] == "--query-history" and i + 1 < len(args):
            query_history = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    # Validate required args
    if details_dir is None:
        print("Error: --details-dir is required", file=sys.stderr)
        sys.exit(1)
    if deep_dir is None:
        print("Error: --deep-dir is required", file=sys.stderr)
        sys.exit(1)
    if meta_path is None:
        print("Error: --meta is required", file=sys.stderr)
        sys.exit(1)

    # Ensure deep directory exists
    Path(deep_dir).mkdir(parents=True, exist_ok=True)

    # Load meta
    meta: dict | None = None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Route to mode
    if report_mode:
        details = scan_detail_files(details_dir)
        deep = scan_deep_files(deep_dir)
        candidates = find_enrichment_candidates(
            details, deep, query_history, details_dir, meta,
        )
        result = generate_report(details, deep, candidates)
    elif file_path:
        result = enrich_single(file_path, details_dir, deep_dir)
    else:
        result = enrich_batch(
            details_dir, deep_dir, query_history, batch_size, meta,
        )

    # Update meta.json with current T3 counts
    update_meta(meta_path, deep_dir)

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
