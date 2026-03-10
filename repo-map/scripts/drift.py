#!/usr/bin/env python3
"""Drift detection for repo-map skill.

Usage:
    python drift.py --meta META.json [--root DIR] [--scan SCAN.json] [--index INDEX.md]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def path_to_slug(path: str) -> str:
    """Convert a file/dir path to a detail file slug."""
    slug = path.replace("/", "-").replace("\\", "-").replace(".", "-")
    slug = re.sub(r'-+', '-', slug)
    return slug.strip("-").lower()




def get_current_commit(root: str) -> str | None:
    """Get current HEAD commit hash (short)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True,
            cwd=root, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def detect_git_changes(root: str, baseline_commit: str) -> dict:
    """Run git diff between baseline and HEAD, return categorized changes."""
    changes: dict[str, list] = {
        "modified": [],
        "added": [],
        "deleted": [],
        "renamed": [],
    }

    # Get non-rename changes (A, D, M, C)
    try:
        result = subprocess.run(
            [
                "git", "diff", f"{baseline_commit}..HEAD",
                "--name-status", "--diff-filter=ADMC",
                "--no-renames",
            ],
            capture_output=True, text=True,
            cwd=root, timeout=30,
        )
        if result.returncode != 0:
            print(f"Warning: git diff failed: {result.stderr.strip()}", file=sys.stderr)
            return changes

        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status, path = parts[0].strip(), parts[1].strip()
            if status == "A":
                changes["added"].append(path)
            elif status == "D":
                changes["deleted"].append(path)
            elif status in ("M", "C"):
                changes["modified"].append(path)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"Warning: git diff failed: {e}", file=sys.stderr)
        return changes

    # Get renames separately
    try:
        result = subprocess.run(
            [
                "git", "diff", f"{baseline_commit}..HEAD",
                "--name-status", "--diff-filter=R",
                "--find-renames=50%",
            ],
            capture_output=True, text=True,
            cwd=root, timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                if not line:
                    continue
                # Format: R<NNN>\told_path\tnew_path
                parts = line.split("\t")
                if len(parts) >= 3 and parts[0].startswith("R"):
                    changes["renamed"].append({
                        "from": parts[1].strip(),
                        "to": parts[2].strip(),
                    })
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Sort for deterministic output
    changes["modified"].sort()
    changes["added"].sort()
    changes["deleted"].sort()
    changes["renamed"].sort(key=lambda r: r["to"])

    return changes


def detect_hash_changes(root: str, meta: dict, scan_file: str | None) -> dict:
    """Fallback: detect changes by comparing current vs stored hashes."""
    changes: dict[str, list] = {
        "modified": [],
        "added": [],
        "deleted": [],
        "renamed": [],
    }

    # Build hash.py command
    hash_script = os.path.join(os.path.dirname(__file__), "hash.py")
    if not os.path.isfile(hash_script):
        print("Warning: hash.py not found, cannot run fallback detection", file=sys.stderr)
        return changes

    cmd = [sys.executable, hash_script, root]
    if scan_file:
        cmd.extend(["--scan", scan_file])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"Warning: hash.py failed: {result.stderr.strip()}", file=sys.stderr)
            return changes
        current_data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"Warning: hash fallback failed: {e}", file=sys.stderr)
        return changes

    current_hashes = current_data.get("hashes", {})
    previous_hashes = meta.get("file_hashes", {})

    if not previous_hashes:
        # No previous hashes — treat everything as added
        changes["added"] = sorted(current_hashes.keys())
        return changes

    current_paths = set(current_hashes.keys())
    previous_paths = set(previous_hashes.keys())

    changes["added"] = sorted(current_paths - previous_paths)
    changes["deleted"] = sorted(previous_paths - current_paths)
    changes["modified"] = sorted(
        p for p in current_paths & previous_paths
        if current_hashes[p] != previous_hashes[p]
    )

    return changes


def load_mapped_files(index_path: str) -> set[str]:
    """Parse index.md to extract currently-mapped file paths."""
    mapped = set()
    if not os.path.isfile(index_path):
        return mapped

    # Match lines that reference file paths (with extensions)
    file_pattern = re.compile(
        r"[-|]\s*`?([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)`?\s*[-|]?"
    )

    with open(index_path, "r") as f:
        for line in f:
            match = file_pattern.search(line)
            if match:
                path = match.group(1)
                # Skip template placeholders
                if "(unmapped)" not in line:
                    mapped.add(path)

    return mapped


def map_changes_to_actions(
    changes: dict, existing_details_dir: str | None,
) -> tuple[list[dict], list[str]]:
    """Cross-reference changes with existing detail files, return (actions, stale_details)."""
    actions = []
    stale_details = []

    # Find existing detail files
    existing_slugs: set[str] = set()
    if existing_details_dir and os.path.isdir(existing_details_dir):
        for fname in os.listdir(existing_details_dir):
            if fname.endswith(".md"):
                existing_slugs.add(fname[:-3])  # strip .md

    # 1. Deletes first (highest priority)
    for path in changes.get("deleted", []):
        actions.append({
            "action": "remove",
            "path": path,
            "reason": "deleted",
        })
        slug = path_to_slug(path)
        if slug in existing_slugs:
            stale_details.append(f"details/{slug}.md")

    # 2. Renames
    for rename in changes.get("renamed", []):
        actions.append({
            "action": "rename",
            "path": rename["to"],
            "from_path": rename["from"],
            "reason": "renamed",
        })
        old_slug = path_to_slug(rename["from"])
        if old_slug in existing_slugs:
            stale_details.append(f"details/{old_slug}.md")

    # 3. Remaps (modified files with existing summaries)
    for path in changes.get("modified", []):
        slug = path_to_slug(path)
        has_detail = slug in existing_slugs
        actions.append({
            "action": "remap",
            "path": path,
            "reason": "modified",
        })
        if has_detail:
            stale_details.append(f"details/{slug}.md")

    # 4. Adds (new files → frontier)
    for path in changes.get("added", []):
        actions.append({
            "action": "add_to_frontier",
            "path": path,
            "reason": "added",
        })

    return actions, stale_details


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    meta_file = None
    root = "."
    scan_file = None
    index_file = None

    i = 0
    while i < len(args):
        if args[i] == "--meta" and i + 1 < len(args):
            meta_file = args[i + 1]
            i += 2
        elif args[i] == "--root" and i + 1 < len(args):
            root = args[i + 1]
            i += 2
        elif args[i] == "--scan" and i + 1 < len(args):
            scan_file = args[i + 1]
            i += 2
        elif args[i] == "--index" and i + 1 < len(args):
            index_file = args[i + 1]
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not meta_file:
        print("Error: --meta META.json is required", file=sys.stderr)
        sys.exit(1)

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"Error: {root_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Load meta.json
    try:
        with open(meta_file, "r") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: cannot read meta file: {e}", file=sys.stderr)
        sys.exit(1)

    baseline_commit = meta.get("last_commit")
    current_commit = get_current_commit(str(root_path))

    # Detect changes
    use_git = baseline_commit is not None and current_commit is not None

    if use_git:
        # Check if baseline commit exists in git history
        try:
            check = subprocess.run(
                ["git", "cat-file", "-t", baseline_commit],
                capture_output=True, text=True,
                cwd=str(root_path), timeout=5,
            )
            if check.returncode != 0:
                use_git = False
        except (FileNotFoundError, subprocess.TimeoutExpired):
            use_git = False

    if use_git:
        changes = detect_git_changes(str(root_path), baseline_commit)
    else:
        changes = detect_hash_changes(str(root_path), meta, scan_file)

    # Compute detail directory path
    meta_dir = os.path.dirname(os.path.abspath(meta_file))
    details_dir = os.path.join(meta_dir, "details")

    # Map changes to actions
    actions, stale_details = map_changes_to_actions(changes, details_dir)

    # Compute summary
    total_changes = (
        len(changes["modified"])
        + len(changes["added"])
        + len(changes["deleted"])
        + len(changes["renamed"])
    )

    summary = {
        "total_changes": total_changes,
        "files_to_remap": len(changes["modified"]) + len(changes["renamed"]),
        "files_to_remove": len(changes["deleted"]),
        "files_to_add": len(changes["added"]),
        "no_changes": total_changes == 0,
    }

    # Build output
    output = {
        "root": str(root_path),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": baseline_commit,
        "current_commit": current_commit,
        "changes": changes,
        "summary": summary,
        "stale_details": stale_details,
        "actions": actions,
    }

    json.dump(output, sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
