# Plugin Version Guard

**Stop shipping Claude Code plugin changes your users will never receive.**

Claude Code only updates an installed plugin when its version string changes. If your `plugin.json` says `"version": "1.0.0"` and you push fixes without bumping it, every existing user runs `claude plugin update`, sees *"already at the latest version (1.0.0)"*, and keeps the old cached copy. The only way out for them is a full uninstall and reinstall.

This plugin catches that before you push.

## What it does

| | |
|---|---|
| **Pre-push hook** | When Claude runs `git push` in a plugin repo, it checks every plugin whose files changed since your upstream branch. If the version didn't change, Claude Code asks you to confirm and explains why. It never blocks silently, and if the checker itself fails it stays out of the way. |
| **`/plugin-version-guard:check`** | On-demand audit of every plugin in the repo. |
| **`/plugin-version-guard:release`** | Picks a semver bump with you, updates `plugin.json`, keeps the `marketplace.json` entry in sync, runs `claude plugin validate --strict`, and offers to tag. |
| **CI mode** | `check_versions.py --ci` exits 1 on problems. See `examples/github-action.yml`. |

### What it detects

- **stale-version**: plugin files changed, version didn't
- **version-mismatch**: `plugin.json` and `marketplace.json` disagree (Claude Code uses `plugin.json`)
- **version-decrease**: the version went down
- **invalid-version**: not `X.Y.Z`

Plugins with **no version** update by commit SHA, so every push reaches users. That's valid, and the guard reports it as OK.

Changes to `README.md`, `CHANGELOG.md`, `LICENSE`, `tests/`, `.github/` and `examples/` inside a plugin don't require a bump by default. Skill and command markdown files always count.

## Install

```shell
/plugin marketplace add TejaChilumula/plugin-version-guard
/plugin install plugin-version-guard@plugin-version-guard
```

Requires `python3` and `git` on your PATH. No other dependencies.

## Use without Claude Code

```bash
python3 scripts/check_versions.py            # report
python3 scripts/check_versions.py --ci       # exit 1 on problems
python3 scripts/check_versions.py --json     # machine-readable
python3 scripts/check_versions.py --base v1.2.0   # compare against a tag
python3 scripts/bump_version.py path/to/plugin patch --dry-run
```

## How it decides

1. Finds every `.claude-plugin/plugin.json` in the repository.
2. Compares `HEAD` with the merge base of your upstream branch (falling back to `origin/HEAD`, `origin/main`, `origin/master`, or `--base`).
3. Resolves each plugin's effective version the way Claude Code does: `plugin.json`, then the plugin's `marketplace.json` entry, then the commit SHA.
4. Reports a problem when relevant files changed but the effective version is the same.

Only committed changes are checked, because that's what a push sends.

## Privacy Policy

Plugin Version Guard runs entirely on your machine. It reads files and git history in your local repository and sends nothing anywhere. It collects no data, makes no network requests, and stores nothing outside your repository.

## Development

```bash
python3 -m unittest discover -s tests -v
claude plugin validate --strict .
```

## License

MIT

## Disclaimer

Plugin Version Guard is an independent community project. It is not made, endorsed, or supported by Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic, used here only to describe compatibility.
