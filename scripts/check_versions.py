#!/usr/bin/env python3
"""plugin-version-guard: catch Claude Code plugin releases that users will never receive.

Claude Code decides whether an installed plugin needs updating by comparing its
version string. The version comes from plugin.json, then from the plugin's entry
in marketplace.json, and only if neither sets one, from the git commit SHA.
So if a plugin declares a version and you push changes without bumping it,
`claude plugin update` reports "already at the latest version" and users keep
the old cached copy.

This script compares each plugin in a repository against a base ref (the
upstream branch by default) and reports:

  stale-version     plugin files changed but the effective version did not
  version-mismatch  plugin.json and marketplace.json declare different versions
  version-decrease  the version went down
  invalid-version   the version is not semver (X.Y.Z)

Modes:
  (default)  human-readable report, exit 0
  --ci       human-readable report, exit 1 if any problem was found
  --json     machine-readable report
  --hook     Claude Code PreToolUse hook: reads the hook JSON on stdin and,
             for `git push` commands only, asks the user to confirm when a
             problem is found. Never blocks on its own errors.

Standard library only. Requires git on PATH.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional

MANIFEST_REL = os.path.join(".claude-plugin", "plugin.json")
MARKETPLACE_REL = os.path.join(".claude-plugin", "marketplace.json")
PRUNE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox"}

# Changes to these paths (relative to the plugin root) don't change what users
# run, so they don't require a version bump. Override with --no-default-ignores.
DEFAULT_IGNORES = [
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "LICENSE.*",
    "tests/*",
    ".github/*",
    "examples/*",
]

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
# The release skill's bump script running earlier in the same command as the push:
# the bump happens after this hook runs, so the version on disk is not final yet.
BUMP_BEFORE_PUSH_RE = re.compile(r"bump_version\.py[\s\S]*\bgit\b[^;&|]*\bpush\b")
COMMIT_BEFORE_PUSH_RE = re.compile(r"\bgit\b[^;&|]*\bcommit\b[\s\S]*\bgit\b[^;&|]*\bpush\b")
GIT_PUSH_RE = re.compile(r"(?:^|[;&|(]\s*|\s)git(?:\s+-[cC]\s+\S+)*\s+push\b")


# --------------------------------------------------------------------------- git


def git(args: list[str], cwd: str, check: bool = True) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        if check:
            raise
        return None
    if out.returncode != 0:
        if check:
            raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} failed")
        return None
    return out.stdout


def repo_root(cwd: str) -> Optional[str]:
    out = git(["rev-parse", "--show-toplevel"], cwd, check=False)
    return out.strip() if out else None


def resolve_base(root: str, explicit: Optional[str]) -> tuple[Optional[str], str]:
    """Return (merge-base commit, human description of the base)."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    else:
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root, check=False)
        if upstream and upstream.strip():
            candidates.append(upstream.strip())
        candidates += ["origin/HEAD", "origin/main", "origin/master"]
    for ref in candidates:
        mb = git(["merge-base", ref, "HEAD"], root, check=False)
        if mb and mb.strip():
            return mb.strip(), ref
    return None, explicit or "upstream"


def show_json(root: str, rev: str, rel_path: str) -> Optional[dict]:
    rel_posix = rel_path.replace(os.sep, "/")
    out = git(["show", f"{rev}:{rel_posix}"], root, check=False)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def has_uncommitted(cwd: str) -> bool:
    out = git(["status", "--porcelain", "--untracked-files=no"], cwd, check=False)
    return bool(out and out.strip())


def read_json_disk(root: str, rel_path: str) -> Optional[dict]:
    try:
        with open(os.path.join(root, rel_path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def changed_files(root: str, base: str, plugin_rel: str, worktree: bool = False) -> list[str]:
    path = plugin_rel.replace(os.sep, "/") or "."
    target = [] if worktree else ["HEAD"]
    out = git(["diff", "--name-only", base, *target, "--", path], root, check=False) or ""
    return [line for line in out.splitlines() if line.strip()]


# ------------------------------------------------------------------ discovery


def discover_plugins(root: str) -> list[str]:
    """Plugin roots (relative to repo root) that contain .claude-plugin/plugin.json."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        if os.path.basename(dirpath) == ".claude-plugin" and "plugin.json" in filenames:
            plugin_root = os.path.dirname(dirpath)
            rel = os.path.relpath(plugin_root, root)
            found.append("" if rel == "." else rel)
    return sorted(found)


def norm_rel(path: str) -> str:
    p = os.path.normpath(path.replace("/", os.sep))
    return "" if p == "." else p


def marketplace_versions(market: Optional[dict]) -> dict[str, dict]:
    """Map plugin root (relative path) -> {"name", "version"} for local-path entries."""
    result: dict[str, dict] = {}
    if not market:
        return result
    for entry in market.get("plugins", []) or []:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if isinstance(source, str) and (source.startswith("./") or source in (".", "./")):
            result[norm_rel(source)] = {"name": entry.get("name"), "version": entry.get("version")}
    return result


# -------------------------------------------------------------------- semver


def parse_semver(v: str) -> Optional[tuple]:
    m = SEMVER_RE.match(v.strip()) if isinstance(v, str) else None
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    # A release sorts after its prereleases.
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")


# ------------------------------------------------------------------- analysis


@dataclass
class Issue:
    kind: str
    plugin: str
    message: str
    fix: str


@dataclass
class PluginReport:
    name: str
    path: str
    base_version: Optional[str]
    head_version: Optional[str]
    version_source: str
    changed: list[str] = field(default_factory=list)
    status: str = "ok"


def is_ignored(file_rel_to_plugin: str, patterns: list[str]) -> bool:
    f = file_rel_to_plugin.replace(os.sep, "/")
    return any(fnmatch.fnmatch(f, pat) for pat in patterns)


def effective_version(manifest: Optional[dict], market_entry: Optional[dict]) -> tuple[Optional[str], str]:
    if manifest and manifest.get("version"):
        return str(manifest["version"]), "plugin.json"
    if market_entry and market_entry.get("version"):
        return str(market_entry["version"]), "marketplace.json"
    return None, "commit SHA"


def analyze(cwd: str, base_ref: Optional[str] = None, ignores: Optional[list[str]] = None,
            worktree: bool = False) -> dict:
    """worktree=True checks the files as they are on disk (what a commit in the same
    command is about to record) instead of the last commit."""
    root = repo_root(cwd)
    if not root:
        return {"ok": True, "skipped": "not a git repository", "plugins": [], "issues": []}

    plugins = discover_plugins(root)
    if not plugins:
        return {"ok": True, "skipped": "no Claude Code plugins found", "plugins": [], "issues": []}

    base, base_desc = resolve_base(root, base_ref)
    ignores = DEFAULT_IGNORES if ignores is None else ignores

    current = (lambda rel: read_json_disk(root, rel)) if worktree else (lambda rel: show_json(root, "HEAD", rel))
    head_market = marketplace_versions(current(MARKETPLACE_REL))
    base_market = marketplace_versions(show_json(root, base, MARKETPLACE_REL)) if base else {}

    reports: list[PluginReport] = []
    issues: list[Issue] = []

    for rel in plugins:
        manifest_rel = os.path.join(rel, MANIFEST_REL) if rel else MANIFEST_REL
        head_manifest = current(manifest_rel)
        if head_manifest is None:
            # plugin.json exists on disk but isn't committed yet: nothing is pushed.
            continue
        name = head_manifest.get("name") or os.path.basename(rel) or os.path.basename(root)
        head_ver, source = effective_version(head_manifest, head_market.get(rel))

        rep = PluginReport(name=name, path=rel or ".", base_version=None,
                           head_version=head_ver, version_source=source)

        # plugin.json vs marketplace.json disagreement (checked on HEAD, no base needed).
        m_entry = head_market.get(rel)
        if head_manifest.get("version") and m_entry and m_entry.get("version") \
                and str(m_entry["version"]) != str(head_manifest["version"]):
            issues.append(Issue(
                "version-mismatch", name,
                f"plugin.json says {head_manifest['version']} but marketplace.json says {m_entry['version']}. "
                "Claude Code uses plugin.json, so the marketplace listing shows users the wrong version.",
                "Make both the same (the release skill does this for you).",
            ))
            rep.status = "problem"

        if head_ver and parse_semver(head_ver) is None:
            issues.append(Issue(
                "invalid-version", name, f"version '{head_ver}' is not semver (X.Y.Z).",
                "Use a version like 1.2.3.",
            ))
            rep.status = "problem"

        if base:
            base_manifest = show_json(root, base, manifest_rel)
            if base_manifest is None:
                rep.status = "new" if rep.status == "ok" else rep.status
            else:
                base_ver, _ = effective_version(base_manifest, base_market.get(rel))
                rep.base_version = base_ver
                prefix = (rel.replace(os.sep, "/") + "/") if rel else ""
                files = []
                for f in changed_files(root, base, rel, worktree):
                    inner = f[len(prefix):] if prefix and f.startswith(prefix) else f
                    if inner.replace(os.sep, "/") == ".claude-plugin/plugin.json":
                        continue
                    if not rel and inner.replace(os.sep, "/") == ".claude-plugin/marketplace.json":
                        continue
                    if is_ignored(inner, ignores):
                        continue
                    files.append(f)
                # A plugin at the repo root "contains" nested plugins; don't double count.
                if not rel:
                    nested = [p.replace(os.sep, "/") + "/" for p in plugins if p]
                    files = [f for f in files if not any(f.startswith(n) for n in nested)]
                rep.changed = files

                if files and head_ver is not None and head_ver == base_ver:
                    sample = ", ".join(files[:5]) + (f" (+{len(files) - 5} more)" if len(files) > 5 else "")
                    issues.append(Issue(
                        "stale-version", name,
                        f"{len(files)} file(s) changed since {base_desc} but the version is still {head_ver} "
                        f"(from {source}). Users who already installed it will NOT get these changes: {sample}",
                        "Bump the version (patch for fixes, minor for features), or remove `version` "
                        "everywhere so Claude Code uses the commit SHA.",
                    ))
                    rep.status = "problem"
                elif head_ver and base_ver:
                    hv, bv = parse_semver(head_ver), parse_semver(base_ver)
                    if hv and bv and hv < bv:
                        issues.append(Issue(
                            "version-decrease", name,
                            f"version went down from {base_ver} to {head_ver}.",
                            "Versions should only go up; users on the newer version won't move back.",
                        ))
                        rep.status = "problem"
        reports.append(rep)

    return {
        "ok": not issues,
        "worktree": worktree,
        "repo": root,
        "base": base_desc if base else None,
        "base_found": bool(base),
        "plugins": [asdict(r) for r in reports],
        "issues": [asdict(i) for i in issues],
    }


# -------------------------------------------------------------------- output


def format_text(report: dict) -> str:
    if report.get("skipped"):
        return f"plugin-version-guard: skipped ({report['skipped']})."
    lines = []
    base = report.get("base")
    lines.append(f"plugin-version-guard: compared against {base}" if base
                 else "plugin-version-guard: no upstream/base ref found; only checked consistency.")
    if report.get("worktree"):
        lines.append("  (checked your files as they are now, including uncommitted changes)")
    for p in report["plugins"]:
        ver = p["head_version"] or "none (updates by commit SHA)"
        changed = f", {len(p['changed'])} changed file(s)" if p["changed"] else ""
        was = f" (was {p['base_version']})" if p["base_version"] and p["base_version"] != p["head_version"] else ""
        lines.append(f"  - {p['name']} [{p['path']}]: version {ver}{was}{changed} -> {p['status']}")
    if report["issues"]:
        lines.append("")
        lines.append(f"{len(report['issues'])} problem(s):")
        for i in report["issues"]:
            lines.append(f"  [{i['kind']}] {i['plugin']}: {i['message']}")
            lines.append(f"      fix: {i['fix']}")
    else:
        lines.append("No version problems found.")
    return "\n".join(lines)


def hook_reason(report: dict) -> str:
    parts = ["plugin-version-guard found version problems before this push:"]
    for i in report["issues"]:
        parts.append(f"- [{i['kind']}] {i['plugin']}: {i['message']}")
    parts.append("Run /plugin-version-guard:release to bump and sync versions, or approve to push anyway.")
    return "\n".join(parts)


# ---------------------------------------------------------------------- hook


def push_target_dir(command: str, cwd: str) -> Optional[str]:
    """If the command runs `git push`, return the directory it runs in, else None."""
    if not GIT_PUSH_RE.search(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return cwd
    for idx, tok in enumerate(tokens):
        if tok == "git":
            j = idx + 1
            target = cwd
            while j < len(tokens) and tokens[j].startswith("-"):
                if tokens[j] == "-C" and j + 1 < len(tokens):
                    target = os.path.join(target, tokens[j + 1])
                    j += 2
                elif tokens[j] == "-c" and j + 1 < len(tokens):
                    j += 2
                else:
                    j += 1
            if j < len(tokens) and tokens[j] == "push":
                return target
    return cwd


def run_hook() -> int:
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") not in (None, "Bash"):
            return 0
        command = (payload.get("tool_input") or {}).get("command") or ""
        cwd = payload.get("cwd") or os.getcwd()
        target = push_target_dir(command, cwd)
        if target is None:
            return 0
        if BUMP_BEFORE_PUSH_RE.search(command):
            return 0
        commits_first = bool(COMMIT_BEFORE_PUSH_RE.search(command))
        report = analyze(target, worktree=commits_first)
        if report["ok"]:
            return 0
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": hook_reason(report),
            }
        }))
        return 0
    except Exception:  # never get in the user's way because of our own bug
        return 0


# ----------------------------------------------------------------------- cli


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".", help="directory inside the repository (default: .)")
    ap.add_argument("--base", help="git ref to compare against (default: upstream, then origin/HEAD/main/master)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--ci", action="store_true", help="exit 1 if problems are found")
    mode.add_argument("--json", action="store_true", help="print the report as JSON")
    mode.add_argument("--hook", action="store_true", help="run as a Claude Code PreToolUse hook")
    ap.add_argument("--ignore", action="append", default=[], help="extra glob (relative to plugin root) to ignore")
    state = ap.add_mutually_exclusive_group()
    state.add_argument("--worktree", action="store_true",
                       help="check files as they are on disk, including uncommitted changes")
    state.add_argument("--committed", action="store_true",
                       help="check only the last commit (what a plain `git push` sends)")
    ap.add_argument("--no-default-ignores", action="store_true", help=f"don't ignore {', '.join(DEFAULT_IGNORES)}")
    args = ap.parse_args(argv)

    if args.hook:
        return run_hook()

    ignores = ([] if args.no_default_ignores else list(DEFAULT_IGNORES)) + args.ignore
    path = os.path.abspath(args.path)
    worktree = args.worktree or (not args.committed and has_uncommitted(path))
    report = analyze(path, args.base, ignores, worktree=worktree)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text(report))
    return 1 if (args.ci and not report["ok"]) else 0


if __name__ == "__main__":
    sys.exit(main())
