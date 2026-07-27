#!/usr/bin/env python3
"""
test_gitwhy.py — run this next to gitwhy.py + report_template.html:

    python test_gitwhy.py

Builds a fake Claude Code session AND a fake Codex session (in a temp
sandbox — your real ~/.claude and ~/.codex are never touched), a git repo
with matching commits, runs the full gitwhy pipeline, checks everything,
and leaves a report you can open:  test-workspace/proj/gitwhy-report.html
"""
import json, os, subprocess, sys, importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE / "test-workspace"
PROJ = WS / "proj"

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  ({detail})" if detail and not cond else ""))

def ts(dt): return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def main():
    import shutil, stat
    def onerr(fn, p, exc):  # windows leaves git objects read-only; clear and retry
        try: os.chmod(p, stat.S_IWRITE); fn(p)
        except Exception: pass
    shutil.rmtree(WS, onerror=onerr)
    claude_dir = WS / "claude"; codex_dir = WS / "codex"; arch = WS / "arch"
    cproj = claude_dir / "projects" / "x"; cproj.mkdir(parents=True)
    xdir = codex_dir / "sessions" / "2026" / "07" / "26"; xdir.mkdir(parents=True)
    PROJ.mkdir(parents=True)
    os.environ["CLAUDE_DIR"] = str(claude_dir)
    os.environ["CODEX_DIR"] = str(codex_dir)
    os.environ["GITWHY_ARCHIVE"] = str(arch)

    # git repo with two commits
    def git(*a, **kw): return subprocess.run(["git", "-C", str(PROJ), *a],
        capture_output=True, text=True, **kw)
    git("init", "-q", "."); git("config", "user.email", "t@t.com"); git("config", "user.name", "T")
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)

    # ---- Claude Code session: prompt -> edit train.py -> commit; includes noise ----
    t = t0
    lines = [
        {"type": "assistant", "uuid": "n0", "timestamp": ts(t), "cwd": str(PROJ),
         "message": {"role": "assistant", "content": [{"type": "text",
            "text": "API Error: 401 OAuth access token has been revoked — noise that must be filtered."}]}},
        {"type": "user", "uuid": "u0", "timestamp": ts(t), "cwd": str(PROJ),
         "message": {"role": "user", "content": "claude doctor"}},
        {"type": "user", "uuid": "u1", "timestamp": ts(t), "cwd": str(PROJ), "gitBranch": "main",
         "message": {"role": "user", "content": "add a training loop with checkpointing so runs resume from any epoch"}},
        {"type": "assistant", "uuid": "a1", "timestamp": ts(t + timedelta(minutes=4)), "cwd": str(PROJ),
         "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Adding a seeded training loop that checkpoints optimizer and scheduler state every epoch, so interrupted runs restart bit-identical from the last boundary."},
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": str(PROJ / "train.py")}}]}},
        {"type": "assistant", "uuid": "a2", "timestamp": ts(t + timedelta(minutes=8)),
         "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t2", "name": "Bash",
             "input": {"command": "git add . && git commit -m 'Add training loop with checkpointing'"}}]}},
    ]
    (cproj / "claude-abc.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    # duplicate copy (resume artifact) — must be deduped
    (cproj / "claude-abc-copy.jsonl").write_text("\n".join(json.dumps(x) for x in lines[:4]), encoding="utf-8")
    (PROJ / "train.py").write_text("x=1\n")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = (t + timedelta(minutes=8)).isoformat()
    git("add", "."); subprocess.run(["git", "-C", str(PROJ), "commit", "-qm",
        "Add training loop with checkpointing"], env=env)

    # ---- Codex session: session_meta + message + apply_patch + commit ----
    t = t0 + timedelta(hours=1)
    patch_args = json.dumps({"command": ["apply_patch",
        "*** Begin Patch\n*** Update File: eval.py\n+print('hi')\n*** End Patch"]})
    xlines = [
        {"timestamp": ts(t), "type": "session_meta",
         "payload": {"cwd": str(PROJ), "git": {"branch": "main"}, "cli_version": "0.140"}},
        {"timestamp": ts(t), "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text",
                                  "text": "write the chronological evaluator with per-window accuracy so drift is visible"}]}},
        {"timestamp": ts(t + timedelta(minutes=3)), "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "Windowed accuracy computed strictly in arrival order; the shuffled baseline flattens to the mean, which makes the drift the headline number rather than a footnote."}]}},
        {"timestamp": ts(t + timedelta(minutes=5)), "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "arguments": patch_args}},
        {"timestamp": ts(t + timedelta(minutes=9)), "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": json.dumps({"command": ["bash", "-lc",
                        "git add . && git commit -m 'Add chronological evaluator'"]})}},
    ]
    (xdir / "rollout-2026-07-26T18-00-00-test1.jsonl").write_text(
        "\n".join(json.dumps(x) for x in xlines), encoding="utf-8")
    (PROJ / "eval.py").write_text("y=2\n")
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = (t + timedelta(minutes=9)).isoformat()
    git("add", "."); subprocess.run(["git", "-C", str(PROJ), "commit", "-qm",
        "Add chronological evaluator"], env=env)

    # ---- load gitwhy and run pipeline ----
    spec = importlib.util.spec_from_file_location("gitwhy", HERE / "gitwhy.py")
    gw = importlib.util.module_from_spec(spec); spec.loader.exec_module(gw)

    print("\n== archive ==")
    gw.archive()
    archived = list(arch.rglob("*.jsonl"))
    check("archiver saved transcripts from BOTH agents", len(archived) == 3, f"got {len(archived)}")

    print("\n== parsing & linking ==")
    sessions = gw.find_sessions(PROJ)
    agents = sorted(s.get("agent") for s in sessions)
    check("both sessions found (claude + codex)", agents == ["claude", "codex"], str(agents))
    check("resume duplicate deduped", len(sessions) == 2, f"{len(sessions)} sessions")
    cl = next((s for s in sessions if s["agent"] == "claude"), None)
    cx = next((s for s in sessions if s["agent"] == "codex"), None)
    check("claude: noise (401) filtered from stories/excerpts",
          cl and all("401" not in (st.get("reason") or "") for st in cl["stories"])
             and all("401" not in e[1] for e in cl["excerpts"]))
    check("claude: 'claude doctor' not treated as a prompt",
          cl and all("doctor" not in p[1] for p in cl["prompts"]))
    check("codex: file extracted from apply_patch",
          cx and any("eval.py" in f for f in cx["files"]), str(cx["files"] if cx else None))
    check("codex: prompt captured", cx and len(cx["prompts"]) == 1)
    check("codex: git commit detected inside session",
          cx and len(cx["git_commits_in_session"]) == 1)

    commits = gw.git_commits(PROJ)
    links = gw.link(sessions, commits, PROJ)
    check("git log parsed (2 commits)", len(commits) == 2, str(len(commits)))
    check("both sessions linked to their commits", len(links) >= 2, f"{len(links)} links")
    check("commit-inside-session evidence present", any(l["inside"] for l in links))

    print("\n== report + agent context ==")
    gw.report(PROJ)
    html_out = PROJ / "gitwhy-report.html"
    md_out = PROJ / "GITWHY.md"
    check("HTML report written", html_out.exists())
    check("agent context GITWHY.md written", md_out.exists())
    check("gitwhy.json written", (PROJ / "gitwhy.json").exists())
    if md_out.exists():
        md = md_out.read_text(encoding="utf-8")
        check("GITWHY.md covers both files", "train.py" in md and "eval.py" in md)
        check("GITWHY.md is token-cheap (< 2000 chars here)", len(md) < 2000, f"{len(md)} chars")
    if html_out.exists():
        h = html_out.read_text(encoding="utf-8")
        check("report shows codex badge", "codex" in h)
        check("no noise leaked into report", "401" not in h)

    print(f"\n{'='*46}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED: " + ", ".join(FAIL))
    else:
        print(f"  Open it:  {html_out}")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
