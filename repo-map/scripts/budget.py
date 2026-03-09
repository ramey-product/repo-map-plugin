#!/usr/bin/env python3
"""Token budget estimator for repo-map skill.

Estimates token counts from file sizes using extension-based heuristics,
tracks budget consumption, and classifies budget zones.

Heuristics:
    - Code files (~3 chars/token): .py, .js, .ts, .go, .rs, .java, etc.
    - Prose files (~4 chars/token): .md, .txt, .rst, .html, .css, etc.
    - Config/data files (~3.5 chars/token): .json, .yaml, .toml, .xml, etc.

Usage:
    python budget.py --budget 25000                          # Show empty budget
    python budget.py --budget 25000 --scan SCAN.json         # Estimate from scan
    python budget.py --budget 25000 --consumed 12000         # Check remaining
    python budget.py --budget 25000 --consumed 12000 --scan SCAN.json  # Full status

Output (JSON to stdout):
    {
        "budget_tokens": 25000,
        "consumed_tokens": 12000,
        "remaining_tokens": 13000,
        "utilization_pct": 48.0,
        "zone": "green",
        "recommendation": "...",
        "file_estimates": [              # only with --scan
            {"path": "src/main.py", "size": 2048, "estimated_tokens": 683},
            ...
        ],
        "total_estimated_tokens": 15000  # only with --scan
    }

Zones:
    green    (0-60%)   — Ample room, explore freely
    yellow   (60-80%)  — Moderate, prefer high-value targets
    red      (80-90%)  — Tight, summarize-only mode
    critical (90%+)    — Stop exploration, compress existing
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ── Token estimation heuristics ──────────────────────────────────────────────

# Code files: dense syntax, ~3 chars per token
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".cs", ".rb", ".php", ".swift", ".dart", ".lua", ".ex", ".exs",
    ".erl", ".hs", ".scala", ".clj", ".r", ".jl", ".cpp", ".cc", ".c",
    ".h", ".hpp", ".vue", ".svelte", ".sql", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd",
}

# Prose files: natural language, ~4 chars per token
PROSE_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc", ".tex", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".org", ".wiki",
}

# Config/data files: structured but less dense, ~3.5 chars per token
CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".gradle", ".tf", ".hcl", ".graphql", ".gql",
    ".proto", ".csv", ".tsv",
}

# Chars per token for each category
CHARS_PER_TOKEN = {
    "code": 3.0,
    "prose": 4.0,
    "config": 3.5,
    "unknown": 3.5,  # conservative default
}

# ── Zone thresholds ──────────────────────────────────────────────────────────

ZONES = [
    (0.60, "green",    "Ample room. Explore freely, generate full T2 summaries."),
    (0.80, "yellow",   "Moderate usage. Prefer high-value targets, skip low-priority paths."),
    (0.90, "red",      "Tight budget. Summarize-only mode, no deep T3 analysis."),
    (1.00, "critical", "Budget nearly exhausted. Stop exploration, compress existing summaries."),
]


def classify_extension(ext: str) -> str:
    """Classify a file extension into a token density category."""
    ext = ext.lower()
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in PROSE_EXTENSIONS:
        return "prose"
    if ext in CONFIG_EXTENSIONS:
        return "config"
    return "unknown"


def estimate_tokens(size_bytes: int, ext: str) -> int:
    """Estimate token count from file size and extension."""
    category = classify_extension(ext)
    chars_per_token = CHARS_PER_TOKEN[category]
    return max(1, int(size_bytes / chars_per_token))


def classify_zone(utilization: float) -> tuple[str, str]:
    """Return (zone_name, recommendation) for a given utilization ratio."""
    for threshold, name, recommendation in ZONES:
        if utilization <= threshold:
            return name, recommendation
    # Over 100%
    return "critical", "Budget exceeded. Compress aggressively and stop all exploration."


def estimate_from_scan(scan_data: dict) -> list[dict]:
    """Estimate tokens for each file in scan output."""
    estimates = []
    for entry in scan_data.get("files", []):
        path = entry["path"]
        size = entry.get("size", 0)
        ext = entry.get("ext", "") or ""
        tokens = estimate_tokens(size, ext)
        estimates.append({
            "path": path,
            "size": size,
            "estimated_tokens": tokens,
        })
    return estimates


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    budget = None
    consumed = 0
    scan_file = None

    i = 0
    while i < len(args):
        if args[i] == "--budget" and i + 1 < len(args):
            budget = int(args[i + 1])
            i += 2
        elif args[i] == "--consumed" and i + 1 < len(args):
            consumed = int(args[i + 1])
            i += 2
        elif args[i] == "--scan" and i + 1 < len(args):
            scan_file = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if budget is None:
        print("Error: --budget is required", file=sys.stderr)
        sys.exit(1)

    if budget <= 0:
        print("Error: --budget must be positive", file=sys.stderr)
        sys.exit(1)

    # Build result
    remaining = max(0, budget - consumed)
    utilization = consumed / budget if budget > 0 else 0.0
    zone, recommendation = classify_zone(utilization)

    result: dict = {
        "budget_tokens": budget,
        "consumed_tokens": consumed,
        "remaining_tokens": remaining,
        "utilization_pct": round(utilization * 100, 1),
        "zone": zone,
        "recommendation": recommendation,
    }

    # Scan-based estimation
    if scan_file:
        with open(scan_file, "r") as f:
            scan_data = json.load(f)
        file_estimates = estimate_from_scan(scan_data)
        total_estimated = sum(e["estimated_tokens"] for e in file_estimates)
        result["file_estimates"] = file_estimates
        result["total_estimated_tokens"] = total_estimated

    json.dump(result, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
