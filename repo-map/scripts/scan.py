#!/usr/bin/env python3
"""Structural scanner for repo-map skill.

Walks a directory tree, respects ignore patterns, detects entry points
and config files, and outputs structured JSON to stdout.

Usage:
    python scan.py [ROOT_DIR]
    python scan.py --help

Output (JSON to stdout):
    {
        "root": "/absolute/path",
        "scanned_at": "ISO-8601 timestamp",
        "total_files": 123,
        "total_dirs": 45,
        "files": [ { "path": "relative/path", "size": 1234, "ext": ".py" }, ... ],
        "dirs": [ { "path": "relative/dir", "file_count": 5, "depth": 2 }, ... ],
        "entry_points": [ "src/main.py", ... ],
        "config_files": [ "package.json", ... ],
        "tech_stack": ["python", "javascript"],
        "ignore_patterns_used": ["node_modules/", ...]
    }
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Ignore patterns ──────────────────────────────────────────────────────────

# Directories to skip entirely (matched against directory name)
IGNORE_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".next",
    ".nuxt",
    "dist",
    "build",
    ".cache",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    ".eggs",
    "*.egg-info",
    ".bundle",
    "vendor",
    "target",          # Rust/Java
    ".gradle",
    ".idea",
    ".vscode",
    ".repo-map",
    ".claude",
    "coverage",
    ".nyc_output",
    ".turbo",
    ".svelte-kit",
    ".output",
    ".vercel",
    ".netlify",
    ".terraform",
    "bower_components",
    "jspm_packages",
    ".parcel-cache",
    ".docusaurus",
}

# File extensions to skip
IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dylib", ".dll",
    ".exe", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".lock",  # package lock files
    ".min.js", ".min.css",
    ".map",   # source maps
    ".DS_Store",
}

# Specific filenames to skip
IGNORE_FILES = {
    ".DS_Store",
    "Thumbs.db",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    "yarn.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "Gemfile.lock",
    "poetry.lock",
    "Pipfile.lock",
    "composer.lock",
    "go.sum",
}

# ── Entry point detection ────────────────────────────────────────────────────

# Files that indicate project entry points (exact name match)
ENTRY_POINT_FILES = {
    "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "index.ts", "index.js", "index.tsx", "index.jsx",
    "main.ts", "main.js", "main.tsx", "main.jsx",
    "app.ts", "app.js", "app.tsx", "app.jsx",
    "server.ts", "server.js", "server.py",
    "cli.py", "cli.ts", "cli.js",
    "main.go", "main.rs", "Main.java", "Program.cs",
    "mod.rs", "lib.rs",
}

# Config/build files (exact name match)
CONFIG_FILES = {
    "package.json", "tsconfig.json", "pyproject.toml", "setup.py",
    "setup.cfg", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "build.gradle.kts", "Makefile", "CMakeLists.txt", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
    ".env.example", ".env.sample",
    "webpack.config.js", "webpack.config.ts",
    "vite.config.js", "vite.config.ts",
    "rollup.config.js", "rollup.config.ts",
    "next.config.js", "next.config.ts", "next.config.mjs",
    "nuxt.config.ts", "nuxt.config.js",
    "svelte.config.js", "astro.config.mjs",
    "tailwind.config.js", "tailwind.config.ts",
    "jest.config.js", "jest.config.ts", "vitest.config.ts",
    "pytest.ini", "tox.ini", "mypy.ini",
    ".eslintrc.js", ".eslintrc.json", ".prettierrc",
    "requirements.txt", "requirements.in",
    "Pipfile", "Gemfile", "composer.json",
    "Procfile", "fly.toml", "vercel.json", "netlify.toml",
    "terraform.tf",
    "SKILL.md",
}

# ── Tech stack detection ─────────────────────────────────────────────────────

# Map file extensions -> technology name
EXT_TO_TECH = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".scala": "scala",
    ".clj": "clojure",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".vue": "vue",
    ".svelte": "svelte",
}

# Config files -> technology name
CONFIG_TO_TECH = {
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
    "Gemfile": "ruby",
    "composer.json": "php",
    "next.config.js": "nextjs",
    "next.config.ts": "nextjs",
    "next.config.mjs": "nextjs",
    "nuxt.config.ts": "nuxt",
    "svelte.config.js": "svelte",
    "astro.config.mjs": "astro",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    "tailwind.config.js": "tailwind",
    "tailwind.config.ts": "tailwind",
}


def should_ignore_dir(name: str) -> bool:
    """Check if a directory name matches ignore patterns."""
    if name in IGNORE_DIRS:
        return True
    # Handle glob-like patterns (e.g., *.egg-info)
    for pattern in IGNORE_DIRS:
        if pattern.startswith("*") and name.endswith(pattern[1:]):
            return True
    # Skip hidden directories (except a few known ones)
    if name.startswith(".") and name not in {".github", ".circleci"}:
        return True
    return False


def should_ignore_file(name: str, ext: str) -> bool:
    """Check if a file should be ignored."""
    if name in IGNORE_FILES:
        return True
    if ext in IGNORE_EXTENSIONS:
        return True
    # Compound extensions like .min.js
    if name.endswith(".min.js") or name.endswith(".min.css"):
        return True
    return False


def scan_repo(root: str) -> dict:
    """Scan a repository and return structured data."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        print(f"Error: {root_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    files = []
    dirs = []
    entry_points = []
    config_files = []
    tech_set = set()
    dir_file_counts: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root_path, topdown=True):
        # Filter directories in-place to prevent os.walk from descending
        dirnames[:] = [
            d for d in sorted(dirnames)
            if not should_ignore_dir(d)
        ]

        rel_dir = os.path.relpath(dirpath, root_path)
        if rel_dir == ".":
            rel_dir = ""
        depth = rel_dir.count(os.sep) if rel_dir else 0

        file_count = 0

        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if should_ignore_file(fname, ext):
                continue

            rel_path = os.path.join(rel_dir, fname) if rel_dir else fname
            full_path = os.path.join(dirpath, fname)

            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue

            # Skip very large files (>1MB likely binary or generated)
            if size > 1_048_576:
                continue

            files.append({
                "path": rel_path,
                "size": size,
                "ext": ext if ext else None,
            })
            file_count += 1

            # Entry point detection
            if fname in ENTRY_POINT_FILES:
                entry_points.append(rel_path)

            # Config file detection
            if fname in CONFIG_FILES:
                config_files.append(rel_path)

            # Tech stack from extensions
            if ext in EXT_TO_TECH:
                tech_set.add(EXT_TO_TECH[ext])

            # Tech stack from config files
            if fname in CONFIG_TO_TECH:
                tech_set.add(CONFIG_TO_TECH[fname])

        if rel_dir:
            dirs.append({
                "path": rel_dir,
                "file_count": file_count,
                "depth": depth,
            })
            dir_file_counts[rel_dir] = file_count

    # Also detect entry points in src/ or app/ subdirectories
    # (already handled by the walk, just noting this is automatic)

    return {
        "root": str(root_path),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "total_files": len(files),
        "total_dirs": len(dirs),
        "files": files,
        "dirs": dirs,
        "entry_points": sorted(set(entry_points)),
        "config_files": sorted(set(config_files)),
        "tech_stack": sorted(tech_set),
        "ignore_patterns_used": sorted(IGNORE_DIRS),
    }


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    result = scan_repo(root)
    json.dump(result, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
