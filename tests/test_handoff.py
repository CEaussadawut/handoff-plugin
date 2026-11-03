"""Tests for handoff/scripts/handoff.py.

Run from the repository root:  python3 -m unittest discover -s tests -v

Each test builds throwaway git repositories and a fake Claude config directory,
so nothing here reads your real ~/.claude or ~/.claude.json.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "handoff" / "scripts" / "handoff.py"
SESSION = "11111111-2222-3333-4444-555555555555"


class HandoffTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-test-"))
        self.home = self.tmp / "home"
        self.cfg = self.home / ".claude"
        (self.cfg / "projects" / "-repo").mkdir(parents=True)
        (self.home / ".claude.json").write_text(json.dumps(
            {"oauthAccount": {"emailAddress": "dev@example.com", "accountUuid": "acct-1"}}))
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "app.py").write_text("print('hi')\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "initial")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run(["git"] + list(args), cwd=cwd or self.repo, env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    def run_script(self, *args, cwd=None, stdin="", env=None):
        e = dict(self.env)
        e.update(env or {})
        out = subprocess.run([sys.executable, str(SCRIPT)] + list(args), cwd=cwd or self.repo,
                             env=e, input=stdin, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stderr, "", "the script must never write to stderr")
        return out.stdout

    def write_handoff(self, slug="fix-tray-crash", session=SESSION, body=None, **kw):
        body = body or (
            "# Handoff — Fix tray crash\n\n## Goal\nStop the crash.\n\n"
            "## Next action\nRun `pytest -k tray` and fix the assertion in `app.py:3`.\n\n"
            "## Files in play\n| File | State |\n|---|---|\n| `app.py` | edited |\n")
        out = self.run_script("write", "--slug", slug, "--session", session, stdin=body, **kw)
        path = Path(out.splitlines()[0].split(": ", 1)[1])
        return path, out

    def meta(self, path):
        text = path.read_text()
        self.assertTrue(text.startswith("---\n"), text[:80])
        block = text[4:text.index("\n---\n", 4)]
        return dict(line.split(": ", 1) for line in block.splitlines())

    def transcript(self, entries, session=SESSION):
        path = self.cfg / "projects" / "-repo" / ("%s.jsonl" % session)
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        return path


class FactsAndAccount(HandoffTestCase):
    def test_facts_report_repo_state_and_account(self):
        (self.repo / "app.py").write_text("print('changed')\n")
        out = self.run_script("facts", "--project", str(self.repo), "--session", SESSION)
        self.assertIn("- Account: dev@example.com", out)
        self.assertIn("main @ ", out)
        self.assertIn("no upstream", out)
        self.assertIn("M app.py", out)
        self.assertIn("not git-ignored", out)

    def test_account_from_another_profile_is_not_trusted(self):
        out = self.run_script("facts", "--session", SESSION, env={"CLAUDE_CODE_ACCOUNT_UUID": "deadbeef-0000"})
        self.assertIn("- Account: account deadbeef", out)
        self.assertNotIn("dev@example.com", out)

    def test_desktop_profile_name_labels_the_account(self):
        out = self.run_script("facts", "--session", SESSION, env={
            "CLAUDE_CODE_ACCOUNT_UUID": "deadbeef-0000",
            "CLAUDE_CODE_EXECPATH": "/home/u/.local/share/claude-desktop-profiles/work/claude-code/2.1/claude"})
        self.assertIn("- Account: work (account deadbeef)", out)

    def test_account_matching_profile_is_used(self):
        out = self.run_script("facts", "--session", SESSION, env={"CLAUDE_CODE_ACCOUNT_UUID": "acct-1"})
        self.assertIn("- Account: dev@example.com", out)

    def test_ignored_folder_is_not_flagged(self):
        (self.repo / ".gitignore").write_text(".handoff/\n")
        out = self.run_script("facts", "--session", SESSION)
        self.assertNotIn("not git-ignored", out)


class WriteListResolve(HandoffTestCase):
    def test_write_generates_metadata(self):
        path, out = self.write_handoff()
        self.assertEqual(path.parent, self.repo / ".handoff")
        self.assertRegex(path.name, r"^\d{4}-\d{2}-\d{2}-\d{4}-fix-tray-crash\.md$")
        m = self.meta(path)
        self.assertEqual(m["status"], "open")
        self.assertEqual(m["session"], SESSION)
        self.assertEqual(m["branch"], "main")
        self.assertEqual(m["commit"], self.git("rev-parse", "--short", "HEAD"))
        self.assertEqual(m["from"], "dev@example.com")
        self.assertIn("Next action: Run `pytest -k tray`", out)
        self.assertIn("Only on this machine: branch main has no upstream", out)

    def test_write_never_overwrites(self):
        first, _ = self.write_handoff()
        second, _ = self.write_handoff()
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_explicit_from_wins(self):
        path, _ = self.write_handoff(env={"CLAUDE_CODE_ACCOUNT_UUID": "other"})
        self.assertEqual(self.meta(path)["from"], "account other")
        path, _ = self.run_and_path("--from", "me@corp.test")
        self.assertEqual(self.meta(path)["from"], "me@corp.test")

    def run_and_path(self, *extra):
        out = self.run_script("write", "--slug", "x", "--session", SESSION, *extra, stdin="# Handoff — X\n")
        return Path(out.splitlines()[0].split(": ", 1)[1]), out

    def test_resolve_reports_drift(self):
        path, _ = self.write_handoff()
        time.sleep(0.01)
        (self.repo / "app.py").write_text("print('later')\n")
        os.utime(self.repo / "app.py", (time.time() + 120, time.time() + 120))
        self.git("commit", "-qam", "work after the handoff")
        out = self.run_script("resolve")
        self.assertIn("File: %s" % path, out)
        self.assertIn("work after the handoff", out)
        self.assertIn("`app.py` touched since", out)
        self.assertIn("## Handoff content", out)
        self.assertIn("Run `pytest -k tray`", out)

    def test_resolve_skips_done_and_takes_newest_open(self):
        older, _ = self.write_handoff(slug="older-task")
        newer, _ = self.write_handoff(slug="newer-task")
        self.run_script("mark", newer.name, "done", "--note", "finished")
        out = self.run_script("resolve")
        self.assertIn("File: %s" % older, out)

    def test_resolve_by_fragment(self):
        self.write_handoff(slug="alpha-task")
        self.write_handoff(slug="beta-task")
        self.assertIn("beta-task", self.run_script("resolve", "beta").splitlines()[2])
        self.assertIn("matches 2 handoffs", self.run_script("resolve", "task"))

    def test_nothing_to_resolve(self):
        self.assertIn("nothing has been cut off", self.run_script("resolve"))

    def test_list_mixes_v1_and_v2(self):
        hdir = self.repo / ".handoff"
        hdir.mkdir()
        (hdir / "2026-01-02-0304-old-style.md").write_text(
            "# Handoff — Old style task\n\n**From** a@b.c · **At** 2026-01-02 03:04 UTC · **Dir** /x · "
            "**Branch** dev\n**Reason** manual switch\n\n## Next action\nDo the old thing.\n\n"
            "## Outcome\nDone.\n")
        self.write_handoff()
        out = self.run_script("list")
        lines = [l for l in out.splitlines() if l.startswith("| ") and "When" not in l]
        self.assertEqual(len(lines), 2)
        self.assertIn("Fix tray crash", lines[0])
        self.assertIn("| open |", lines[0])
        self.assertIn("Old style task", lines[1])
        self.assertIn("| done |", lines[1])
        self.assertIn("Do the old thing.", lines[1])


class MarkAndChain(HandoffTestCase):
    def test_mark_v1_file_adds_frontmatter(self):
        hdir = self.repo / ".handoff"
        hdir.mkdir()
        f = hdir / "2026-01-02-0304-old-style.md"
        f.write_text("# Handoff — Old\n\n**From** a@b.c · **At** 2026-01-02 03:04 UTC · **Dir** /x · "
                     "**Branch** dev\n\n## Next action\nGo.\n")
        self.run_script("mark", f.name, "picked-up", "--session", "s2", "--by", "me@x.y")
        m = self.meta(f)
        self.assertEqual((m["status"], m["from"], m["branch"]), ("picked-up", "a@b.c", "dev"))
        self.assertEqual((m["picked_up_by"], m["picked_up_session"]), ("me@x.y", "s2"))
        self.assertIn("**From** a@b.c", f.read_text(), "the original header stays")

    def test_done_note_from_stdin(self):
        path, _ = self.write_handoff()
        self.run_script("mark", path.name, "done", "--note", "-", stdin="Shipped in abc123.\nTests pass.")
        text = path.read_text()
        self.assertEqual(self.meta(path)["status"], "done")
        self.assertIn("## Outcome", text)
        self.assertIn("Shipped in abc123.", text)

    def test_cutoff_after_pickup_links_and_supersedes(self):
        first, _ = self.write_handoff(slug="first", session="writer-session")
        self.run_script("mark", first.name, "picked-up", "--session", SESSION)
        second, out = self.write_handoff(slug="second", session=SESSION)
        self.assertEqual(self.meta(second)["continues"], first.name)
        self.assertEqual(self.meta(first)["status"], "superseded")
        self.assertEqual(self.meta(first)["continued_in"], second.name)
        self.assertIn("continued in `%s`" % second.name, first.read_text())
        self.assertIn("now marked superseded", out)


class Worktrees(HandoffTestCase):
    def test_worktree_sessions_share_the_main_checkout_folder(self):
        wt = self.tmp / "wt"
        self.git("worktree", "add", "-q", "-b", "feature", str(wt))
        out = self.run_script("write", "--slug", "from-worktree", "--session", SESSION,
                              cwd=wt, stdin="# Handoff — WT\n\n## Next action\nGo.\n")
        path = Path(out.splitlines()[0].split(": ", 1)[1])
        self.assertEqual(path.parent, self.repo / ".handoff")
        self.assertEqual(self.meta(path)["branch"], "feature")
        self.assertEqual(Path(self.meta(path)["dir"]).resolve(), wt.resolve())
        resolved = self.run_script("resolve")
        self.assertIn("Different directory than the handoff's", resolved)


def user(text, **kw):
    e = {"type": "user", "message": {"role": "user", "content": text}}
    e.update(kw)
    return e


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


class Hooks(HandoffTestCase):
    def hook(self, name, payload, env=None):
        e = {"CLAUDE_PROJECT_DIR": str(self.repo)}
        e.update(env or {})
        return self.run_script(name, stdin=json.dumps(payload), env=e)

    def busy_transcript(self):
        return self.transcript([
            {"type": "custom-title", "customTitle": "Tray crash fix"},
            user("The tray icon crashes after resume, fix it. My token is sk-ant-abcdefghijklmnopqrstuvwxyz"),
            assistant({"type": "text", "text": "Looking at tray.py."},
                      {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "pytest -k tray"}}),
            user([{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                   "content": "FAILED test_tray.py::test_resume - AssertionError"}]),
            assistant({"type": "tool_use", "id": "t2", "name": "Edit", "input": {"file_path": "/r/tray.py"}},
                      {"type": "tool_use", "id": "t3", "name": "TodoWrite", "input": {"todos": [
                          {"content": "Patch resume handler", "status": "completed"},
                          {"content": "Add regression test", "status": "pending"}]}}),
            user("<system-reminder>noise</system-reminder>now add the regression test"),
            user("skill body", isMeta=True),
            assistant({"type": "text", "text": "Adding the regression test in test_tray.py next."}),
        ])

    def test_stop_failure_writes_a_redacted_snapshot(self):
        t = self.busy_transcript()
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit", "last_assistant_message": "API Error: Rate limit reached"})
        files = list((self.repo / ".handoff").glob("*-auto-11111111.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text()
        m = self.meta(files[0])
        self.assertEqual(m["status"], "auto")
        self.assertIn("rate_limit", m["error"])
        self.assertIn("# Handoff — Tray crash fix (auto-saved)", text)
        self.assertIn("now add the regression test", text)
        self.assertNotIn("noise", text)
        self.assertNotIn("skill body", text)
        self.assertNotIn("sk-ant-abc", text)
        self.assertIn("[redacted]", text)
        self.assertIn("- [x] Patch resume handler", text)
        self.assertIn("`/r/tray.py`", text)
        self.assertIn("AssertionError", text)
        self.assertIn("continue the user's last request: now add the regression test", text)

        # A second failure in the same session updates the same file.
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit"})
        self.assertEqual(len(list((self.repo / ".handoff").glob("*.md"))), 1)

    def test_stop_failure_ignores_a_session_with_no_work(self):
        t = self.transcript([user("hello")])
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit"})
        self.assertFalse((self.repo / ".handoff").exists())

    def test_stop_failure_defers_to_a_fresh_cutoff(self):
        self.write_handoff(session=SESSION)
        t = self.busy_transcript()
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit"})
        self.assertEqual(len(list((self.repo / ".handoff").glob("*-auto-*.md"))), 0)

    def test_hooks_can_be_switched_off(self):
        t = self.busy_transcript()
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit"}, env={"HANDOFF_HOOKS": "0"})
        self.assertFalse((self.repo / ".handoff").exists())
        self.write_handoff()
        self.assertEqual(self.hook("hook-session-start", {"source": "startup"}, env={"HANDOFF_HOOKS": "off"}), "")

    def test_session_start_points_at_the_open_handoff(self):
        self.assertEqual(self.hook("hook-session-start", {"source": "startup", "cwd": str(self.repo)}), "")
        path, _ = self.write_handoff()
        out = json.loads(self.hook("hook-session-start", {"source": "startup", "cwd": str(self.repo)}))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(path.name, ctx)
        self.assertIn("Fix tray crash", ctx)
        self.assertIn("/pickup", out["systemMessage"])
        self.run_script("mark", path.name, "done", "--note", "ok")
        self.assertEqual(self.hook("hook-session-start", {"source": "startup", "cwd": str(self.repo)}), "")

    def test_stale_snapshot_is_not_advertised(self):
        t = self.busy_transcript()
        self.hook("hook-stop-failure", {"session_id": SESSION, "transcript_path": str(t), "cwd": str(self.repo),
                                        "error": "rate_limit"})
        self.assertIn("auto-saved", self.run_script("list"))
        later = time.time() + 3600
        os.utime(t, (later, later))  # the session carried on after the limit reset
        self.assertIn("stale", self.run_script("list"))
        self.assertEqual(self.hook("hook-session-start", {"source": "startup", "cwd": str(self.repo)}), "")

    def test_hooks_survive_garbage_input(self):
        self.assertEqual(self.run_script("hook-session-start", stdin="not json"), "")
        self.assertEqual(self.run_script("hook-stop-failure", stdin="[1, 2]"), "")


if __name__ == "__main__":
    unittest.main()
