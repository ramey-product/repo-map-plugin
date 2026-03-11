#!/usr/bin/env python3
"""Token budget estimator for repo-map skill.

Usage:
    python budget.py --budget N [--consumed N] [--scan SCAN.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".cs", ".rb", ".php", ".swift", ".dart", ".lua", ".ex", ".exs",
    ".erl", ".hs", ".scala", ".clj", ".r", ".jl", ".cpp", ".cc", ".c",
    ".h", ".hpp", ".vue", ".svelte", ".sql", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd",
}

PROSE_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc", ".tex", ".html", ".htm", ".css",
    ".scss", ".sass", ".less", ".org", ".wiki",
}

CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
    ".env", ".properties", ".gradle", ".tf", ".hcl", ".graphql", ".gql",
    ".proto", ".csv", ".tsv",
}

CHARS_PER_TOKEN = {
    "code": 3.0,
    "prose": 4.0,
    "config": 3.5,
    "unknown": 3.5,  # conservative default
}

ZONES = [
    (0.60, "green",    "Ample room. Explore freely, generate full T2 summaries."),
    (0.80, "yellow",   "Moderate usage. Prefer high-value targets, skip low-priority paths."),
    (0.90, "red",      "Tight budget. Summarize-only mode, no deep T3 analysis."),
    (1.00, "critical", "Budget nearly exhausted. Stop exploration, compress existing summaries."),
]


def classify_extension(ext: str) -> str:
    """Classify file extension into token density category."""
    ext = ext.lower()
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in PROSE_EXTENSIONS:
        return "prose"
    if ext in CONFIG_EXTENSIONS:
        return "config"
    return "unknown"


def estimate_tokens(size_bytes: int, ext: str) -> int:
    """Estimate token count from file size and extension category."""
    category = classify_extension(ext)
    chars_per_token = CHARS_PER_TOKEN[category]
    return max(1, int(size_bytes / chars_per_token))


def classify_zone(utilization: float) -> tuple[str, str]:
    """Return (zone_name, recommendation) for utilization ratio."""
    for threshold, name, recommendation in ZONES:
        if utilization <= threshold:
            return name, recommendation
    # Over 100%
    return "critical", "Budget exceeded. Compress aggressively and stop all exploration."


def estimate_from_scan(scan_data: dict) -> list[dict]:
    """Estimate tokens for each file in scan data."""
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

    json.dump(result, sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
