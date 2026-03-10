#!/usr/bin/env python3
"""File hasher for repo-map skill.

Usage:
    python hash.py [ROOT_DIR] [--scan SCAN.json] [--compare OLD.json]
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def truncated_sha256(filepath: str) -> str | None:
    """SHA-256 of file contents, truncated to first 12 hex chars."""
    try:
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()[:12]
    except (OSError, PermissionError):
        return None


def get_git_commit(root: str) -> str | None:
    """Get current short git commit hash, or None."""
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


def load_scan_paths(scan_file: str) -> list[str]:
    """Load file paths from scan.py JSON output."""
    with open(scan_file, "r") as f:
        scan_data = json.load(f)
    return [entry["path"] for entry in scan_data.get("files", [])]


def load_previous_hashes(compare_file: str) -> dict[str, str]:
    """Load previous hash results for comparison."""
    with open(compare_file, "r") as f:
        data = json.load(f)
    return data.get("hashes", {})


def hash_files(root: str, file_paths: list[str]) -> dict[str, str]:
    """Hash a list of files relative to root."""
    root_path = Path(root).resolve()
    hashes = {}

    for rel_path in file_paths:
        full_path = str(root_path / rel_path)
        file_hash = truncated_sha256(full_path)
        if file_hash is not None:
            hashes[rel_path] = file_hash

    return hashes


def compute_changes(
    current: dict[str, str],
    previous: dict[str, str],
) -> dict[str, list[str]]:
    """Compare current and previous hashes to find changes."""
    current_paths = set(current.keys())
    previous_paths = set(previous.keys())

    added = sorted(current_paths - previous_paths)
    deleted = sorted(previous_paths - current_paths)
    modified = sorted(
        p for p in current_paths & previous_paths
        if current[p] != previous[p]
    )

    return {
        "modified": modified,
        "added": added,
        "deleted": deleted,
    }


def discover_files(root: str) -> list[str]:
    """Walk directory tree to discover hashable files (fallback if no --scan)."""
    ignore_dirs = {
        "node_modules", ".git", "__pycache__", ".next", "dist", "build",
        ".cache", "venv", ".venv", ".repo-map", ".claude", "target",
        ".gradle", ".idea", ".vscode", "coverage",
    }
    ignore_exts = {
        ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dylib", ".dll",
        ".exe", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
        ".mp3", ".mp4", ".zip", ".tar", ".gz", ".woff", ".woff2",
        ".lock", ".map",
    }
    ignore_files = {
        ".DS_Store", "Thumbs.db", "yarn.lock", "package-lock.json",
        "pnpm-lock.yaml", "Cargo.lock", "go.sum", "poetry.lock",
    }

    root_path = Path(root).resolve()
    paths = []

    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
        dirnames[:] = [
            d for d in sorted(dirnames)
            if d not in ignore_dirs and not (d.startswith(".") and d not in {".github"})
        ]

        rel_dir = os.path.relpath(dirpath, root_path)
        if rel_dir == ".":
            rel_dir = ""

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if fname in ignore_files or ext in ignore_exts:
                continue
            if fname.endswith(".min.js") or fname.endswith(".min.css"):
                continue

            full_path = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(full_path) > 1_048_576:
                    continue
            except OSError:
                continue

            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            paths.append(rel_path)

    return paths


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    root = "."
    scan_file = None
    compare_file = None

    i = 0
    while i < len(args):
        if args[i] == "--scan" and i + 1 < len(args):
            scan_file = args[i + 1]
            i += 2
        elif args[i] == "--compare" and i + 1 < len(args):
            compare_file = args[i + 1]
            i += 2
        elif not args[i].startswith("--"):
            root = args[i]
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"Error: {root_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Get file list
    if scan_file:
        file_paths = load_scan_paths(scan_file)
    else:
        file_paths = discover_files(str(root_path))

    # Compute hashes
    hashes = hash_files(str(root_path), file_paths)

    result: dict = {
        "root": str(root_path),
        "hashed_at": datetime.now(timezone.utc).isoformat(),
        "commit": get_git_commit(str(root_path)),
        "total_files": len(hashes),
        "hashes": hashes,
    }

    # Compare if requested
    if compare_file:
        previous = load_previous_hashes(compare_file)
        result["changes"] = compute_changes(hashes, previous)

    json.dump(result, sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
