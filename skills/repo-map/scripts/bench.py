#!/usr/bin/env python3
"""Benchmark suite for repo-map plugin token efficacy.

Usage:
    python bench.py [--tiers TIERS] [--seed N] [--output FILE] [--queries-per-session N]

Examples:
    python bench.py                           # all tiers, default seed
    python bench.py --tiers tiny,small        # subset of tiers
    python bench.py --seed 123 --output report.md
"""

from __future__ import annotations

import atexit
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from random import Random


# ---------------------------------------------------------------------------
# 1. Data structures
# ---------------------------------------------------------------------------

@dataclass
class RepoTierSpec:
    name: str
    file_count: int
    total_kb: int
    dir_depth: int
    py_ratio: float  # fraction of files that are .py
    js_ratio: float  # fraction that are .js
    has_tests: bool
    has_docs: bool


@dataclass
class BenchMetrics:
    tier: str
    file_count: int
    total_bytes: int
    naive_tokens_per_session: int = 0
    plugin_cold_tokens: int = 0
    plugin_warm_tokens: int = 0
    skill_tokens: int = 1023
    index_tokens: int = 0
    frontier_tokens: int = 0
    t2_count: int = 0
    t2_tokens: int = 0
    t3_count: int = 0
    t3_tokens: int = 0
    compressed_index_tokens: int = 0
    break_even_sessions: int = 0
    savings_3: float = 0.0
    savings_5: float = 0.0
    savings_10: float = 0.0
    pipeline_ok: bool = False


TIER_SPECS = {
    "tiny": RepoTierSpec("tiny", 20, 22, 1, 0.6, 0.3, False, False),
    "small": RepoTierSpec("small", 65, 75, 2, 0.5, 0.3, True, False),
    "medium": RepoTierSpec("medium", 200, 350, 3, 0.45, 0.3, True, True),
    "large": RepoTierSpec("large", 650, 2048, 4, 0.4, 0.3, True, True),
    "xl": RepoTierSpec("xl", 2000, 7680, 4, 0.35, 0.35, True, True),
}


# ---------------------------------------------------------------------------
# 2. Content generators — name pools and templates
# ---------------------------------------------------------------------------

_CLASS_NAMES = [
    "UserManager", "DataStore", "AuthService", "CacheLayer", "EventBus",
    "TaskRunner", "ConfigParser", "MetricsCollector", "Logger", "Router",
    "Middleware", "Validator", "Serializer", "Scheduler", "Pipeline",
    "Registry", "Factory", "Observer", "Adapter", "Controller",
    "Repository", "Gateway", "Handler", "Provider", "Dispatcher",
]

_METHOD_NAMES = [
    "initialize", "process", "validate", "transform", "execute",
    "handle_request", "parse_input", "build_output", "cleanup", "refresh",
    "connect", "disconnect", "serialize", "deserialize", "authenticate",
    "authorize", "emit_event", "subscribe", "unsubscribe", "configure",
    "reset", "flush", "aggregate", "partition", "merge",
]

_VAR_NAMES = [
    "result", "data", "config", "items", "context", "state", "buffer",
    "payload", "response", "options", "params", "cache", "mapping",
    "queue", "stack", "counter", "threshold", "timeout", "retries", "status",
]

_IMPORT_MODULES = [
    "os", "sys", "json", "logging", "typing", "pathlib", "datetime",
    "collections", "hashlib", "re", "math", "functools", "itertools",
    "dataclasses", "abc", "enum", "contextlib", "io", "copy", "textwrap",
]

_JS_IMPORTS = [
    "React", "useState", "useEffect", "useCallback", "useMemo",
    "express", "cors", "helmet", "morgan", "compression",
    "lodash", "axios", "moment", "dayjs", "uuid",
]


def _generate_python_file(rng: Random, target_bytes: int, module_name: str,
                          all_modules: list[str]) -> str:
    """Generate a realistic Python source file."""
    lines: list[str] = [f'"""Module {module_name}."""', ""]

    # imports
    n_imports = rng.randint(2, 5)
    for mod in rng.sample(_IMPORT_MODULES, min(n_imports, len(_IMPORT_MODULES))):
        lines.append(f"import {mod}")

    # cross-references to sibling modules
    siblings = [m for m in all_modules if m != module_name]
    if siblings:
        for sib in rng.sample(siblings, min(2, len(siblings))):
            lines.append(f"from {sib} import {rng.choice(_CLASS_NAMES)}")
    lines.append("")

    # generate classes until we hit target
    while len("\n".join(lines)) < target_bytes:
        cls = rng.choice(_CLASS_NAMES) + str(rng.randint(1, 99))
        lines.append(f"class {cls}:")
        n_methods = rng.randint(2, 5)
        for _ in range(n_methods):
            method = rng.choice(_METHOD_NAMES)
            var = rng.choice(_VAR_NAMES)
            lines.append(f"    def {method}(self, {var}):")
            body_lines = rng.randint(3, 8)
            for j in range(body_lines):
                indent = "        "
                if j == 0:
                    lines.append(f'{indent}{var} = self._{rng.choice(_VAR_NAMES)}')
                elif j == body_lines - 1:
                    lines.append(f"{indent}return {var}")
                else:
                    lines.append(f"{indent}{var} = {var}  # step {j}")
            lines.append("")
        lines.append("")

    return "\n".join(lines)[:target_bytes]


def _generate_js_file(rng: Random, target_bytes: int, module_name: str,
                      all_modules: list[str]) -> str:
    """Generate a realistic JavaScript/TypeScript source file."""
    lines: list[str] = [f"// Module: {module_name}", ""]

    # imports
    n_imports = rng.randint(2, 4)
    for imp in rng.sample(_JS_IMPORTS, min(n_imports, len(_JS_IMPORTS))):
        lines.append(f'import {{ {imp} }} from "{imp.lower()}";')

    siblings = [m for m in all_modules if m != module_name]
    if siblings:
        for sib in rng.sample(siblings, min(2, len(siblings))):
            lines.append(f'import {{ default as {sib} }} from "./{sib}";')
    lines.append("")

    while len("\n".join(lines)) < target_bytes:
        func = rng.choice(_METHOD_NAMES) + str(rng.randint(1, 99))
        var = rng.choice(_VAR_NAMES)
        lines.append(f"export const {func} = ({var}) => {{")
        body_lines = rng.randint(3, 8)
        for j in range(body_lines):
            if j == body_lines - 1:
                lines.append(f"  return {var};")
            else:
                lines.append(f"  const step{j} = {var};  // transform")
        lines.append("};")
        lines.append("")

    return "\n".join(lines)[:target_bytes]


def _generate_config_file(rng: Random, target_bytes: int,
                          config_type: str) -> str:
    """Generate a config file (JSON, YAML, or Markdown)."""
    if config_type == "json":
        obj: dict = {
            "name": f"project-{rng.randint(1,100)}",
            "version": f"{rng.randint(0,5)}.{rng.randint(0,20)}.{rng.randint(0,10)}",
            "description": "Auto-generated project configuration",
            "scripts": {
                "build": "node build.js",
                "test": "jest --coverage",
                "lint": "eslint src/",
            },
            "dependencies": {},
        }
        for _ in range(target_bytes // 40):
            obj["dependencies"][f"pkg-{rng.randint(1,999)}"] = f"^{rng.randint(1,9)}.0.0"
        return json.dumps(obj, indent=2)[:target_bytes]

    if config_type == "yaml":
        lines = ["# Configuration", f"name: project-{rng.randint(1,100)}"]
        while len("\n".join(lines)) < target_bytes:
            lines.append(f"setting_{rng.randint(1,999)}: {rng.choice(['true','false','null'])}")
        return "\n".join(lines)[:target_bytes]

    # markdown (docs)
    lines = [f"# Documentation {rng.randint(1,100)}", ""]
    while len("\n".join(lines)) < target_bytes:
        lines.append(f"## Section {rng.randint(1,50)}")
        lines.append(f"Description of feature {rng.randint(1,999)}.")
        lines.append("")
    return "\n".join(lines)[:target_bytes]


# ---------------------------------------------------------------------------
# 3. Directory structure planner + repo generation
# ---------------------------------------------------------------------------

def _file_size_distribution(rng: Random, count: int, total_kb: int) -> list[int]:
    """Log-normal distribution of file sizes in bytes."""
    total_bytes = total_kb * 1024
    raw = [max(1.0, math.exp(rng.gauss(0, 1.2))) for _ in range(count)]
    s = sum(raw)
    sizes = [max(100, int(r / s * total_bytes)) for r in raw]
    # adjust to hit total
    diff = total_bytes - sum(sizes)
    if diff != 0 and sizes:
        sizes[0] = max(100, sizes[0] + diff)
    return sizes


def _plan_directory_structure(spec: RepoTierSpec, rng: Random) -> list[dict]:
    """Plan file layout: returns list of {rel_path, type, target_bytes}."""
    sizes = _file_size_distribution(rng, spec.file_count, spec.total_kb)
    files: list[dict] = []

    # build directory prefixes based on depth
    if spec.dir_depth <= 1:
        dirs = ["src"]
    elif spec.dir_depth == 2:
        dirs = ["src/api", "src/models", "src/services"]
        if spec.has_tests:
            dirs.append("tests")
    elif spec.dir_depth == 3:
        dirs = [
            "src/api/routes", "src/api/middleware", "src/models",
            "src/services/core", "src/services/external", "src/utils",
        ]
        if spec.has_tests:
            dirs.extend(["tests/unit", "tests/integration"])
        if spec.has_docs:
            dirs.append("docs")
    else:
        dirs = [
            "packages/core/src/api", "packages/core/src/models",
            "packages/core/src/services", "packages/core/src/utils",
            "packages/shared/src", "packages/shared/src/types",
            "apps/web/src/components", "apps/web/src/pages",
            "apps/api/src/routes", "apps/api/src/middleware",
            "libs/common/src", "libs/testing/src",
        ]
        if spec.has_tests:
            dirs.extend([
                "packages/core/tests", "apps/web/tests",
                "apps/api/tests/integration",
            ])
        if spec.has_docs:
            dirs.extend(["docs/guides", "docs/api"])

    # assign file types
    n_py = int(spec.file_count * spec.py_ratio)
    n_js = int(spec.file_count * spec.js_ratio)
    n_config = spec.file_count - n_py - n_js

    type_list = (["py"] * n_py + ["js"] * n_js + ["config"] * n_config)
    rng.shuffle(type_list)

    ext_map = {"py": ".py", "js": ".js"}
    config_exts = [".json", ".yaml", ".md"]

    for i, (ftype, size) in enumerate(zip(type_list, sizes)):
        d = dirs[i % len(dirs)]
        if ftype == "config":
            ext = rng.choice(config_exts)
            name = f"config_{i}{ext}"
        else:
            ext = ext_map[ftype]
            name = f"mod_{i}{ext}"
        files.append({"rel_path": f"{d}/{name}", "type": ftype, "target_bytes": size})

    # add entry point + package.json at root
    files.append({"rel_path": "main.py", "type": "py", "target_bytes": 200})
    files.append({"rel_path": "package.json", "type": "config", "target_bytes": 300})

    return files


def _stable_hash(s: str) -> int:
    """Deterministic string hash (Python's hash() is randomized)."""
    h = 0
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h


def generate_synthetic_repo(spec: RepoTierSpec, seed: int) -> str:
    """Create a synthetic repo in a temp directory. Returns the path."""
    rng = Random(seed + _stable_hash(spec.name))
    tmpdir = tempfile.mkdtemp(prefix=f"bench_{spec.name}_")

    plan = _plan_directory_structure(spec, rng)
    all_modules = [
        Path(f["rel_path"]).stem
        for f in plan if f["type"] in ("py", "js")
    ]

    for entry in plan:
        fpath = Path(tmpdir) / entry["rel_path"]
        fpath.parent.mkdir(parents=True, exist_ok=True)

        if entry["type"] == "py":
            content = _generate_python_file(
                rng, entry["target_bytes"],
                Path(entry["rel_path"]).stem, all_modules,
            )
        elif entry["type"] == "js":
            content = _generate_js_file(
                rng, entry["target_bytes"],
                Path(entry["rel_path"]).stem, all_modules,
            )
        else:
            cfg = entry["rel_path"].rsplit(".", 1)[-1]
            cfg_type = {"json": "json", "yaml": "yaml", "md": "markdown"}.get(cfg, "json")
            content = _generate_config_file(rng, entry["target_bytes"], cfg_type)

        fpath.write_text(content)

    return tmpdir


# ---------------------------------------------------------------------------
# 4. Pipeline runner + T2/T3 simulation
# ---------------------------------------------------------------------------

def path_to_slug(path: str) -> str:
    """Convert a file/dir path to a detail file slug (matches drift/compress/enrich)."""
    slug = path.replace("/", "-").replace("\\", "-").replace(".", "-")
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-").lower()


def _run_script(scripts_dir: str, name: str, args: list[str],
                timeout: int = 120) -> tuple[str, str, int]:
    """Run a pipeline script, return (stdout, stderr, returncode)."""
    cmd = [sys.executable, str(Path(scripts_dir) / name)] + args
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout after {timeout}s", 1


def _simulate_t2_files(repo_dir: str, scan_data: dict,
                       frontier_data: dict, rng: Random) -> list[str]:
    """Write synthetic T2 detail files for top frontier entries."""
    details_dir = Path(repo_dir) / ".repo-map" / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    frontier = frontier_data.get("frontier", [])
    # top ~60%
    n = max(1, int(len(frontier) * 0.6))
    top = frontier[:n]

    all_paths = [f["path"] for f in scan_data.get("files", [])]
    created: list[str] = []

    for entry in top:
        src = entry["path"]
        slug = path_to_slug(src)
        detail_path = details_dir / f"{slug}.md"

        # pick 2-3 cross-references from other files
        others = [p for p in all_paths if p != src]
        refs = rng.sample(others, min(3, len(others)))
        ref_lines = "\n".join(f"- References `{r}`" for r in refs)

        tokens = rng.randint(200, 800)
        filler = "x " * (tokens * 2)  # ~4 chars per token, pad to hit size

        content = (
            f"# {src}\n\n"
            f"## Summary\nDetail summary for {src}.\n\n"
            f"## Cross-references\n{ref_lines}\n\n"
            f"## Analysis\n{filler}\n"
        )

        detail_path.write_text(content)
        created.append(str(detail_path.relative_to(Path(repo_dir) / ".repo-map")))

    return created


def _simulate_t3_files(repo_dir: str, t2_files: list[str],
                       rng: Random) -> list[str]:
    """Write synthetic T3 deep-dive files for top 15% of T2 entries."""
    deep_dir = Path(repo_dir) / ".repo-map" / "deep"
    deep_dir.mkdir(parents=True, exist_ok=True)

    n = max(1, int(len(t2_files) * 0.15))
    top = rng.sample(t2_files, min(n, len(t2_files)))
    created: list[str] = []

    for t2_rel in top:
        # t2_rel is like "details/src-api-mod-1-py.md"
        slug = Path(t2_rel).stem
        deep_path = deep_dir / f"{slug}.md"

        tokens = rng.randint(500, 2000)
        filler = "deep " * (tokens * 2)
        content = (
            f"# Deep dive: {slug}\n\n"
            f"## Detailed analysis\n{filler}\n"
        )
        deep_path.write_text(content)
        created.append(str(deep_path.relative_to(Path(repo_dir) / ".repo-map")))

    return created


def run_pipeline(repo_dir: str, scripts_dir: str,
                 seed: int) -> dict:
    """Run the full cold-start pipeline. Returns results dict."""
    rng = Random(seed)
    results: dict = {"steps": {}, "ok": True}

    # git init (needed for hash.py / drift.py)
    subprocess.run(
        ["git", "init"], cwd=repo_dir,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "add", "."], cwd=repo_dir,
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-c", "user.name=bench", "-c", "user.email=bench@test",
         "commit", "-m", "init", "--allow-empty"],
        cwd=repo_dir, capture_output=True, text=True,
    )

    # scan.py
    out, err, rc = _run_script(scripts_dir, "scan.py", [repo_dir])
    results["steps"]["scan"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    scan_data = json.loads(out)

    # write scan to disk for other scripts
    scan_path = Path(repo_dir) / ".bench_scan.json"
    scan_path.write_text(out)

    # frontier.py
    out, err, rc = _run_script(scripts_dir, "frontier.py",
                               ["--scan", str(scan_path)])
    results["steps"]["frontier"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    frontier_data = json.loads(out)

    frontier_path = Path(repo_dir) / ".bench_frontier.json"
    frontier_path.write_text(out)

    # budget.py --scan
    out, err, rc = _run_script(scripts_dir, "budget.py",
                               ["--budget", "200000",
                                "--scan", str(scan_path)])
    results["steps"]["budget"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    budget_data = json.loads(out)

    # init.py
    out, err, rc = _run_script(scripts_dir, "init.py",
                               ["--scan", str(scan_path),
                                "--frontier", str(frontier_path),
                                "--root", repo_dir])
    results["steps"]["init"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    init_data = json.loads(out)

    # simulate T2 detail files
    t2_files = _simulate_t2_files(repo_dir, scan_data, frontier_data, rng)

    # compress.py --dry-run
    index_path = Path(repo_dir) / ".repo-map" / "index.md"
    details_dir = Path(repo_dir) / ".repo-map" / "details"
    out, err, rc = _run_script(scripts_dir, "compress.py",
                               ["--index", str(index_path),
                                "--details-dir", str(details_dir),
                                "--dry-run"])
    results["steps"]["compress"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    compress_data = json.loads(out)

    # enrich.py --report
    deep_dir = Path(repo_dir) / ".repo-map" / "deep"
    deep_dir.mkdir(exist_ok=True)
    meta_path = Path(repo_dir) / ".repo-map" / "meta.json"
    out, err, rc = _run_script(scripts_dir, "enrich.py",
                               ["--details-dir", str(details_dir),
                                "--deep-dir", str(deep_dir),
                                "--meta", str(meta_path),
                                "--report"])
    results["steps"]["enrich"] = {"rc": rc, "stderr": err.strip()}
    if rc != 0:
        results["ok"] = False
        return results
    enrich_data = json.loads(out)

    # simulate T3
    t3_files = _simulate_t3_files(repo_dir, t2_files, rng)

    # store parsed outputs
    results["scan"] = scan_data
    results["frontier"] = frontier_data
    results["budget"] = budget_data
    results["init"] = init_data
    results["compress"] = compress_data
    results["enrich"] = enrich_data
    results["t2_files"] = t2_files
    results["t3_files"] = t3_files

    return results


# ---------------------------------------------------------------------------
# 5. Metrics collector
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = {"code": 3.0, "prose": 4.0, "config": 3.5, "unknown": 3.5}
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt",
    ".cs", ".rb", ".php", ".swift", ".dart", ".c", ".cpp", ".h",
}
_PROSE_EXTS = {".md", ".txt", ".rst", ".html", ".css"}
_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".xml"}


def _classify_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in _CODE_EXTS:
        return "code"
    if ext in _PROSE_EXTS:
        return "prose"
    if ext in _CONFIG_EXTS:
        return "config"
    return "unknown"


def _estimate_file_tokens(size: int, ext: str) -> int:
    return max(1, int(size / _CHARS_PER_TOKEN[_classify_ext(ext)]))


def _estimate_text_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _compute_break_even(naive_per: int, cold: int, warm: int) -> int:
    """Sessions needed for plugin to break even with naive."""
    if warm >= naive_per:
        return 0  # plugin never cheaper per session
    # total_naive(n) = naive_per * n
    # total_plugin(n) = cold + warm * (n - 1)
    # solve: naive_per * n = cold + warm * (n - 1)
    # n * (naive_per - warm) = cold - warm
    denom = naive_per - warm
    if denom <= 0:
        return 0
    return max(1, math.ceil((cold - warm) / denom))


def _compute_savings(naive_per: int, cold: int, warm: int,
                     sessions: int) -> float:
    """Percentage savings over N sessions."""
    total_naive = naive_per * sessions
    total_plugin = cold + warm * max(0, sessions - 1)
    if total_naive == 0:
        return 0.0
    return round((1.0 - total_plugin / total_naive) * 100, 1)


def collect_metrics(repo_dir: str, spec: RepoTierSpec,
                    pipeline: dict, queries_per_session: int) -> BenchMetrics:
    """Compute all token metrics for a single tier."""
    m = BenchMetrics(tier=spec.name, file_count=spec.file_count,
                     total_bytes=spec.total_kb * 1024)

    m.pipeline_ok = pipeline.get("ok", False)
    if not m.pipeline_ok:
        return m

    # --- Naive model: load all source each session ---
    scan = pipeline["scan"]
    naive_total = 0
    for f in scan.get("files", []):
        naive_total += _estimate_file_tokens(f.get("size", 0),
                                             f.get("ext", ""))
    m.naive_tokens_per_session = naive_total

    # --- Plugin model ---
    # index tokens
    index_path = Path(repo_dir) / ".repo-map" / "index.md"
    if index_path.exists():
        m.index_tokens = _estimate_text_tokens(index_path.read_text())

    # frontier tokens
    frontier_path = Path(repo_dir) / ".repo-map" / "frontier.md"
    if frontier_path.exists():
        m.frontier_tokens = _estimate_text_tokens(frontier_path.read_text())

    # compressed index
    compress = pipeline.get("compress", {})
    m.compressed_index_tokens = compress.get("compressed_tokens", m.index_tokens)

    # T2 file tokens
    details_dir = Path(repo_dir) / ".repo-map" / "details"
    if details_dir.exists():
        t2_files = list(details_dir.glob("*.md"))
        m.t2_count = len(t2_files)
        m.t2_tokens = sum(_estimate_text_tokens(f.read_text()) for f in t2_files)

    # T3 file tokens
    deep_dir = Path(repo_dir) / ".repo-map" / "deep"
    if deep_dir.exists():
        t3_files = list(deep_dir.glob("*.md"))
        m.t3_count = len(t3_files)
        m.t3_tokens = sum(_estimate_text_tokens(f.read_text()) for f in t3_files)

    # warm query cost: compressed_index + 2-3 T2 files (~1500-4500 tokens)
    avg_t2 = m.t2_tokens // max(1, m.t2_count)
    warm_query = m.compressed_index_tokens + avg_t2 * 3

    # cold start: SKILL + index + frontier + Q warm queries
    m.plugin_cold_tokens = (
        m.skill_tokens + m.index_tokens + m.frontier_tokens
        + warm_query * queries_per_session
    )

    # warm session: SKILL + compressed_index + drift cost + Q warm queries
    update_cost = int(m.index_tokens * 0.05)  # ~5% remap
    m.plugin_warm_tokens = (
        m.skill_tokens + m.compressed_index_tokens + update_cost
        + warm_query * queries_per_session
    )

    # break-even + savings
    m.break_even_sessions = _compute_break_even(
        m.naive_tokens_per_session, m.plugin_cold_tokens, m.plugin_warm_tokens,
    )
    m.savings_3 = _compute_savings(
        m.naive_tokens_per_session, m.plugin_cold_tokens,
        m.plugin_warm_tokens, 3,
    )
    m.savings_5 = _compute_savings(
        m.naive_tokens_per_session, m.plugin_cold_tokens,
        m.plugin_warm_tokens, 5,
    )
    m.savings_10 = _compute_savings(
        m.naive_tokens_per_session, m.plugin_cold_tokens,
        m.plugin_warm_tokens, 10,
    )

    return m


# ---------------------------------------------------------------------------
# 6. Report generator
# ---------------------------------------------------------------------------

def _fmt(n: int) -> str:
    """Format number with commas."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _bar(value: int, max_value: int, width: int = 40) -> str:
    """ASCII bar chart segment."""
    if max_value == 0:
        return ""
    filled = max(1, int(value / max_value * width))
    return "\u2588" * filled


def _session_table(all_m: list[BenchMetrics],
                   queries_per_session: int) -> str:
    """Multi-session projection table."""
    lines = [
        "| Tier | Sessions | Naive Total | Plugin Total | Savings |",
        "|------|----------|-------------|--------------|---------|",
    ]
    for m in all_m:
        if not m.pipeline_ok:
            continue
        for n in [1, 3, 5, 10]:
            total_naive = m.naive_tokens_per_session * n
            total_plugin = m.plugin_cold_tokens + m.plugin_warm_tokens * max(0, n - 1)
            sav = _compute_savings(m.naive_tokens_per_session,
                                   m.plugin_cold_tokens,
                                   m.plugin_warm_tokens, n)
            lines.append(
                f"| {m.tier:<6} | {n:>8} | {_fmt(total_naive):>11} "
                f"| {_fmt(total_plugin):>12} | {sav:>6.1f}% |"
            )
    return "\n".join(lines)


def generate_report(all_metrics: list[BenchMetrics], seed: int,
                    queries_per_session: int) -> str:
    """Build the full Markdown efficacy report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections: list[str] = []

    # header
    sections.append(
        f"# Repo-Map Plugin — Token Efficacy Report\n\n"
        f"Generated: {now}  \n"
        f"Seed: {seed} | Queries/session: {queries_per_session}\n"
    )

    # summary table
    sections.append("## Summary\n")
    header = (
        "| Tier | Files | Source KB | Naive/Session | Plugin Cold | "
        "Plugin Warm | Break-even | 5-session savings |"
    )
    sep = "|------|-------|----------|---------------|-------------|" \
          "------------|------------|-------------------|"
    rows: list[str] = [header, sep]
    for m in all_metrics:
        if not m.pipeline_ok:
            rows.append(f"| {m.tier} | — | — | PIPELINE FAILED | — | — | — | — |")
            continue
        rows.append(
            f"| {m.tier:<5} | {m.file_count:>5} | {m.total_bytes//1024:>8} "
            f"| {_fmt(m.naive_tokens_per_session):>13} "
            f"| {_fmt(m.plugin_cold_tokens):>11} "
            f"| {_fmt(m.plugin_warm_tokens):>10} "
            f"| {m.break_even_sessions:>10} "
            f"| {m.savings_5:>16.1f}% |"
        )
    sections.append("\n".join(rows))

    # multi-session projection
    sections.append("\n## Multi-Session Projection\n")
    sections.append(_session_table(all_metrics, queries_per_session))

    # visual comparison (ASCII bars)
    sections.append("\n## Visual Comparison (5 sessions)\n")
    sections.append("```")
    ok_metrics = [m for m in all_metrics if m.pipeline_ok]
    if ok_metrics:
        max_val = max(m.naive_tokens_per_session * 5 for m in ok_metrics)
        for m in ok_metrics:
            naive5 = m.naive_tokens_per_session * 5
            plugin5 = m.plugin_cold_tokens + m.plugin_warm_tokens * 4
            sections.append(f"{m.tier:<7} naive  |{_bar(naive5, max_val)}| {_fmt(naive5)}")
            sections.append(f"        plugin |{_bar(plugin5, max_val)}| {_fmt(plugin5)}")
            sections.append("")
    sections.append("```")

    # break-even table
    sections.append("\n## Break-Even Analysis\n")
    be_rows = [
        "| Tier | Break-even Session | After 10 Sessions |",
        "|------|--------------------|--------------------|",
    ]
    for m in all_metrics:
        if m.pipeline_ok:
            be_rows.append(
                f"| {m.tier:<5} | {m.break_even_sessions:>18} "
                f"| {m.savings_10:>17.1f}% |"
            )
    sections.append("\n".join(be_rows))

    # key findings
    sections.append("\n## Key Findings\n")
    for m in ok_metrics:
        ratio = m.plugin_warm_tokens / max(1, m.naive_tokens_per_session) * 100
        sections.append(
            f"- **{m.tier}** ({m.file_count} files): warm session uses "
            f"{ratio:.0f}% of naive cost, "
            f"saving {m.savings_5:.0f}% over 5 sessions"
        )

    # pipeline status
    sections.append("\n## Pipeline Status\n")
    for m in all_metrics:
        status = "PASS" if m.pipeline_ok else "FAIL"
        sections.append(f"- {m.tier}: {status}")

    # raw JSON (collapsible)
    sections.append("\n<details>\n<summary>Raw metrics JSON</summary>\n")
    sections.append("```json")
    raw = [asdict(m) for m in all_metrics]
    sections.append(json.dumps(raw, indent=2))
    sections.append("```\n</details>")

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# 7. Main + arg parsing
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    tiers_str = "tiny,small,medium,large,xl"
    seed = 42
    output_file = None
    queries_per_session = 5

    i = 0
    while i < len(args):
        if args[i] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif args[i] == "--tiers" and i + 1 < len(args):
            tiers_str = args[i + 1]
            i += 2
        elif args[i] == "--seed" and i + 1 < len(args):
            seed = int(args[i + 1])
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_file = args[i + 1]
            i += 2
        elif args[i] == "--queries-per-session" and i + 1 < len(args):
            queries_per_session = int(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    tier_names = [t.strip() for t in tiers_str.split(",")]
    for t in tier_names:
        if t not in TIER_SPECS:
            print(f"Unknown tier: {t}. Valid: {','.join(TIER_SPECS)}",
                  file=sys.stderr)
            sys.exit(1)

    scripts_dir = str(Path(__file__).parent)
    all_metrics: list[BenchMetrics] = []
    tempdirs: list[str] = []

    def cleanup():
        for d in tempdirs:
            shutil.rmtree(d, ignore_errors=True)

    atexit.register(cleanup)

    for name in tier_names:
        spec = TIER_SPECS[name]
        print(f"[bench] {name}: generating {spec.file_count} files "
              f"({spec.total_kb} KB)...", file=sys.stderr)

        repo_dir = generate_synthetic_repo(spec, seed)
        tempdirs.append(repo_dir)

        print(f"[bench] {name}: running pipeline...", file=sys.stderr)
        pipeline = run_pipeline(repo_dir, scripts_dir, seed)

        if not pipeline["ok"]:
            failed = [k for k, v in pipeline["steps"].items() if v["rc"] != 0]
            errs = {k: pipeline["steps"][k]["stderr"] for k in failed}
            print(f"[bench] {name}: PIPELINE FAILED at {failed}: {errs}",
                  file=sys.stderr)

        metrics = collect_metrics(repo_dir, spec, pipeline, queries_per_session)
        all_metrics.append(metrics)

        print(f"[bench] {name}: done (ok={metrics.pipeline_ok})",
              file=sys.stderr)

    # generate report
    report = generate_report(all_metrics, seed, queries_per_session)

    if output_file:
        Path(output_file).write_text(report)
        print(f"[bench] Report written to {output_file}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
