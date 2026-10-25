---
name: release
description: Release a new version of a Claude Code plugin so users actually receive it. Bumps the semver version in plugin.json, keeps the marketplace.json entry in sync, runs claude plugin validate --strict, and optionally tags the release. Use this whenever the user wants to release, ship, publish, cut a version, bump the version, or fix a stale-version warning from plugin-version-guard, even if they only say "push my plugin changes".
---

# Release a plugin version

## Steps

1. **Find out what changed.** Run the checker so you and the user see the current state:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_versions.py"
   ```

   If the repo has several plugins, release only the ones the user means (by default, the ones reported as stale).

2. **Agree on the bump size** unless the user already said it. Suggest one based on the changed files and commit messages since the base:
   - `patch`: bug fixes, wording fixes in skills, no new behavior
   - `minor`: new skills, commands, agents, hooks or options; backward compatible
   - `major`: removed or renamed skills/commands, changed hook behavior users depend on, breaking config changes

3. **Bump.** Preview with `--dry-run`, then apply:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" <plugin-dir> <patch|minor|major|X.Y.Z> --dry-run
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" <plugin-dir> <patch|minor|major|X.Y.Z>
   ```

   If it reports `no-version`, the plugin updates by commit SHA and needs no bump. Tell the user that, and only re-run with `--add-version` if they want to switch to explicit versions.

4. **Validate.** If the `claude` CLI is available:

   ```bash
   claude plugin validate --strict <plugin-dir>
   ```

   Fix anything it reports before continuing. If the CLI isn't available, say so and continue.

5. **Update the changelog** if the plugin has a CHANGELOG.md: add an entry for the new version summarizing the changes.

6. **Re-run the checker** and confirm it reports no problems.

7. **Commit and push in their own command, after the bump has finished.** Don't chain the bump script and `git push` in one command: the pre-push check runs before a command starts, so it can't see a bump made inside that same command.

8. **Commit, and offer to tag.** Commit the version change with a message like `release: <name> v<version>`. Tagging matters when other plugins declare a version range on this one; offer `claude plugin tag --push` from the plugin directory (it creates a `{name}--v{version}` tag). Ask before pushing anything.

Never change a published plugin's `name` as part of a release. Existing installs are recorded under the old name.
