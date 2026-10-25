---
name: check
description: Audit Claude Code plugin versions in the current repository before a release. Finds plugins whose files changed without a version bump (so users would never receive the update), plugin.json vs marketplace.json version mismatches, version decreases and non-semver versions. Use this whenever the user is about to push, release, publish or submit a Claude Code plugin or marketplace, asks why users aren't getting their plugin update, sees "already at the latest version" after pushing changes, or asks whether their plugin version is right.
---

# Check plugin versions

Claude Code only updates an installed plugin when its version string changes. The version comes from `plugin.json`, then from the plugin's `marketplace.json` entry, and only if neither sets one, from the git commit SHA. A plugin that declares a version and gets new commits without a bump looks "already at the latest version" to every existing user.

## Steps

1. Run the checker from the repository (any subdirectory works):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_versions.py"
   ```

   Add `--base <ref>` if the user wants to compare against something other than the upstream branch (for example the last release tag: `--base v1.2.0`). Use `--json` if you need to process the result.

2. Explain the result in plain language, plugin by plugin:
   - **stale-version**: name the changed files and say clearly that existing users won't receive them until the version changes.
   - **version-mismatch**: say which file Claude Code actually uses (plugin.json) and that the marketplace listing is misleading.
   - **version-decrease** / **invalid-version**: say what to change.
   - **version none (updates by commit SHA)**: this is valid, not a problem. Every pushed commit reaches users. Mention it only so the user knows which model they're on.
   - If no base ref was found (new repo, no upstream), say that only consistency was checked.

3. If there are problems, offer to fix them with the release skill (`/plugin-version-guard:release`). Don't edit versions without the user agreeing on the bump size.

By default, changes to README.md, CHANGELOG.md, LICENSE, tests/, .github/ and examples/ inside a plugin don't require a bump. Pass `--no-default-ignores` to count them, or `--ignore '<glob>'` to skip more paths.
