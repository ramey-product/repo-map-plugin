#!/usr/bin/env python3
"""Generate initial .repo-map/ directory from scan and frontier data.

Usage:
    python init.py --scan SCAN.json [--frontier FRONTIER.json] [--root DIR]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_json(filepath: str) -> dict:
    """Load and parse a JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def get_git_commit(root: str) -> str | None:
    """Get short git commit hash, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def detect_repo_name(root: str) -> str:
    """Derive repo name from root directory path."""
    return Path(root).resolve().name


def format_tech_stack(tech_list: list[str]) -> str:
    """Format tech stack list into a readable string."""
    if not tech_list:
        return "(unknown)"
    return ", ".join(sorted(set(tech_list)))


def format_entry_points(entry_points: list[str]) -> str:
    """Format entry points list into a readable string."""
    if not entry_points:
        return "(none detected)"
    return ", ".join(entry_points[:10])


def build_structure_tree(dirs: list[dict], files: list[dict]) -> str:
    """Build compact directory tree from scan data, marking all as (unmapped)."""
    if not dirs:
        return "(empty repository)\n"

    # Group dirs by top-level parent
    top_dirs: dict[str, dict] = {}
    for d in dirs:
        parts = Path(d["path"]).parts
        if not parts:
            continue
        top = parts[0]
        if top not in top_dirs:
            top_dirs[top] = {"file_count": 0, "sub_count": 0, "depth": 0}
        top_dirs[top]["file_count"] += d.get("file_count", 0)
        top_dirs[top]["sub_count"] += 1
        top_dirs[top]["depth"] = max(top_dirs[top]["depth"], d.get("depth", 0))

    # Count files at root level (depth 0)
    root_files = [f for f in files if "/" not in f["path"] and "\\" not in f["path"]]

    lines = []
    # Root-level files first
    for f in sorted(root_files, key=lambda x: x["path"]):
        lines.append(f"  {f['path']}  (unmapped)")

    # Top-level directories
    for name in sorted(top_dirs.keys()):
        info = top_dirs[name]
        count = info["file_count"]
        subs = info["sub_count"]
        suffix = f"[{count} files]" if count > 0 else ""
        sub_note = f", {subs} subdirs" if subs > 1 else ""
        lines.append(f"  {name}/  {suffix}{sub_note}  (unmapped)")

    return "\n".join(lines) + "\n"


def build_frontier_md(frontier_data: dict | None, total_files: int) -> str:
    """Build frontier.md content from frontier.py output."""
    template_path = Path(__file__).parent.parent / "templates" / "frontier-template.md"
    template = template_path.read_text(encoding="utf-8")

    if frontier_data is None:
        # No frontier data -- generate placeholder
        remaining = total_files
        high = "- (run frontier.py to generate exploration priorities)\n"
        medium = ""
        low = ""
        skipped = ""
    else:
        items = frontier_data.get("frontier", [])
        remaining = len([i for i in items if i.get("tier") != "skip"])

        high_items = [i for i in items if i.get("tier") == "high"]
        medium_items = [i for i in items if i.get("tier") == "medium"]
        low_items = [i for i in items if i.get("tier") == "low"]
        skip_items = [i for i in items if i.get("tier") == "skip"]

        high = format_frontier_tier(high_items) or "- (none)\n"
        medium = format_frontier_tier(medium_items) or "- (none)\n"
        low = format_frontier_tier(low_items) or "- (none)\n"
        skipped = format_frontier_skipped(skip_items) or "- (none)\n"

    return template.format(
        session_number=0,
        next_session=1,
        remaining_files=remaining,
        high_priority_items=high.rstrip(),
        medium_priority_items=medium.rstrip(),
        low_priority_items=low.rstrip(),
        skipped_items=skipped.rstrip(),
    )


def format_frontier_tier(items: list[dict], max_items: int = 50) -> str:
    """Format frontier items for a priority tier."""
    lines = []
    for item in items[:max_items]:
        path = item["path"]
        score = item.get("score", 0)
        lines.append(f"- [ ] {path}  P:{score}")
    if len(items) > max_items:
        lines.append(f"- ... and {len(items) - max_items} more")
    return "\n".join(lines) + "\n" if lines else ""


def format_frontier_skipped(items: list[dict]) -> str:
    """Format skipped items (no checkboxes)."""
    lines = []
    for item in items[:30]:
        path = item["path"]
        lines.append(f"- {path}  (skip)")
    if len(items) > 30:
        lines.append(f"- ... and {len(items) - 30} more")
    return "\n".join(lines) + "\n" if lines else ""


def build_index_md(scan_data: dict, commit: str | None) -> str:
    """Build index.md content from scan data."""
    template_path = Path(__file__).parent.parent / "templates" / "index-template.md"
    template = template_path.read_text(encoding="utf-8")

    repo_name = detect_repo_name(scan_data["root"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_files = scan_data.get("total_files", 0)
    tech = format_tech_stack(scan_data.get("tech_stack", []))
    entries = format_entry_points(scan_data.get("entry_points", []))
    tree = build_structure_tree(
        scan_data.get("dirs", []),
        scan_data.get("files", []),
    )

    return template.format(
        repo_name=repo_name,
        generated_date=now,
        commit_hash=commit or "(unknown)",
        files_mapped=0,
        files_total=total_files,
        coverage_pct="0",
        tech_stack=tech,
        entry_points=entries,
        structure_tree=tree.rstrip(),
        key_patterns="- (unmapped -- patterns will emerge during exploration)",
        dependency_edges="- (unmapped -- edges will be discovered during exploration)",
    )


def build_meta_json(scan_data: dict, commit: str | None) -> dict:
    """Build meta.json dict from scan data."""
    template_path = Path(__file__).parent.parent / "templates" / "meta-template.json"
    meta = json.loads(template_path.read_text(encoding="utf-8"))

    repo_name = detect_repo_name(scan_data["root"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_files = scan_data.get("total_files", 0)
    total_dirs = scan_data.get("total_dirs", 0)
    ignore_patterns = scan_data.get("ignore_patterns_used", meta["config"]["ignore_patterns"])

    meta["repo_name"] = repo_name
    meta["repo_root"] = scan_data["root"]
    meta["last_commit"] = commit
    meta["last_run"] = now
    meta["sessions_completed"] = 0
    meta["coverage"]["files_mapped"] = 0
    meta["coverage"]["files_total"] = total_files
    meta["coverage"]["directories_fully_explored"] = 0
    meta["coverage"]["directories_total"] = total_dirs
    meta["coverage"]["estimated_sessions_remaining"] = None
    meta["index_token_estimate"] = 0
    meta["detail_files"] = 0
    meta["detail_total_tokens"] = 0
    meta["config"]["ignore_patterns"] = ignore_patterns

    # Detect priority patterns from tech stack
    tech = scan_data.get("tech_stack", [])
    priority = detect_priority_patterns(tech)
    if priority:
        meta["config"]["priority_patterns"] = priority

    return meta


_TECH_EXTS: dict[str, list[str]] = {
    "python": ["*.py"], "javascript": ["*.js", "*.jsx"], "typescript": ["*.ts", "*.tsx"],
    "java": ["*.java"], "kotlin": ["*.kt"], "go": ["*.go"], "rust": ["*.rs"],
    "ruby": ["*.rb"], "php": ["*.php"], "csharp": ["*.cs"], "swift": ["*.swift"],
    "vue": ["*.vue"], "svelte": ["*.svelte"], "scala": ["*.scala"],
    "elixir": ["*.ex", "*.exs"], "dart": ["*.dart"], "lua": ["*.lua"],
    "r": ["*.r", "*.R"], "julia": ["*.jl"], "haskell": ["*.hs"],
    "clojure": ["*.clj"], "erlang": ["*.erl"], "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.hpp", "*.cc"],
}


def detect_priority_patterns(tech_stack: list[str]) -> list[str]:
    """Detect file patterns to prioritize based on tech stack."""
    patterns = [ext for tech in tech_stack for ext in _TECH_EXTS.get(tech, [])]
    return sorted(set(patterns)) if patterns else ["*.py", "*.js", "*.ts"]


def write_artifacts(
    output_dir: Path, index_content: str, frontier_content: str, meta_dict: dict,
) -> dict:
    """Write all .repo-map/ artifacts to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    (output_dir / "details").mkdir(exist_ok=True)
    (output_dir / "deep").mkdir(exist_ok=True)

    # Write .gitignore
    gitignore_path = output_dir / ".gitignore"
    gitignore_path.write_text("*\n", encoding="utf-8")

    # Write index.md
    index_path = output_dir / "index.md"
    index_path.write_text(index_content, encoding="utf-8")

    # Write frontier.md
    frontier_path = output_dir / "frontier.md"
    frontier_path.write_text(frontier_content, encoding="utf-8")

    # Write meta.json
    meta_path = output_dir / "meta.json"
    meta_path.write_text(
        json.dumps(meta_dict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "output_dir": str(output_dir),
        "files_created": [
            str(gitignore_path),
            str(index_path),
            str(frontier_path),
            str(meta_path),
        ],
        "dirs_created": [
            str(output_dir / "details"),
            str(output_dir / "deep"),
        ],
    }


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate .repo-map/ directory from scan and frontier data."
    )
    parser.add_argument(
        "--scan",
        required=True,
        help="Path to scan.py JSON output (required).",
    )
    parser.add_argument(
        "--frontier",
        help="Path to frontier.py JSON output (optional).",
    )
    parser.add_argument(
        "--root",
        help="Repository root path. Defaults to scan data's root field.",
    )
    parser.add_argument(
        "--output",
        help="Output directory name. Defaults to '.repo-map' inside root.",
    )
    args = parser.parse_args()

    # Load scan data
    try:
        scan_data = load_json(args.scan)
    except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
        print(f"Error loading scan data: {e}", file=sys.stderr)
        sys.exit(1)

    # Load frontier data if provided
    frontier_data = None
    if args.frontier:
        try:
            frontier_data = load_json(args.frontier)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
            print(f"Warning: Could not load frontier data: {e}", file=sys.stderr)

    # Determine root
    root = args.root or scan_data.get("root", ".")
    root = str(Path(root).resolve())

    # Determine output directory
    output_name = args.output or ".repo-map"
    output_dir = Path(root) / output_name

    # Check if already initialized
    if output_dir.exists() and (output_dir / "meta.json").exists():
        print(
            f"Warning: {output_dir} already exists. Overwriting artifacts.",
            file=sys.stderr,
        )

    # Get git commit
    commit = get_git_commit(root)

    # Build artifacts
    index_content = build_index_md(scan_data, commit)
    frontier_content = build_frontier_md(
        frontier_data, scan_data.get("total_files", 0)
    )
    meta_dict = build_meta_json(scan_data, commit)

    # Estimate index tokens (rough: ~3.5 chars/token for markdown)
    index_tokens = max(1, len(index_content) // 4)
    meta_dict["index_token_estimate"] = index_tokens

    # Write everything
    result = write_artifacts(output_dir, index_content, frontier_content, meta_dict)

    # Output summary
    summary = {
        "status": "ok",
        "repo_name": meta_dict["repo_name"],
        "repo_root": root,
        "output_dir": str(output_dir),
        "commit": commit,
        "total_files": scan_data.get("total_files", 0),
        "total_dirs": scan_data.get("total_dirs", 0),
        "index_tokens": index_tokens,
        "files_created": result["files_created"],
        "dirs_created": result["dirs_created"],
    }
    print(json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    main()
