# Changelog

## 0.1.3

- Running the checker by hand now includes uncommitted changes when there are any, so it no longer reports ok for a bump or edit you haven't committed yet. Use `--committed` to check only the last commit.

## 0.1.2

- Fixed a false warning when the bump script, commit and push run in one command.
- Release skill now pushes in a separate command after the bump.

## 0.1.1

- Fixed a false warning when one command commits the version bump and pushes it (`git commit ... && git push`).

## 0.1.0

- Pre-push hook: asks before `git push` when plugin files changed without a version bump.
- Detects plugin.json / marketplace.json version mismatches, version decreases and non-semver versions.
- `check` skill for on-demand audits and `release` skill for bump, sync, validate and tag.
- CI mode (`--ci`) with a GitHub Actions example.
