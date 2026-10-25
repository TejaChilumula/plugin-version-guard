"""Run with:  python3 -m unittest discover -s tests -v"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import check_versions as cv  # noqa: E402


def sh(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content, indent=2))


class Repo:
    """A working repo with a bare 'origin' remote and an upstream branch."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = self.tmp.name
        self.remote = os.path.join(base, "remote.git")
        self.path = os.path.join(base, "work")
        sh(["git", "init", "--bare", "-b", "main", self.remote], base)
        sh(["git", "init", "-b", "main", self.path], base)
        for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
            sh(["git", "config", k, v], self.path)
        sh(["git", "remote", "add", "origin", self.remote], self.path)

    def file(self, rel, content):
        write(os.path.join(self.path, rel), content)

    def commit(self, msg="c"):
        sh(["git", "add", "-A"], self.path)
        sh(["git", "commit", "-m", msg], self.path)

    def push(self):
        sh(["git", "push", "-u", "origin", "main"], self.path)

    def close(self):
        self.tmp.cleanup()


def manifest(name="demo", version="1.0.0"):
    m = {"name": name, "description": "d"}
    if version is not None:
        m["version"] = version
    return m


class CheckVersionsTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo()

    def tearDown(self):
        self.r.close()

    def kinds(self, report):
        return sorted(i["kind"] for i in report["issues"])

    # --- the core problem -------------------------------------------------

    def test_stale_version_detected(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("skills/a/SKILL.md", "v1")
        self.r.commit(); self.r.push()
        self.r.file("skills/a/SKILL.md", "v2 with a fix")
        self.r.commit()
        rep = cv.analyze(self.r.path)
        self.assertEqual(self.kinds(rep), ["stale-version"])
        self.assertIn("skills/a/SKILL.md", rep["issues"][0]["message"])

    def test_bumped_version_is_ok(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("skills/a/SKILL.md", "v1")
        self.r.commit(); self.r.push()
        self.r.file("skills/a/SKILL.md", "v2")
        self.r.file(".claude-plugin/plugin.json", manifest(version="1.0.1"))
        self.r.commit()
        rep = cv.analyze(self.r.path)
        self.assertTrue(rep["ok"], rep["issues"])

    def test_no_version_uses_commit_sha_and_is_ok(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version=None))
        self.r.file("skills/a/SKILL.md", "v1")
        self.r.commit(); self.r.push()
        self.r.file("skills/a/SKILL.md", "v2")
        self.r.commit()
        rep = cv.analyze(self.r.path)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["plugins"][0]["version_source"], "commit SHA")

    def test_docs_only_change_needs_no_bump(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("README.md", "a")
        self.r.commit(); self.r.push()
        self.r.file("README.md", "b")
        self.r.file("tests/test_x.py", "pass")
        self.r.commit()
        self.assertTrue(cv.analyze(self.r.path)["ok"])
        # ...unless default ignores are turned off
        self.assertEqual(self.kinds(cv.analyze(self.r.path, ignores=[])), ["stale-version"])

    def test_skill_markdown_is_not_ignored(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("skills/a/SKILL.md", "a")
        self.r.commit(); self.r.push()
        self.r.file("skills/a/SKILL.md", "b")
        self.r.commit()
        self.assertEqual(self.kinds(cv.analyze(self.r.path)), ["stale-version"])

    # --- marketplace interplay -------------------------------------------

    def test_marketplace_version_is_used_when_plugin_json_has_none(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version=None))
        self.r.file(".claude-plugin/marketplace.json",
                    {"name": "m", "owner": {"name": "o"},
                     "plugins": [{"name": "demo", "source": "./", "version": "2.0.0"}]})
        self.r.file("hooks/hooks.json", "{}")
        self.r.commit(); self.r.push()
        self.r.file("hooks/hooks.json", '{"hooks": {}}')
        self.r.commit()
        rep = cv.analyze(self.r.path)
        self.assertEqual(self.kinds(rep), ["stale-version"])
        self.assertEqual(rep["plugins"][0]["version_source"], "marketplace.json")

    def test_version_mismatch(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version="1.2.0"))
        self.r.file(".claude-plugin/marketplace.json",
                    {"name": "m", "owner": {"name": "o"},
                     "plugins": [{"name": "demo", "source": "./", "version": "1.1.0"}]})
        self.r.commit()
        self.assertEqual(self.kinds(cv.analyze(self.r.path)), ["version-mismatch"])

    def test_multi_plugin_marketplace(self):
        self.r.file(".claude-plugin/marketplace.json",
                    {"name": "m", "owner": {"name": "o"},
                     "plugins": [{"name": "a", "source": "./plugins/a"},
                                 {"name": "b", "source": "./plugins/b"}]})
        self.r.file("plugins/a/.claude-plugin/plugin.json", manifest("a"))
        self.r.file("plugins/a/commands/x.md", "1")
        self.r.file("plugins/b/.claude-plugin/plugin.json", manifest("b"))
        self.r.file("plugins/b/commands/y.md", "1")
        self.r.commit(); self.r.push()
        self.r.file("plugins/a/commands/x.md", "2")        # a changed, not bumped
        self.r.file("plugins/b/commands/y.md", "2")        # b changed and bumped
        self.r.file("plugins/b/.claude-plugin/plugin.json", manifest("b", "1.1.0"))
        self.r.commit()
        rep = cv.analyze(self.r.path)
        self.assertEqual([(i["kind"], i["plugin"]) for i in rep["issues"]], [("stale-version", "a")])

    # --- other checks ----------------------------------------------------

    def test_version_decrease(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version="1.5.0"))
        self.r.commit(); self.r.push()
        self.r.file(".claude-plugin/plugin.json", manifest(version="1.4.0"))
        self.r.commit()
        self.assertEqual(self.kinds(cv.analyze(self.r.path)), ["version-decrease"])

    def test_invalid_version(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version="latest"))
        self.r.commit()
        self.assertEqual(self.kinds(cv.analyze(self.r.path)), ["invalid-version"])

    def test_new_plugin_without_upstream_is_ok(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.commit()  # never pushed: no base ref
        rep = cv.analyze(self.r.path)
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["base_found"])

    def test_not_a_plugin_repo_is_skipped(self):
        self.r.file("src/app.py", "print(1)")
        self.r.commit()
        self.assertIn("skipped", cv.analyze(self.r.path))

    # --- hook mode -------------------------------------------------------

    def run_hook(self, command):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.r.path}
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(buf):
            code = cv.run_hook()
        self.assertEqual(code, 0)
        out = buf.getvalue().strip()
        return json.loads(out) if out else None

    def _make_stale(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("agents/x.md", "1")
        self.r.commit(); self.r.push()
        self.r.file("agents/x.md", "2")
        self.r.commit()

    def test_hook_asks_on_git_push_when_stale(self):
        self._make_stale()
        out = self.run_hook("git push origin main")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("stale-version", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_hook_handles_chained_and_dash_C(self):
        self._make_stale()
        self.assertIsNotNone(self.run_hook("git add -A && git commit -m x && git push"))
        parent = os.path.dirname(self.r.path)
        payload_cmd = f"git -C {os.path.basename(self.r.path)} push"
        payload = {"tool_name": "Bash", "tool_input": {"command": payload_cmd}, "cwd": parent}
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(buf):
            cv.run_hook()
        self.assertIn("ask", buf.getvalue())

    def test_hook_ignores_other_commands(self):
        self._make_stale()
        self.assertIsNone(self.run_hook("git status"))
        self.assertIsNone(self.run_hook("npm test"))
        self.assertIsNone(self.run_hook("git pushd-is-not-a-thing"))

    def test_hook_silent_when_ok(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.commit(); self.r.push()
        self.assertIsNone(self.run_hook("git push"))

    def test_hook_never_crashes_on_bad_input(self):
        buf = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("not json")), redirect_stdout(buf):
            self.assertEqual(cv.run_hook(), 0)
        self.assertEqual(buf.getvalue(), "")

    # --- ci mode ---------------------------------------------------------

    def test_ci_exit_code(self):
        self._make_stale()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cv.main([self.r.path, "--ci"]), 1)
            self.assertEqual(cv.main([self.r.path]), 0)


class BumpVersionTest(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.script = os.path.join(SCRIPTS, "bump_version.py")

    def tearDown(self):
        self.r.close()

    def bump(self, *args):
        out = subprocess.run([sys.executable, self.script, *args], cwd=self.r.path,
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def read(self, rel):
        with open(os.path.join(self.r.path, rel)) as f:
            return json.load(f)

    def test_bump_patch_and_sync_marketplace(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version="1.2.3"))
        self.r.file(".claude-plugin/marketplace.json",
                    {"name": "m", "owner": {"name": "o"},
                     "plugins": [{"name": "demo", "source": "./", "version": "1.2.3"}]})
        self.r.commit()
        res = self.bump(".", "minor")
        self.assertEqual((res["from"], res["to"]), ("1.2.3", "1.3.0"))
        self.assertEqual(self.read(".claude-plugin/plugin.json")["version"], "1.3.0")
        self.assertEqual(self.read(".claude-plugin/marketplace.json")["plugins"][0]["version"], "1.3.0")

    def test_does_not_add_version_to_sha_plugin_by_default(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version=None))
        self.r.commit()
        res = self.bump(".", "patch")
        self.assertFalse(res["changed"])
        self.assertNotIn("version", self.read(".claude-plugin/plugin.json"))

    def test_dry_run_changes_nothing(self):
        self.r.file(".claude-plugin/plugin.json", manifest(version="0.9.0"))
        self.r.commit()
        res = self.bump(".", "major", "--dry-run")
        self.assertEqual(res["to"], "1.0.0")
        self.assertEqual(self.read(".claude-plugin/plugin.json")["version"], "0.9.0")

    def test_fixes_stale_report_end_to_end(self):
        self.r.file(".claude-plugin/plugin.json", manifest())
        self.r.file("skills/s/SKILL.md", "1")
        self.r.commit(); self.r.push()
        self.r.file("skills/s/SKILL.md", "2")
        self.r.commit()
        self.assertFalse(cv.analyze(self.r.path)["ok"])
        self.bump(".", "patch")
        self.r.commit("release")
        self.assertTrue(cv.analyze(self.r.path)["ok"])


if __name__ == "__main__":
    unittest.main()
