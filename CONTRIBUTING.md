# Contributing

Thanks for your interest in contributing to repo-map!

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/repo-map-plugin.git
   cd repo-map-plugin
   ```
3. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature
   ```

## Code Conventions

- **Python 3.9+ stdlib only** — no pip dependencies. Every script must run with a bare Python install.
- **`from __future__ import annotations`** — required at the top of every Python file.
- **JSON to stdout, errors to stderr** — scripts are CLI tools designed to be piped together.
- **Standalone scripts** — each script in `repo-map/scripts/` is a self-contained CLI tool. No shared imports between scripts.

## Testing

Run the benchmark suite to validate all scripts:

```bash
python3 repo-map/scripts/bench.py --seed 42
```

This exercises the full pipeline (scan, hash, frontier, init, budget, drift, compress, enrich) across 5 synthetic repository sizes. All tiers should pass.

## Submitting Changes

1. Ensure `bench.py` passes for all tiers
2. Keep commits focused — one logical change per commit
3. Open a pull request against `main` with a clear description of the change

## Reporting Issues

Open an issue on GitHub with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Python version and OS
