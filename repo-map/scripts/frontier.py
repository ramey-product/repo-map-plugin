#!/usr/bin/env python3
"""Exploration frontier scorer for repo-map skill.

Computes priority scores (0-100) for candidate paths to guide the
exploration order. Higher scores = explore first.

Scoring factors (weights):
    - Structural centrality  (30%): Entry points, shallow depth, config files
    - Query relevance        (25%): Matches against recent query history
    - Freshness              (25%): Recently modified files rank higher
    - Coverage gap           (20%): Unmapped paths rank higher than mapped ones

Usage:
    python frontier.py --scan SCAN.json
    python frontier.py --scan SCAN.json --mapped-paths MAPPED.json
    python frontier.py --scan SCAN.json --query-history QUERIES.json
    python frontier.py --scan SCAN.json --git-log GIT_LOG.json

Input files:
    SCAN.json       — Output from scan.py (required)
    MAPPED.json     — JSON array of already-mapped relative paths
    QUERIES.json    — JSON array of query strings from previous sessions
    GIT_LOG.json    — JSON object { "path": "ISO-timestamp", ... }

Output (JSON to stdout):
    {
        "scored_at": "ISO-8601 timestamp",
        "total_candidates": 50,
        "frontier": [
            {
                "path": "src/main.py",
                "score": 92,
                "tier": "high",
                "factors": {
                    "centrality": 28,
                    "relevance": 22,
                    "freshness": 25,
                    "coverage_gap": 17
                }
            },
            ...
        ]
    }

Tiers:
    high    (score >= 70)  — Explore immediately
    medium  (score 40-69)  — Explore if budget allows
    low     (score 10-39)  — Explore only in deep passes
    skip    (score < 10)   — Unlikely to add value
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Scoring weights ──────────────────────────────────────────────────────────

W_CENTRALITY = 30
W_RELEVANCE = 25
W_FRESHNESS = 25
W_COVERAGE_GAP = 20

# ── Centrality signals ──────────────────────────────────────────────────────

# Entry point files get high centrality
ENTRY_POINT_NAMES = {
    "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "index.ts", "index.js", "index.tsx", "index.jsx",
    "main.ts", "main.js", "main.tsx", "main.jsx",
    "app.ts", "app.js", "app.tsx", "app.jsx",
    "server.ts", "server.js", "server.py",
    "cli.py", "cli.ts", "cli.js",
    "main.go", "main.rs", "Main.java", "Program.cs",
    "mod.rs", "lib.rs",
}

# Config files get moderate centrality
CONFIG_NAMES = {
    "package.json", "tsconfig.json", "pyproject.toml", "setup.py",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "webpack.config.js", "vite.config.js", "vite.config.ts",
    "next.config.js", "next.config.ts", "next.config.mjs",
}

# Directories that typically hold important code
HIGH_VALUE_DIRS = {"src", "lib", "app", "core", "api", "pkg", "cmd", "internal"}


def score_centrality(path: str, entry_points: list[str], config_files: list[str]) -> int:
    """Score 0-100 based on structural importance."""
    fname = os.path.basename(path)
    parts = Path(path).parts
    depth = len(parts) - 1  # depth 0 = root level

    score = 0.0

    # Entry points: highest centrality
    if path in entry_points or fname in ENTRY_POINT_NAMES:
        score += 50

    # Config files: moderate centrality
    if path in config_files or fname in CONFIG_NAMES:
        score += 35

    # Shallow depth bonus (root=30, depth1=20, depth2=10)
    depth_bonus = max(0, 30 - depth * 10)
    score += depth_bonus

    # High-value directory bonus
    for part in parts[:-1]:  # check parent dirs, not filename
        if part in HIGH_VALUE_DIRS:
            score += 15
            break

    # README/docs at any level
    if fname.lower().startswith("readme"):
        score += 20

    return min(100, int(score))


def score_relevance(path: str, query_history: list[str]) -> int:
    """Score 0-100 based on match against recent queries."""
    if not query_history:
        return 50  # neutral when no history

    fname = os.path.basename(path).lower()
    path_lower = path.lower()
    parts = set(Path(path_lower).parts)

    match_count = 0
    for query in query_history:
        query_terms = query.lower().split()
        for term in query_terms:
            if len(term) < 3:
                continue
            if term in fname or term in path_lower:
                match_count += 2  # direct match
            elif any(term in part for part in parts):
                match_count += 1  # partial path match

    if match_count == 0:
        return 20  # low but not zero — might still be relevant

    # Scale: 1 match = 40, 2 = 55, 3 = 65, 5+ = 80+
    return min(100, 30 + match_count * 12)


def score_freshness(path: str, git_log: dict[str, str] | None) -> int:
    """Score 0-100 based on recency of modifications."""
    if not git_log:
        return 50  # neutral when no git data

    timestamp_str = git_log.get(path)
    if not timestamp_str:
        return 30  # no history = probably old or untracked

    try:
        mod_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_ago = (now - mod_time).days

        if days_ago <= 1:
            return 100
        elif days_ago <= 7:
            return 85
        elif days_ago <= 30:
            return 65
        elif days_ago <= 90:
            return 45
        elif days_ago <= 365:
            return 25
        else:
            return 10
    except (ValueError, TypeError):
        return 30


def score_coverage_gap(path: str, mapped_paths: set[str]) -> int:
    """Score 0-100 based on whether the path is already mapped."""
    if path in mapped_paths:
        return 0  # already mapped, no gap

    # Check if parent directory is partially mapped
    parent = str(Path(path).parent)
    sibling_mapped = any(
        p.startswith(parent + os.sep) or (parent == "." and os.sep not in p)
        for p in mapped_paths
    )

    if sibling_mapped:
        return 60  # siblings mapped but not this file

    return 100  # completely unmapped area


def compute_score(
    path: str,
    entry_points: list[str],
    config_files: list[str],
    query_history: list[str],
    git_log: dict[str, str] | None,
    mapped_paths: set[str],
) -> dict:
    """Compute weighted priority score for a single path."""
    c = score_centrality(path, entry_points, config_files)
    r = score_relevance(path, query_history)
    f = score_freshness(path, git_log)
    g = score_coverage_gap(path, mapped_paths)

    total = int(
        (c * W_CENTRALITY + r * W_RELEVANCE + f * W_FRESHNESS + g * W_COVERAGE_GAP)
        / 100
    )

    return {
        "path": path,
        "score": min(100, total),
        "factors": {
            "centrality": int(c * W_CENTRALITY / 100),
            "relevance": int(r * W_RELEVANCE / 100),
            "freshness": int(f * W_FRESHNESS / 100),
            "coverage_gap": int(g * W_COVERAGE_GAP / 100),
        },
    }


def classify_tier(score: int) -> str:
    """Classify a score into a priority tier."""
    if score >= 70:
        return "high"
    elif score >= 40:
        return "medium"
    elif score >= 10:
        return "low"
    return "skip"


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    scan_file = None
    mapped_file = None
    query_file = None
    git_log_file = None

    i = 0
    while i < len(args):
        if args[i] == "--scan" and i + 1 < len(args):
            scan_file = args[i + 1]
            i += 2
        elif args[i] == "--mapped-paths" and i + 1 < len(args):
            mapped_file = args[i + 1]
            i += 2
        elif args[i] == "--query-history" and i + 1 < len(args):
            query_file = args[i + 1]
            i += 2
        elif args[i] == "--git-log" and i + 1 < len(args):
            git_log_file = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not scan_file:
        print("Error: --scan is required", file=sys.stderr)
        sys.exit(1)

    # Load inputs
    with open(scan_file, "r") as f:
        scan_data = json.load(f)

    entry_points = scan_data.get("entry_points", [])
    config_files = scan_data.get("config_files", [])
    file_paths = [entry["path"] for entry in scan_data.get("files", [])]

    # Optional inputs
    mapped_paths: set[str] = set()
    if mapped_file:
        with open(mapped_file, "r") as f:
            mapped_paths = set(json.load(f))

    query_history: list[str] = []
    if query_file:
        with open(query_file, "r") as f:
            query_history = json.load(f)

    git_log: dict[str, str] | None = None
    if git_log_file:
        with open(git_log_file, "r") as f:
            git_log = json.load(f)

    # Score all files
    scored = []
    for path in file_paths:
        result = compute_score(
            path, entry_points, config_files,
            query_history, git_log, mapped_paths,
        )
        result["tier"] = classify_tier(result["score"])
        scored.append(result)

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "total_candidates": len(scored),
        "frontier": scored,
    }

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
