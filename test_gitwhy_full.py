#!/usr/bin/env python3
"""
test_gitwhy_full.py — comprehensive, git-based test suite for gitwhy.

Covers every behavior documented in README.md: the archiver, both agent
parsers (Claude Code + Codex), noise/CLI-prompt filtering, dedupe-by-
fingerprint, the three-signal commit linker, the three output artifacts
(gitwhy-report.html / GITWHY.md / gitwhy.json) and their exact shapes, and
the CLI entry points (`archive`, `<path>`, `--demo`).

Stdlib only (unittest), matching gitwhy's own "no dependencies" ethos.
Everything runs inside tempfile sandboxes — your real ~/.claude and
~/.codex are never read or written.

Run:
    python test_gitwhy_full.py
    python test_gitwhy_full.py -v
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
GITWHY_PY = HERE / "gitwhy.py"


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def load_gitwhy(claude_dir: Path, codex_dir: Path, archive_dir: Path):
    """Fresh module exec so module-level CLAUDE_DIR/CODEX_DIR/ARCHIVE_DIR
    constants pick up the env vars for *this* sandbox."""
    os.environ["CLAUDE_DIR"] = str(claude_dir)
    os.environ["CODEX_DIR"] = str(codex_dir)
    os.environ["GITWHY_ARCHIVE"] = str(archive_dir)
    spec = importlib.util.spec_from_file_location(
        f"gitwhy_under_test_{id(claude_dir)}", GITWHY_PY)
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    return gw


def git(repo: Path, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, env=env)


def init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", ".")
    git(repo, "config", "user.email", "t@t.com")
    git(repo, "config", "user.name", "T")


def commit_all(repo: Path, message: str, when: datetime):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    git(repo, "add", ".")
    r = subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], env=env,
                        capture_output=True, text=True)
    return r


# -------------------- Claude Code JSONL builders --------------------

def c_user(t, text, cwd=None, branch=None, is_meta=False):
    rec = {"type": "user", "timestamp": ts(t),
           "message": {"role": "user", "content": text}}
    if cwd: rec["cwd"] = str(cwd)
    if branch: rec["gitBranch"] = branch
    if is_meta: rec["isMeta"] = True
    return rec


def c_assistant_text(t, text, cwd=None):
    rec = {"type": "assistant", "timestamp": ts(t),
           "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if cwd: rec["cwd"] = str(cwd)
    return rec


def c_assistant_tool(t, tool_name, tool_input, extra_text=None, cwd=None):
    content = []
    if extra_text:
        content.append({"type": "text", "text": extra_text})
    content.append({"type": "tool_use", "id": "tid", "name": tool_name, "input": tool_input})
    rec = {"type": "assistant", "timestamp": ts(t),
           "message": {"role": "assistant", "content": content}}
    if cwd: rec["cwd"] = str(cwd)
    return rec


def write_claude_session(path: Path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


# -------------------- Codex JSONL builders --------------------

def x_meta(t, cwd, branch="main"):
    return {"timestamp": ts(t), "type": "session_meta",
            "payload": {"cwd": str(cwd), "git": {"branch": branch}, "cli_version": "0.140"}}


def x_message(t, role, text):
    key = "input_text" if role == "user" else "output_text"
    return {"timestamp": ts(t), "type": "response_item",
            "payload": {"type": "message", "role": role,
                        "content": [{"type": key, "text": text}]}}


def x_apply_patch(t, action, filename, body="+print('hi')"):
    args = json.dumps({"command": ["apply_patch",
        f"*** Begin Patch\n*** {action} File: {filename}\n{body}\n*** End Patch"]})
    return {"timestamp": ts(t), "type": "response_item",
            "payload": {"type": "function_call", "name": "shell", "arguments": args}}


def x_shell_commit(t, message="work"):
    args = json.dumps({"command": ["bash", "-lc", f"git add . && git commit -m '{message}'"]})
    return {"timestamp": ts(t), "type": "response_item",
            "payload": {"type": "function_call", "name": "shell", "arguments": args}}


def write_codex_session(path: Path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


class Sandbox:
    """One isolated CLAUDE_DIR/CODEX_DIR/ARCHIVE/repo tree per test."""
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.claude_dir = self.root / "claude"
        self.codex_dir = self.root / "codex"
        self.archive_dir = self.root / "archive"
        self.cproj = self.claude_dir / "projects" / "x"
        self.cproj.mkdir(parents=True)
        self.xproj = self.codex_dir / "sessions" / "2026" / "07" / "27"
        self.xproj.mkdir(parents=True)
        self.repo = self.root / "proj"
        init_repo(self.repo)

    def gw(self):
        return load_gitwhy(self.claude_dir, self.codex_dir, self.archive_dir)

    def cleanup(self):
        self.tmp.cleanup()


# ===================================================================
# Archiver — README: "Archives your transcripts before the reaper runs"
# ===================================================================
class TestArchive(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_archives_both_agents(self):
        write_claude_session(self.sb.cproj / "a.jsonl", [c_user(datetime.now(timezone.utc), "x" * 30)])
        write_codex_session(self.sb.xproj / "rollout-1.jsonl", [x_meta(datetime.now(timezone.utc), self.sb.repo)])
        gw = self.sb.gw()
        gw.archive()
        archived = list(self.sb.archive_dir.rglob("*.jsonl"))
        self.assertEqual(len(archived), 2)

    def test_rerun_is_idempotent_skips_unchanged(self):
        write_claude_session(self.sb.cproj / "a.jsonl", [c_user(datetime.now(timezone.utc), "x" * 30)])
        gw = self.sb.gw()
        gw.archive()
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            gw.archive()
        self.assertIn("0 archived", buf.getvalue().replace("archived 0", "0 archived"))

    def test_updates_when_source_grew(self):
        p = self.sb.cproj / "a.jsonl"
        write_claude_session(p, [c_user(datetime.now(timezone.utc), "x" * 30)])
        gw = self.sb.gw()
        gw.archive()
        dest = self.sb.archive_dir / "projects" / "x" / "a.jsonl"
        before = dest.stat().st_size
        write_claude_session(p, [c_user(datetime.now(timezone.utc), "x" * 30),
                                  c_user(datetime.now(timezone.utc), "y" * 30)])
        gw.archive()
        self.assertGreater(dest.stat().st_size, before)

    def test_no_transcripts_found_does_not_crash(self):
        gw = self.sb.gw()
        try:
            gw.archive()
        except Exception as e:
            self.fail(f"archive() raised on empty state: {e}")


# ===================================================================
# Claude Code parsing — file extraction, noise/CLI filtering, reasoning
# ===================================================================
class TestClaudeParsing(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_edit_and_write_and_multiedit_and_notebookedit_all_extract_files(self):
        t0 = datetime.now(timezone.utc)
        recs = [c_user(t0, "please refactor the four touched files consistently", cwd=self.sb.repo)]
        for i, (tool, key, fname) in enumerate([
                ("Write", "file_path", "a.py"), ("Edit", "file_path", "b.py"),
                ("MultiEdit", "file_path", "c.py"), ("NotebookEdit", "notebook_path", "d.ipynb")]):
            recs.append(c_assistant_tool(t0 + timedelta(minutes=i + 1), tool,
                                          {key: str(self.sb.repo / fname)}))
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["files"], {str(self.sb.repo / f) for f in ("a.py", "b.py", "c.py", "d.ipynb")})

    def test_noise_filtered_from_prompts(self):
        t0 = datetime.now(timezone.utc)
        noisy = [
            "API Error: 401 unauthorized, please login again to continue",
            "please visit /login to re-authenticate your session token",
            "oauth token refresh failed during the last operation attempt",
            "Traceback (most recent call last): something broke badly",
            "exit code 1 returned from the last shell command invocation",
            "Error: could not resolve the requested module dependency",
            "fatal: not a git repository detected in this working directory",
        ]
        recs = [c_user(t0 + timedelta(seconds=i), text, cwd=self.sb.repo)
                for i, text in enumerate(noisy)]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        # every one of these should have been filtered out -> no prompts, no files -> None
        self.assertIsNone(sess)

    def test_cli_prompts_filtered(self):
        t0 = datetime.now(timezone.utc)
        cli_like = ["/compact", "claude", "claude doctor", "exit", "clear", "help", "y", "yes", "n", "no", "ok"]
        recs = [c_user(t0 + timedelta(seconds=i), text, cwd=self.sb.repo)
                for i, text in enumerate(cli_like)]
        recs.append(c_assistant_tool(t0 + timedelta(minutes=1), "Write",
                                      {"file_path": str(self.sb.repo / "x.py")}))
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["prompts"], [])

    def test_short_prompt_under_20_chars_ignored(self):
        t0 = datetime.now(timezone.utc)
        recs = [c_user(t0, "too short"), c_assistant_tool(t0 + timedelta(minutes=1), "Write",
                        {"file_path": str(self.sb.repo / "x.py")})]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertEqual(sess["prompts"], [])

    def test_ismeta_user_message_not_captured_as_prompt(self):
        t0 = datetime.now(timezone.utc)
        recs = [c_user(t0, "this is a long enough meta message to pass length filter", is_meta=True),
                c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(self.sb.repo / "x.py")})]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertEqual(sess["prompts"], [])

    def test_reasoning_backfilled_onto_story_after_edit(self):
        t0 = datetime.now(timezone.utc)
        recs = [
            c_user(t0, "add input validation to the signup endpoint please", cwd=self.sb.repo),
            c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(self.sb.repo / "signup.py")}),
            c_assistant_text(t0 + timedelta(minutes=2),
                              "Validating email format and rejecting disposable domains up front, "
                              "since bad addresses were silently corrupting the welcome-email queue downstream."),
        ]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertEqual(len(sess["stories"]), 1)
        self.assertIn("disposable domains", sess["stories"][0]["reason"])

    def test_git_commit_bash_detected_inside_session(self):
        t0 = datetime.now(timezone.utc)
        recs = [c_user(t0, "wire up the release commit script for this repo", cwd=self.sb.repo),
                c_assistant_tool(t0 + timedelta(minutes=1), "Bash",
                                 {"command": "git add . && git commit -m 'wip'"})]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertEqual(len(sess["git_commits_in_session"]), 1)

    def test_malformed_json_lines_skipped_without_crash(self):
        p = self.sb.cproj / "s.jsonl"
        good = c_user(datetime.now(timezone.utc), "a perfectly valid and long enough prompt here", cwd=self.sb.repo)
        p.write_text("{not valid json\n" + json.dumps(good) + "\nalso not json {{{\n", encoding="utf-8")
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertIsNotNone(sess)
        self.assertEqual(len(sess["prompts"]), 1)

    def test_empty_or_contentless_session_returns_none(self):
        p = self.sb.cproj / "empty.jsonl"
        p.write_text("", encoding="utf-8")
        gw = self.sb.gw()
        self.assertIsNone(gw.parse_session(p))

    def test_cwd_and_branch_captured(self):
        t0 = datetime.now(timezone.utc)
        recs = [c_user(t0, "capture cwd and branch on this long enough prompt", cwd=self.sb.repo, branch="feature/x")]
        p = self.sb.cproj / "s.jsonl"
        write_claude_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_session(p)
        self.assertEqual(sess["cwd"], str(self.sb.repo))
        self.assertIn("feature/x", sess["branches"])


# ===================================================================
# Codex parsing — apply_patch file extraction, session_meta, filtering
# ===================================================================
class TestCodexParsing(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_apply_patch_extracts_file_for_update_and_add(self):
        t0 = datetime.now(timezone.utc)
        recs = [x_meta(t0, self.sb.repo),
                x_message(t0, "user", "please add the new pricing module for the checkout flow"),
                x_apply_patch(t0 + timedelta(minutes=1), "Add", "pricing.py"),
                x_apply_patch(t0 + timedelta(minutes=2), "Update", "checkout.py")]
        p = self.sb.xproj / "rollout-t.jsonl"
        write_codex_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_codex_session(p)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["files"], {"pricing.py", "checkout.py"})

    def test_git_commit_detected_in_shell_call(self):
        t0 = datetime.now(timezone.utc)
        recs = [x_meta(t0, self.sb.repo),
                x_message(t0, "user", "commit the pricing module once it looks correct"),
                x_shell_commit(t0 + timedelta(minutes=1))]
        p = self.sb.xproj / "rollout-t.jsonl"
        write_codex_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_codex_session(p)
        self.assertEqual(len(sess["git_commits_in_session"]), 1)

    def test_noise_and_cli_prompts_filtered_same_as_claude(self):
        t0 = datetime.now(timezone.utc)
        recs = [x_meta(t0, self.sb.repo),
                x_message(t0, "user", "API Error: 401 OAuth token revoked, please login again now"),
                x_message(t0 + timedelta(seconds=1), "user", "/compact"),
                x_apply_patch(t0 + timedelta(minutes=1), "Add", "z.py")]
        p = self.sb.xproj / "rollout-t.jsonl"
        write_codex_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_codex_session(p)
        self.assertEqual(sess["prompts"], [])

    def test_reasoning_excerpt_captured_for_assistant_message(self):
        t0 = datetime.now(timezone.utc)
        recs = [x_meta(t0, self.sb.repo),
                x_message(t0, "user", "write the chronological evaluator with per-window accuracy"),
                x_message(t0 + timedelta(minutes=1), "assistant",
                          "Windowed accuracy computed strictly in arrival order so drift is visible "
                          "instead of averaged away by a shuffled baseline that hides it."),
                x_apply_patch(t0 + timedelta(minutes=2), "Add", "eval.py")]
        p = self.sb.xproj / "rollout-t.jsonl"
        write_codex_session(p, recs)
        gw = self.sb.gw()
        sess = gw.parse_codex_session(p)
        self.assertTrue(any("arrival order" in (st.get("reason") or "") for st in sess["stories"]))


# ===================================================================
# Format sniffing — parse_any()
# ===================================================================
class TestFormatSniffing(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_rollout_prefixed_filename_forces_codex_parser(self):
        t0 = datetime.now(timezone.utc)
        p = self.sb.xproj / "rollout-anything.jsonl"
        write_codex_session(p, [x_meta(t0, self.sb.repo),
                                 x_message(t0, "user", "a long enough codex prompt for this test case"),
                                 x_apply_patch(t0 + timedelta(minutes=1), "Add", "q.py")])
        gw = self.sb.gw()
        sess = gw.parse_any(p)
        self.assertEqual(sess["agent"], "codex")

    def test_content_sniffing_without_rollout_prefix(self):
        t0 = datetime.now(timezone.utc)
        p = self.sb.xproj / "not-rollout-named.jsonl"
        write_codex_session(p, [x_meta(t0, self.sb.repo),
                                 x_message(t0, "user", "a long enough codex prompt for this test case"),
                                 x_apply_patch(t0 + timedelta(minutes=1), "Add", "q.py")])
        gw = self.sb.gw()
        sess = gw.parse_any(p)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["agent"], "codex")

    def test_sniffing_does_not_leak_file_handle(self):
        import warnings
        t0 = datetime.now(timezone.utc)
        p = self.sb.cproj / "ordinary.jsonl"
        write_claude_session(p, [c_user(t0, "a long enough claude prompt for this test case", cwd=self.sb.repo),
                                  c_assistant_tool(t0 + timedelta(minutes=1), "Write",
                                                    {"file_path": str(self.sb.repo / "q.py")})])
        gw = self.sb.gw()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            gw.parse_any(p)
            import gc
            gc.collect()
        leaks = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(leaks, [], f"parse_any() leaked a file handle: {leaks}")

    def test_default_claude_parser_for_ordinary_jsonl(self):
        t0 = datetime.now(timezone.utc)
        p = self.sb.cproj / "ordinary.jsonl"
        write_claude_session(p, [c_user(t0, "a long enough claude prompt for this test case", cwd=self.sb.repo),
                                  c_assistant_tool(t0 + timedelta(minutes=1), "Write",
                                                    {"file_path": str(self.sb.repo / "q.py")})])
        gw = self.sb.gw()
        sess = gw.parse_any(p)
        self.assertEqual(sess["agent"], "claude")


# ===================================================================
# Dedupe by fingerprint + repo scoping — README: "resumed/duplicated
# transcripts are collapsed by content fingerprint"
# ===================================================================
class TestDedupeAndScoping(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def _session_records(self, t0, n_msgs_extra=0):
        recs = [c_user(t0, "add retry logic with exponential backoff to the client", cwd=self.sb.repo),
                c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(self.sb.repo / "client.py")})]
        for i in range(n_msgs_extra):
            recs.append(c_assistant_text(t0 + timedelta(minutes=2 + i), "x" * 50))
        return recs

    def test_resume_duplicate_collapsed_keeping_richer_copy(self):
        t0 = datetime.now(timezone.utc)
        write_claude_session(self.sb.cproj / "orig.jsonl", self._session_records(t0))
        write_claude_session(self.sb.cproj / "resume-copy.jsonl", self._session_records(t0, n_msgs_extra=3))
        gw = self.sb.gw()
        sessions = gw.find_sessions(self.sb.repo)
        self.assertEqual(len(sessions), 1)
        # the richer copy (more messages) must be the one kept
        self.assertGreaterEqual(sessions[0]["n_msgs"], 5)

    def test_sessions_outside_repo_cwd_excluded(self):
        t0 = datetime.now(timezone.utc)
        other = self.sb.root / "unrelated"
        other.mkdir()
        write_claude_session(self.sb.cproj / "elsewhere.jsonl", [
            c_user(t0, "this session belongs to a totally different project", cwd=other),
            c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(other / "x.py")})])
        gw = self.sb.gw()
        sessions = gw.find_sessions(self.sb.repo)
        self.assertEqual(sessions, [])

    def test_case_and_slash_insensitive_cwd_matching_windows_style(self):
        t0 = datetime.now(timezone.utc)
        weird_case_cwd = str(self.sb.repo).upper().replace("/", "\\") + "\\"
        write_claude_session(self.sb.cproj / "s.jsonl", [
            c_user(t0, "windows style path casing should still match this repo", cwd=weird_case_cwd),
            c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(self.sb.repo / "x.py")})])
        gw = self.sb.gw()
        sessions = gw.find_sessions(self.sb.repo)
        self.assertEqual(len(sessions), 1)


# ===================================================================
# git log parsing + linking — README: "How the linking works"
# ===================================================================
class TestGitAndLinking(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def test_is_git_repo(self):
        gw = self.sb.gw()
        self.assertTrue(gw.is_git_repo(self.sb.repo))
        non_repo = self.sb.root / "no-git"
        non_repo.mkdir()
        self.assertFalse(gw.is_git_repo(non_repo))

    def test_git_commits_parses_hash_author_time_subject_files(self):
        (self.sb.repo / "a.py").write_text("1\n")
        commit_all(self.sb.repo, "Add a.py", datetime.now(timezone.utc))
        gw = self.sb.gw()
        commits = gw.git_commits(self.sb.repo)
        self.assertEqual(len(commits), 1)
        c = commits[0]
        self.assertEqual(c["subject"], "Add a.py")
        self.assertEqual(c["author"], "T")
        self.assertIn("a.py", c["files"])
        self.assertEqual(len(c["hash"]), 40)

    def test_git_commits_on_non_repo_returns_empty_not_crash(self):
        non_repo = self.sb.root / "no-git"
        non_repo.mkdir()
        gw = self.sb.gw()
        commits = gw.git_commits(non_repo)
        self.assertEqual(commits, [])

    def test_link_via_file_overlap_and_time_window(self):
        gw = self.sb.gw()
        t0 = datetime.now(timezone.utc)
        sess = {"id": "s1", "start": t0, "end": t0 + timedelta(minutes=10),
                "files": {str(self.sb.repo / "a.py")}, "git_commits_in_session": []}
        commit = {"hash": "h" * 40, "time": t0 + timedelta(minutes=20), "files": ["a.py"], "subject": "x"}
        links = gw.link([sess], [commit], self.sb.repo)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["score"], 2 * 1 + 3)  # overlap*2 + in_window(3)

    def test_link_strongest_signal_commit_observed_inside_session(self):
        gw = self.sb.gw()
        t0 = datetime.now(timezone.utc)
        commit_time = t0 + timedelta(hours=10)  # far outside the 45-min slack window
        sess = {"id": "s1", "start": t0, "end": t0 + timedelta(minutes=5),
                "files": set(),  # no file overlap at all
                "git_commits_in_session": [(commit_time + timedelta(seconds=30), "git commit")]}
        commit = {"hash": "h" * 40, "time": commit_time, "files": ["unrelated.py"], "subject": "x"}
        links = gw.link([sess], [commit], self.sb.repo)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0]["inside"])
        self.assertEqual(links[0]["score"], 5)

    def test_no_link_when_no_signal_present(self):
        gw = self.sb.gw()
        t0 = datetime.now(timezone.utc)
        sess = {"id": "s1", "start": t0, "end": t0 + timedelta(minutes=5),
                "files": {str(self.sb.repo / "a.py")}, "git_commits_in_session": []}
        commit = {"hash": "h" * 40, "time": t0 + timedelta(days=3), "files": ["unrelated.py"], "subject": "x"}
        links = gw.link([sess], [commit], self.sb.repo)
        self.assertEqual(links, [])

    def test_commit_inside_session_outranks_overlap_only(self):
        gw = self.sb.gw()
        t0 = datetime.now(timezone.utc)
        weak = {"id": "weak", "start": t0, "end": t0 + timedelta(minutes=5),
                "files": {str(self.sb.repo / "a.py")}, "git_commits_in_session": []}
        strong = {"id": "strong", "start": t0, "end": t0 + timedelta(minutes=5),
                  "files": {str(self.sb.repo / "a.py")},
                  "git_commits_in_session": [(t0 + timedelta(minutes=6), "git commit")]}
        commit = {"hash": "h" * 40, "time": t0 + timedelta(minutes=6), "files": ["a.py"], "subject": "x"}
        links = gw.link([weak, strong], [commit], self.sb.repo)
        by_id = {L["session"]: L for L in links}
        self.assertGreater(by_id["strong"]["score"], by_id["weak"]["score"])


# ===================================================================
# End-to-end report + digest outputs — README: "The three outputs"
# ===================================================================
class TestReportAndDigest(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.gw = self.sb.gw()
        t0 = datetime.now(timezone.utc)

        recs = [
            c_assistant_text(t0, "API Error: 401 unauthorized noise line", cwd=self.sb.repo),
            c_user(t0 + timedelta(seconds=1),
                   "add a training loop with checkpointing so runs resume from any epoch",
                   cwd=self.sb.repo, branch="main"),
            c_assistant_tool(t0 + timedelta(minutes=4), "Write", {"file_path": str(self.sb.repo / "train.py")},
                              extra_text="Checkpointing optimizer and scheduler state every epoch so "
                                         "interrupted runs restart bit-identical from the last boundary."),
            c_assistant_tool(t0 + timedelta(minutes=8), "Bash",
                              {"command": "git add . && git commit -m 'Add training loop with checkpointing'"}),
        ]
        write_claude_session(self.sb.cproj / "claude-1.jsonl", recs)

        tx = t0 + timedelta(hours=1)
        xrecs = [x_meta(tx, self.sb.repo),
                 x_message(tx, "user", "write the chronological evaluator with per-window accuracy"),
                 x_message(tx + timedelta(minutes=3), "assistant",
                           "Windowed accuracy computed strictly in arrival order so drift is the headline number."),
                 x_apply_patch(tx + timedelta(minutes=5), "Update", "eval.py"),
                 x_shell_commit(tx + timedelta(minutes=9), "Add chronological evaluator")]
        write_codex_session(self.sb.xproj / "rollout-1.jsonl", xrecs)

        (self.sb.repo / "train.py").write_text("x=1\n")
        commit_all(self.sb.repo, "Add training loop with checkpointing", t0 + timedelta(minutes=8))
        (self.sb.repo / "eval.py").write_text("y=2\n")
        commit_all(self.sb.repo, "Add chronological evaluator", tx + timedelta(minutes=9))

    def tearDown(self):
        self.sb.cleanup()

    def test_report_writes_all_three_artifacts(self):
        self.gw.report(self.sb.repo)
        self.assertTrue((self.sb.repo / "gitwhy-report.html").exists())
        self.assertTrue((self.sb.repo / "GITWHY.md").exists())
        self.assertTrue((self.sb.repo / "gitwhy.json").exists())

    def test_html_report_has_both_agent_badges_and_no_noise(self):
        self.gw.report(self.sb.repo)
        html = (self.sb.repo / "gitwhy-report.html").read_text(encoding="utf-8")
        self.assertIn("codex", html)
        self.assertIn("claude", html)
        self.assertNotIn("401 unauthorized", html)
        self.assertNotIn("__DATA__", html)  # template placeholder must be substituted

    def test_gitwhy_json_matches_documented_shape(self):
        self.gw.report(self.sb.repo)
        data = json.loads((self.sb.repo / "gitwhy.json").read_text(encoding="utf-8"))
        self.assertIn("repo", data)
        self.assertIn("files", data)
        self.assertIn("commits", data)
        self.assertIn("train.py", data["files"])
        entry = data["files"]["train.py"][0]
        self.assertIn("date", entry)
        self.assertIn("agent", entry)
        self.assertIn("prompt", entry)
        # commits keyed by file -> short hash, per README example shape
        self.assertIn("train.py", data["commits"])
        self.assertEqual(len(data["commits"]["train.py"]), 7)

    def test_gitwhy_md_covers_both_files_and_is_token_cheap(self):
        self.gw.report(self.sb.repo)
        md = (self.sb.repo / "GITWHY.md").read_text(encoding="utf-8")
        self.assertIn("## train.py", md)
        self.assertIn("## eval.py", md)
        self.assertNotIn("401", md)
        self.assertLess(len(md), 2000)

    def test_report_without_git_repo_is_session_only(self):
        no_git_dir = self.sb.root / "nogit_proj"
        no_git_dir.mkdir()
        t0 = datetime.now(timezone.utc)
        write_claude_session(self.sb.cproj / "nogit.jsonl", [
            c_user(t0, "a plain session with no git repository behind it at all", cwd=no_git_dir),
            c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(no_git_dir / "x.py")})])
        self.gw.report(no_git_dir)
        data = json.loads((no_git_dir / "gitwhy.json").read_text(encoding="utf-8"))
        self.assertEqual(data["commits"], {})  # no commits possible without git

    def test_report_demo_mode_needs_no_real_data(self):
        empty_sb = Sandbox()
        gw = empty_sb.gw()
        out_dir = empty_sb.root / "demo_out"
        out_dir.mkdir()
        cwd = os.getcwd()
        try:
            os.chdir(out_dir)
            gw.report(Path("."), demo=True)
            self.assertTrue((out_dir / "gitwhy-report.html").exists())
        finally:
            os.chdir(cwd)
            empty_sb.cleanup()

    def test_clip_truncates_at_word_boundary(self):
        short = "short text"
        self.assertEqual(self.gw.clip(short), short)
        long_text = "word " * 200
        clipped = self.gw.clip(long_text, n=50)
        self.assertLessEqual(len(clipped), 53)
        self.assertTrue(clipped.endswith("…"))


# ===================================================================
# CLI entry points — README Quickstart commands, run as real subprocess
# ===================================================================
class TestCLI(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.cleanup()

    def _env(self):
        env = os.environ.copy()
        env["CLAUDE_DIR"] = str(self.sb.claude_dir)
        env["CODEX_DIR"] = str(self.sb.codex_dir)
        env["GITWHY_ARCHIVE"] = str(self.sb.archive_dir)
        return env

    def test_cli_archive_subcommand(self):
        write_claude_session(self.sb.cproj / "a.jsonl",
                              [c_user(datetime.now(timezone.utc), "z" * 30)])
        r = subprocess.run([sys.executable, str(GITWHY_PY), "archive"],
                            capture_output=True, text=True, env=self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(list(self.sb.archive_dir.rglob("*.jsonl")))

    def test_cli_demo_flag_writes_report_without_transcripts(self):
        empty_root = self.sb.root / "cli_demo"
        empty_root.mkdir()
        env = self._env()
        r = subprocess.run([sys.executable, str(GITWHY_PY), "--demo"],
                            capture_output=True, text=True, env=env, cwd=str(empty_root))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((empty_root / "gitwhy-report.html").exists())
        self.assertTrue((empty_root / "GITWHY.md").exists())

    def test_cli_nonexistent_path_errors_cleanly(self):
        r = subprocess.run([sys.executable, str(GITWHY_PY), str(self.sb.root / "does-not-exist")],
                            capture_output=True, text=True, env=self._env())
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", (r.stdout + r.stderr))

    def test_cli_no_args_prints_usage_and_exits_1(self):
        r = subprocess.run([sys.executable, str(GITWHY_PY)],
                            capture_output=True, text=True, env=self._env())
        self.assertEqual(r.returncode, 1)
        self.assertIn("Usage", r.stdout)

    def test_cli_report_for_real_repo(self):
        t0 = datetime.now(timezone.utc)
        write_claude_session(self.sb.cproj / "s.jsonl", [
            c_user(t0, "add a health check endpoint to the api service please", cwd=self.sb.repo),
            c_assistant_tool(t0 + timedelta(minutes=1), "Write", {"file_path": str(self.sb.repo / "health.py")})])
        (self.sb.repo / "health.py").write_text("ok\n")
        commit_all(self.sb.repo, "Add health check endpoint", t0 + timedelta(minutes=2))
        r = subprocess.run([sys.executable, str(GITWHY_PY), str(self.sb.repo)],
                            capture_output=True, text=True, env=self._env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.sb.repo / "gitwhy-report.html").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
