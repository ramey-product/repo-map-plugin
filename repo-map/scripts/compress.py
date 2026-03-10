#!/usr/bin/env python3
"""Compress repo-map index.md when it exceeds a token threshold.

Applies three compression strategies in order:
1. Hierarchical Collapse — collapse fully-mapped directories to single lines
2. Depth Limiting — collapse deep directories (depth > 3) unless hot-path
3. Sibling Merging — merge 10+ same-extension files in a directory

Usage:
    python compress.py --index .repo-map/index.md
    python compress.py --index .repo-map/index.md --threshold 20000
    python compress.py --index .repo-map/index.md --details-dir .repo-map/details/
    python compress.py --index .repo-map/index.md --query-history .repo-map/queries.json
    python compress.py --index .repo-map/index.md --dry-run

Output (JSON to stdout):
    {
        "original_tokens": 32500,
        "compressed_tokens": 18200,
        "reduction_pct": 44.0,
        "threshold": 20000,
        "under_threshold": true,
        "strategies_applied": ["hierarchical_collapse", "depth_limiting", "sibling_merging"],
        "collapsed_dirs": [...],
        "preserved_hot_paths": ["src/api/", "src/auth/"],
        "compressed_index_path": ".repo-map/index.md"
    }
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ── Token estimation ─────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count: ~4 chars/token for markdown/prose."""
    return max(1, len(text) // 4)


# ── Tree node ────────────────────────────────────────────────────────────────

@dataclass
class TreeNode:
    """Represents a file or directory in the structure tree."""
    path: str
    depth: int
    is_dir: bool
    description: str  # text after the path (e.g. "— JWT auth handler")
    children: list[TreeNode] = field(default_factory=list)
    raw_line: str = ""  # original line from index.md
    is_mapped: bool = False  # True if has a description (not "(unmapped)")
    is_collapsed: bool = False  # True if already collapsed (from prior run)
    collapsed_line: str = ""  # replacement line when collapsed

    @property
    def extension(self) -> str:
        if self.is_dir:
            return ""
        return Path(self.path).suffix


# ── Parsing ──────────────────────────────────────────────────────────────────

# Patterns for tree lines
# File: "  src/auth/jwt.ts — JWT token creation"  or  "    - jwt.ts — JWT token creation"
# Dir:  "  src/auth/"  or  "  src/ [5 files], 3 subdirs  (unmapped)"
# Collapsed: "  src/utils/ [5 utility modules, see details/src-utils.md]"
_LINE_INDENT_RE = re.compile(r'^(\s*)')
_DIR_LINE_RE = re.compile(r'^(\s*)(?:-\s+)?(\S+/)\s*(.*)')
_FILE_LINE_RE = re.compile(r'^(\s*)(?:-\s+)?(\S+)\s*(.*)')
_COLLAPSED_RE = re.compile(r'\[.*?see details/.*?\]')
_UNMAPPED_RE = re.compile(r'\(unmapped\)')


def _measure_indent(line: str) -> int:
    """Count leading spaces as indent level (2 spaces = 1 level)."""
    m = _LINE_INDENT_RE.match(line)
    if not m:
        return 0
    spaces = len(m.group(1))
    return spaces // 2


def _parse_description(rest: str) -> tuple[str, bool, bool]:
    """Parse the rest of a line after the path.

    Returns (description, is_mapped, is_collapsed).
    """
    rest = rest.strip()
    if _COLLAPSED_RE.search(rest):
        return rest, True, True
    if _UNMAPPED_RE.search(rest):
        return rest, False, False
    if rest.startswith("—") or rest.startswith("-"):
        desc = rest.lstrip("—- ").strip()
        return desc, bool(desc), False
    return rest, bool(rest), False


def parse_structure_tree(structure_text: str) -> list[TreeNode]:
    """Parse the ## Structure section into a flat list of TreeNodes.

    Returns a flat list; parent-child relationships are inferred by indent.
    """
    nodes: list[TreeNode] = []
    for line in structure_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        indent = _measure_indent(line)

        # Try directory match first (ends with /)
        m = _DIR_LINE_RE.match(line)
        if m and m.group(2).endswith("/"):
            path = m.group(2)
            rest = m.group(3)
            desc, is_mapped, is_collapsed = _parse_description(rest)
            nodes.append(TreeNode(
                path=path,
                depth=indent,
                is_dir=True,
                description=desc,
                raw_line=line,
                is_mapped=is_mapped,
                is_collapsed=is_collapsed,
            ))
            continue

        # File match
        m = _FILE_LINE_RE.match(line)
        if m:
            path = m.group(2)
            # Skip lines that are just markdown artifacts
            if path.startswith("#") or path.startswith(">") or path.startswith("--("):
                continue
            rest = m.group(3)
            desc, is_mapped, is_collapsed = _parse_description(rest)
            nodes.append(TreeNode(
                path=path,
                depth=indent,
                is_dir=False,
                description=desc,
                raw_line=line,
                is_mapped=is_mapped,
                is_collapsed=is_collapsed,
            ))

    return nodes


def build_dir_tree(nodes: list[TreeNode]) -> dict[str, list[TreeNode]]:
    """Group nodes by their parent directory based on indent levels.

    Returns {dir_path: [child_nodes]}.
    Uses indent-based parent tracking: a node's parent is the most recent
    directory at indent level = node.indent - 1.
    """
    dir_children: dict[str, list[TreeNode]] = {}
    # Stack of (indent, dir_path) for tracking parent context
    dir_stack: list[tuple[int, str]] = []

    for node in nodes:
        # Pop stack to find correct parent
        while dir_stack and dir_stack[-1][0] >= node.depth:
            dir_stack.pop()

        parent = dir_stack[-1][1] if dir_stack else "__root__"

        if parent not in dir_children:
            dir_children[parent] = []
        dir_children[parent].append(node)

        if node.is_dir:
            dir_stack.append((node.depth, node.path))

    return dir_children


# ── Hot paths ────────────────────────────────────────────────────────────────

def load_hot_paths(query_history_path: str) -> set[str]:
    """Extract directory prefixes from query history as hot paths."""
    hot: set[str] = set()
    try:
        with open(query_history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return hot

    for entry in data.get("queries", []):
        for path in entry.get("paths_accessed", []):
            # Extract immediate parent directory only
            parts = path.replace("\\", "/").split("/")
            if len(parts) >= 2:
                parent = "/".join(parts[:-1]) + "/"
                hot.add(parent)
    return hot


# ── Detail file helpers ──────────────────────────────────────────────────────

def path_to_slug(path: str) -> str:
    """Convert a file/dir path to a detail file slug.

    src/utils/helper.py → src-utils-helper-py
    src/utils/ → src-utils
    """
    slug = path.replace("/", "-").replace("\\", "-").replace(".", "-")
    slug = re.sub(r'-+', '-', slug)
    slug = slug.strip("-").lower()
    return slug


def check_all_mapped(children: list[TreeNode], details_dir: str | None) -> bool:
    """Check if ALL file children of a directory have detail files."""
    if not details_dir:
        return False

    file_children = [c for c in children if not c.is_dir]
    if not file_children:
        return False

    details_path = Path(details_dir)
    for child in file_children:
        if not child.is_mapped:
            return False
        # Check if detail file exists
        slug = path_to_slug(child.path)
        if not (details_path / f"{slug}.md").exists():
            # Also try with parent dir prefix
            # The child.path might be relative (just filename) while detail
            # files use full paths from repo root. Try matching by filename.
            found = False
            for f in details_path.iterdir():
                if f.name.endswith(f"{slug}.md"):
                    found = True
                    break
            if not found:
                return False
    return True


def create_rollup_detail(
    dir_path: str, children: list[TreeNode], details_dir: str
) -> str:
    """Create a roll-up detail file for a collapsed directory.

    Returns the path to the created file (relative to .repo-map/).
    """
    slug = path_to_slug(dir_path)
    detail_filename = f"{slug}.md"
    detail_path = Path(details_dir) / detail_filename

    lines = [f"# {dir_path}\n", "", "Collapsed directory containing:\n", ""]
    for child in sorted(children, key=lambda c: c.path):
        if child.is_dir:
            lines.append(f"- **{child.path}** (subdirectory)")
        else:
            desc = child.description or "(no description)"
            lines.append(f"- **{child.path}** — {desc}")
    lines.append("")

    detail_path.write_text("\n".join(lines), encoding="utf-8")
    return f"details/{detail_filename}"


# ── Compression strategies ───────────────────────────────────────────────────

def apply_hierarchical_collapse(
    nodes: list[TreeNode],
    details_dir: str | None,
    hot_paths: set[str],
    dry_run: bool,
) -> tuple[list[TreeNode], list[dict]]:
    """Strategy 1: Collapse fully-mapped directories.

    Only collapse if:
    - ALL file children have detail files
    - Directory has >= 3 file children
    - Directory is NOT in hot paths
    - Directory is not already collapsed
    """
    dir_children = build_dir_tree(nodes)
    collapsed_dirs: list[dict] = []
    collapse_set: set[str] = set()  # dir paths to collapse

    for dir_path, children in dir_children.items():
        if dir_path == "__root__":
            continue

        file_children = [c for c in children if not c.is_dir]
        if len(file_children) < 3:
            continue

        if dir_path in hot_paths or any(dir_path.startswith(hp) for hp in hot_paths):
            continue

        # Check if any node for this dir is already collapsed
        dir_node = next((n for n in nodes if n.path == dir_path and n.is_dir), None)
        if dir_node and dir_node.is_collapsed:
            continue

        if not check_all_mapped(file_children, details_dir):
            continue

        collapse_set.add(dir_path)

        # Create roll-up detail file
        detail_pointer = ""
        if details_dir and not dry_run:
            detail_pointer = create_rollup_detail(dir_path, children, details_dir)

        collapsed_dirs.append({
            "path": dir_path,
            "files_collapsed": len(file_children),
            "detail_pointer": detail_pointer or f"details/{path_to_slug(dir_path)}.md",
        })

    if not collapse_set:
        return nodes, collapsed_dirs

    # Rebuild node list with collapsed directories
    result: list[TreeNode] = []
    skip_depth: int | None = None

    for node in nodes:
        # If we're skipping children of a collapsed dir
        if skip_depth is not None and node.depth > skip_depth:
            continue
        skip_depth = None

        if node.is_dir and node.path in collapse_set:
            children = dir_children.get(node.path, [])
            file_count = len([c for c in children if not c.is_dir])
            slug = path_to_slug(node.path)
            detail_ref = f"details/{slug}.md"
            indent = "  " * node.depth
            collapsed_line = f"{indent}{node.path} [{file_count} files, see {detail_ref}]"
            node.collapsed_line = collapsed_line
            node.is_collapsed = True
            node.raw_line = collapsed_line
            result.append(node)
            skip_depth = node.depth
        else:
            result.append(node)

    return result, collapsed_dirs


def apply_depth_limiting(
    nodes: list[TreeNode],
    hot_paths: set[str],
    max_depth: int = 3,
) -> list[TreeNode]:
    """Strategy 2: Collapse directories deeper than max_depth.

    Preserves hot-path directories at full depth.
    """
    result: list[TreeNode] = []
    collapsed_at_depth: dict[str, bool] = {}  # track which dirs we collapse

    for node in nodes:
        if node.is_collapsed:
            result.append(node)
            continue

        # Calculate actual directory depth from path
        path_parts = node.path.rstrip("/").split("/")
        path_depth = len(path_parts)

        if path_depth <= max_depth:
            result.append(node)
            continue

        # Check if in hot path
        is_hot = False
        for hp in hot_paths:
            if node.path.startswith(hp) or hp.startswith(node.path):
                is_hot = True
                break
        if is_hot:
            result.append(node)
            continue

        # Collapse this node if it's a directory, skip if it's a file under a deep dir
        if node.is_dir and not node.is_collapsed:
            indent = "  " * node.depth
            node.collapsed_line = f"{indent}{node.path} [deep, collapsed]"
            node.is_collapsed = True
            node.raw_line = node.collapsed_line
            result.append(node)
            collapsed_at_depth[node.path] = True
        elif not node.is_dir:
            # Check if any ancestor dir was collapsed at this depth
            parent_parts = path_parts[:-1]
            parent_collapsed = False
            for i in range(len(parent_parts)):
                ancestor = "/".join(parent_parts[:i + 1]) + "/"
                if ancestor in collapsed_at_depth:
                    parent_collapsed = True
                    break
            if not parent_collapsed:
                result.append(node)
            # else: skip file under collapsed dir

    return result


def apply_sibling_merging(
    nodes: list[TreeNode],
    details_dir: str | None,
    hot_paths: set[str],
    dry_run: bool,
    min_siblings: int = 10,
) -> tuple[list[TreeNode], list[dict]]:
    """Strategy 3: Merge 10+ same-extension files in a directory.

    Groups files by extension and collapses them into a single summary line.
    """
    dir_children = build_dir_tree(nodes)
    merge_dirs: list[dict] = []
    merge_set: dict[str, dict[str, list[TreeNode]]] = {}  # dir -> {ext -> [nodes]}

    for dir_path, children in dir_children.items():
        if dir_path in hot_paths or any(dir_path.startswith(hp) for hp in hot_paths):
            continue

        file_children = [c for c in children if not c.is_dir and not c.is_collapsed]
        if len(file_children) < min_siblings:
            continue

        # Group by extension
        by_ext: dict[str, list[TreeNode]] = {}
        for child in file_children:
            ext = child.extension or "(no ext)"
            if ext not in by_ext:
                by_ext[ext] = []
            by_ext[ext].append(child)

        for ext, group in by_ext.items():
            if len(group) >= min_siblings:
                if dir_path not in merge_set:
                    merge_set[dir_path] = {}
                merge_set[dir_path][ext] = group

    if not merge_set:
        return nodes, merge_dirs

    # Build set of nodes to remove (merged into summary)
    remove_nodes: set[int] = set()  # indices into nodes list
    insert_after: dict[int, list[str]] = {}  # index -> lines to insert

    for dir_path, ext_groups in merge_set.items():
        for ext, group in ext_groups.items():
            # Find the dir node index
            dir_idx = None
            for i, n in enumerate(nodes):
                if n.path == dir_path and n.is_dir:
                    dir_idx = i
                    break

            if dir_idx is None:
                # Files under __root__; find first file of the group
                first_idx = None
                for i, n in enumerate(nodes):
                    if n in group:
                        if first_idx is None:
                            first_idx = i
                        break
                if first_idx is not None:
                    dir_idx = first_idx

            if dir_idx is None:
                continue

            # Mark group nodes for removal
            for n in group:
                idx = nodes.index(n)
                remove_nodes.add(idx)

            # Create summary line
            ext_name = _ext_to_language(ext)
            indent = "  " * (group[0].depth if group else 1)
            dir_display = dir_path if dir_path != "__root__" else ""
            slug = path_to_slug(dir_path) if dir_path != "__root__" else "root"
            detail_ref = f"details/{slug}.md"
            summary_line = f"{indent}[{len(group)} {ext_name} files, see {detail_ref}]"

            if dir_idx not in insert_after:
                insert_after[dir_idx] = []
            insert_after[dir_idx].append(summary_line)

            # Create roll-up if not dry run
            if details_dir and not dry_run:
                create_rollup_detail(dir_path if dir_path != "__root__" else "root-files", group, details_dir)

            merge_dirs.append({
                "path": dir_path,
                "files_collapsed": len(group),
                "extension": ext,
                "detail_pointer": detail_ref,
            })

    # Rebuild nodes
    result: list[TreeNode] = []
    for i, node in enumerate(nodes):
        if i in remove_nodes:
            continue
        result.append(node)
        if i in insert_after:
            for line in insert_after[i]:
                result.append(TreeNode(
                    path="",
                    depth=node.depth + 1 if node.is_dir else node.depth,
                    is_dir=False,
                    description="",
                    raw_line=line,
                    is_mapped=True,
                    is_collapsed=True,
                    collapsed_line=line,
                ))

    return result, merge_dirs


def _ext_to_language(ext: str) -> str:
    """Convert file extension to human-readable language name."""
    mapping = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
        ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
        ".cs": "C#", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
        ".vue": "Vue", ".svelte": "Svelte", ".css": "CSS",
        ".scss": "SCSS", ".html": "HTML", ".md": "Markdown",
        ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
        ".xml": "XML", ".sql": "SQL", ".sh": "Shell",
        ".c": "C", ".cpp": "C++", ".h": "C/C++ header",
        ".vb": "VB.NET", ".fs": "F#",
    }
    return mapping.get(ext, ext.lstrip(".").upper() if ext else "misc")


# ── Index rebuild ────────────────────────────────────────────────────────────

def extract_structure_section(content: str) -> tuple[str, str, str]:
    """Split index.md into (before_structure, structure_text, after_structure).

    The structure section starts with "## Structure" and ends at the next "##" heading.
    """
    lines = content.split("\n")
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if line.strip().startswith("## Structure"):
            start_idx = i
        elif start_idx is not None and line.strip().startswith("## ") and i > start_idx:
            end_idx = i
            break

    if start_idx is None:
        return content, "", ""

    if end_idx is None:
        end_idx = len(lines)

    before = "\n".join(lines[:start_idx])
    structure = "\n".join(lines[start_idx:end_idx])
    after = "\n".join(lines[end_idx:])
    return before, structure, after


def rebuild_index(original_content: str, compressed_nodes: list[TreeNode]) -> str:
    """Replace ## Structure section with compressed tree, preserving other sections."""
    before, structure, after = extract_structure_section(original_content)

    # Rebuild structure section
    new_lines = ["## Structure"]
    for node in compressed_nodes:
        if node.is_collapsed and node.collapsed_line:
            new_lines.append(node.collapsed_line)
        else:
            new_lines.append(node.raw_line)
    new_lines.append("")  # blank line before next section

    new_structure = "\n".join(new_lines)

    parts = [before]
    if before and not before.endswith("\n"):
        parts.append("")
    parts.append(new_structure)
    if after:
        if not after.startswith("\n"):
            parts.append("")
        parts.append(after)

    result = "\n".join(parts)
    # Clean up excessive blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    args = sys.argv[1:]
    index_path: str | None = None
    threshold = 20000
    details_dir: str | None = None
    query_history: str | None = None
    dry_run = False

    i = 0
    while i < len(args):
        if args[i] == "--index" and i + 1 < len(args):
            index_path = args[i + 1]
            i += 2
        elif args[i] == "--threshold" and i + 1 < len(args):
            threshold = int(args[i + 1])
            i += 2
        elif args[i] == "--details-dir" and i + 1 < len(args):
            details_dir = args[i + 1]
            i += 2
        elif args[i] == "--query-history" and i + 1 < len(args):
            query_history = args[i + 1]
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if index_path is None:
        print("Error: --index is required", file=sys.stderr)
        sys.exit(1)

    # Read index
    try:
        index_content = Path(index_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as e:
        print(f"Error reading index: {e}", file=sys.stderr)
        sys.exit(1)

    original_tokens = estimate_tokens(index_content)

    # Already under threshold?
    if original_tokens <= threshold:
        result = {
            "original_tokens": original_tokens,
            "compressed_tokens": original_tokens,
            "reduction_pct": 0.0,
            "threshold": threshold,
            "under_threshold": True,
            "strategies_applied": [],
            "collapsed_dirs": [],
            "preserved_hot_paths": [],
            "compressed_index_path": index_path,
        }
        json.dump(result, sys.stdout, indent=2)
        print()
        sys.exit(0)

    # Load hot paths
    hot_paths: set[str] = set()
    if query_history:
        hot_paths = load_hot_paths(query_history)

    # Extract and parse structure section
    before, structure_text, after = extract_structure_section(index_content)
    nodes = parse_structure_tree(structure_text)

    strategies_applied: list[str] = []
    all_collapsed_dirs: list[dict] = []

    # Strategy 1: Hierarchical Collapse
    nodes, collapsed = apply_hierarchical_collapse(nodes, details_dir, hot_paths, dry_run)
    if collapsed:
        strategies_applied.append("hierarchical_collapse")
        all_collapsed_dirs.extend(collapsed)

    # Check if under threshold
    compressed_content = rebuild_index(index_content, nodes)
    current_tokens = estimate_tokens(compressed_content)

    # Strategy 2: Depth Limiting
    if current_tokens > threshold:
        nodes = apply_depth_limiting(nodes, hot_paths)
        strategies_applied.append("depth_limiting")
        compressed_content = rebuild_index(index_content, nodes)
        current_tokens = estimate_tokens(compressed_content)

    # Strategy 3: Sibling Merging
    if current_tokens > threshold:
        nodes, merged = apply_sibling_merging(nodes, details_dir, hot_paths, dry_run)
        if merged:
            strategies_applied.append("sibling_merging")
            all_collapsed_dirs.extend(merged)
            compressed_content = rebuild_index(index_content, nodes)
            current_tokens = estimate_tokens(compressed_content)

    # Write compressed index (unless dry run)
    if not dry_run and strategies_applied:
        Path(index_path).write_text(compressed_content, encoding="utf-8")

    reduction = ((original_tokens - current_tokens) / original_tokens) * 100 if original_tokens > 0 else 0.0

    result = {
        "original_tokens": original_tokens,
        "compressed_tokens": current_tokens,
        "reduction_pct": round(reduction, 1),
        "threshold": threshold,
        "under_threshold": current_tokens <= threshold,
        "strategies_applied": strategies_applied,
        "collapsed_dirs": all_collapsed_dirs,
        "preserved_hot_paths": sorted(hot_paths),
        "compressed_index_path": index_path,
    }

    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
