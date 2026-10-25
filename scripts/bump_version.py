#!/usr/bin/env python3
"""Bump a Claude Code plugin's version and keep marketplace.json in sync.

Usage:
  bump_version.py <plugin-dir> patch|minor|major|X.Y.Z [--dry-run] [--add-version]

- Updates `version` in <plugin-dir>/.claude-plugin/plugin.json.
- If the repository's .claude-plugin/marketplace.json has a local-path entry for
  this plugin that also sets `version`, updates it to the same value.
- If the plugin has no version anywhere, it updates by commit SHA. Adding a
  version changes how users receive updates, so that requires --add-version.

Prints a JSON summary. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def bump(current: str, part: str) -> str:
    if SEMVER_RE.match(part):
        return part
    m = SEMVER_RE.match(current or "")
    if not m:
        raise SystemExit(f"Current version '{current}' is not semver; pass an explicit X.Y.Z.")
    major, minor, patch = (int(x) for x in m.groups()[:3])
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"Unknown bump '{part}'. Use patch, minor, major or X.Y.Z.")


def repo_root(start: str) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except OSError:
        pass
    return start


def find_market_entry(root: str, plugin_dir: str):
    path = os.path.join(root, ".claude-plugin", "marketplace.json")
    if not os.path.isfile(path):
        return path, None, None
    data = load(path)
    rel = os.path.normpath(os.path.relpath(plugin_dir, root))
    for idx, entry in enumerate(data.get("plugins", []) or []):
        src = entry.get("source") if isinstance(entry, dict) else None
        if isinstance(src, str) and (src.startswith("./") or src == "."):
            if os.path.normpath(src) == rel:
                return path, data, idx
    return path, data, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plugin_dir")
    ap.add_argument("bump", help="patch, minor, major, or an explicit X.Y.Z")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--add-version", action="store_true",
                    help="allow adding a version to a plugin that currently updates by commit SHA")
    args = ap.parse_args()

    plugin_dir = os.path.abspath(args.plugin_dir)
    manifest_path = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
    if not os.path.isfile(manifest_path):
        raise SystemExit(f"No .claude-plugin/plugin.json in {plugin_dir}")
    manifest = load(manifest_path)

    root = repo_root(plugin_dir)
    market_path, market, idx = find_market_entry(root, plugin_dir)
    entry = market["plugins"][idx] if market is not None and idx is not None else None

    current = manifest.get("version") or (entry or {}).get("version")
    if not current:
        if not args.add_version:
            print(json.dumps({
                "changed": False,
                "reason": "no-version",
                "message": "This plugin has no version, so users get every pushed commit (commit-SHA updates). "
                           "Nothing to bump. Re-run with --add-version to switch to explicit versions.",
            }, indent=2))
            return 0
        current = "0.0.0"

    new = bump(current, args.bump)
    summary = {"changed": True, "plugin": manifest.get("name"), "from": current, "to": new,
               "files": [os.path.relpath(manifest_path, root)]}

    manifest["version"] = new
    sync_market = entry is not None and "version" in entry
    if sync_market:
        entry["version"] = new
        summary["files"].append(os.path.relpath(market_path, root))

    if not args.dry_run:
        save(manifest_path, manifest)
        if sync_market:
            save(market_path, market)
    summary["dry_run"] = args.dry_run
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
