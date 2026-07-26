#!/usr/bin/env python3
"""
gitwhy - v0.2
git blame tells you WHO wrote the line. This tells you WHY.

Links Claude Code sessions to the git commits and files they produced,
renders a local provenance report, and archives transcripts before
Claude Code's 30-day cleanup deletes them.

Usage:
    python gitwhy.py archive                  # save ALL transcripts to ~/.gitwhy/archive
    python gitwhy.py <path-to-project>        # provenance report (git optional)
    python gitwhy.py --demo                   # synthetic data, preview the UI

Output: gitwhy-report.html. Stdlib only. Nothing leaves your machine.
"""
import json, os, re, shutil, subprocess, sys, html
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # Windows consoles/CI pipes may default to cp1252; our output is UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NOISE = re.compile(r"api error|/login|oauth|traceback|exit code \d|^error\b|fatal:", re.I)
CLI_PROMPT = re.compile(r"^(/\S+|claude(\s+\S+)?|exit|clear|help|y|yes|n|no|ok)\s*$", re.I)
SLACK_MIN = 45
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
CODEX_DIR = Path(os.environ.get("CODEX_DIR", Path.home() / ".codex"))
PATCH_FILE = re.compile(r"\*\*\*\s+(?:Update|Add)\s+File:\s*([^\n\\\"]+)")
ARCHIVE_DIR = Path(os.environ.get("GITWHY_ARCHIVE", Path.home() / ".gitwhy" / "archive"))

# ------------------------- session parsing -------------------------

def iso(ts):
    if not ts: return None
    try: return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception: return None

def parse_session(path: Path):
    """Defensively parse one session JSONL. Every field is optional."""
    s = {"id": path.stem, "path": str(path), "cwd": None, "branches": set(),
         "start": None, "end": None, "prompts": [], "files": set(),
         "git_commits_in_session": [], "n_msgs": 0, "excerpts": [], "stories": [],
         "agent": "claude"}
    last_prompt, last_reason, seen = None, None, set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    for line in lines:
        line = line.strip()
        if not line: continue
        try: rec = json.loads(line)
        except Exception: continue
        if not isinstance(rec, dict): continue
        t = iso(rec.get("timestamp"))
        if t:
            s["start"] = min(s["start"], t) if s["start"] else t
            s["end"] = max(s["end"], t) if s["end"] else t
        if rec.get("cwd"): s["cwd"] = rec["cwd"]
        if rec.get("gitBranch"): s["branches"].add(rec["gitBranch"])
        msg = rec.get("message") or {}
        role, content = msg.get("role"), msg.get("content")
        if rec.get("type") in ("user", "assistant"): s["n_msgs"] += 1
        if role == "user" and not rec.get("isMeta"):
            texts = []
            if isinstance(content, str) and content.strip():
                texts = [content.strip()]
            elif isinstance(content, list):
                texts = [b["text"].strip() for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()]
            for txt in texts:
                if NOISE.search(txt[:120]) or CLI_PROMPT.match(txt) or len(txt) < 20: continue
                s["prompts"].append((t, txt))
                last_prompt, last_reason = txt, None
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict): continue
                if b.get("type") == "tool_use":
                    name, inp = b.get("name", ""), b.get("input") or {}
                    if name in EDIT_TOOLS and isinstance(inp, dict):
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp:
                            s["files"].add(fp)
                            key = (fp, last_prompt)
                            if last_prompt and key not in seen:
                                seen.add(key)
                                s["stories"].append({"file": fp, "t": t,
                                                     "prompt": last_prompt,
                                                     "reason": last_reason})
                    if name == "Bash" and isinstance(inp, dict):
                        cmd = inp.get("command", "")
                        if re.search(r"\bgit\b.*\bcommit\b", cmd):
                            s["git_commits_in_session"].append((t, cmd))
                elif b.get("type") == "text" and role == "assistant":
                    txt = b.get("text", "").strip()
                    if 40 < len(txt) < 700 and not NOISE.search(txt):
                        last_reason = txt
                        if len(s["excerpts"]) < 12: s["excerpts"].append((t, txt))
                        # backfill reasoning for stories under the current prompt
                        for st in s["stories"]:
                            if st["prompt"] == last_prompt and not st["reason"]:
                                st["reason"] = txt
    if not s["start"] or (not s["files"] and not s["prompts"]):
        return None
    return s

def parse_codex_session(path: Path):
    """OpenAI Codex CLI rollout-*.jsonl -> same session shape. Defensive."""
    s = {"id": path.stem.replace("rollout-", "")[:24], "path": str(path), "cwd": None,
         "branches": set(), "start": None, "end": None, "prompts": [], "files": set(),
         "git_commits_in_session": [], "n_msgs": 0, "excerpts": [], "stories": [],
         "agent": "codex"}
    last_prompt, last_reason, seen = None, None, set()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None
    def note_file(fp, t):
        s["files"].add(fp)
        key = (fp, last_prompt)
        if last_prompt and key not in seen:
            seen.add(key)
            s["stories"].append({"file": fp, "t": t, "prompt": last_prompt, "reason": last_reason})
    for line in lines:
        line = line.strip()
        if not line: continue
        try: rec = json.loads(line)
        except Exception: continue
        if not isinstance(rec, dict): continue
        t = iso(rec.get("timestamp"))
        if t:
            s["start"] = min(s["start"], t) if s["start"] else t
            s["end"] = max(s["end"], t) if s["end"] else t
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
        rtype = rec.get("type") or p.get("type")
        if rtype == "session_meta":
            s["cwd"] = p.get("cwd") or (p.get("git") or {}).get("cwd") or s["cwd"]
            g = p.get("git") or {}
            if g.get("branch"): s["branches"].add(g["branch"])
            continue
        ptype = p.get("type")
        if ptype == "message":
            s["n_msgs"] += 1
            role = p.get("role")
            texts = []
            c = p.get("content")
            if isinstance(c, str): texts = [c]
            elif isinstance(c, list):
                texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("text")]
            joined = "\n".join(x for x in texts if x).strip()
            if not joined: continue
            if role == "user":
                if NOISE.search(joined[:120]) or CLI_PROMPT.match(joined) or len(joined) < 20:
                    continue
                s["prompts"].append((t, joined))
                last_prompt, last_reason = joined, None
            elif role == "assistant":
                if 40 < len(joined) < 700 and not NOISE.search(joined):
                    last_reason = joined
                    if len(s["excerpts"]) < 12: s["excerpts"].append((t, joined))
                    for st in s["stories"]:
                        if st["prompt"] == last_prompt and not st["reason"]:
                            st["reason"] = joined
        elif ptype in ("function_call", "local_shell_call", "custom_tool_call"):
            args = p.get("arguments") or p.get("input") or ""
            raw = args if isinstance(args, str) else json.dumps(args)
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
                cmd = parsed.get("command") if isinstance(parsed, dict) else None
                if isinstance(cmd, list): raw = " ".join(str(x) for x in cmd)
                elif isinstance(cmd, str): raw = cmd
            except Exception:
                pass
            for m in PATCH_FILE.finditer(raw):
                note_file(m.group(1).strip(), t)
            if re.search(r"\bgit\b.*\bcommit\b", raw):
                s["git_commits_in_session"].append((t, raw[:200]))
    if not s["start"] or (not s["files"] and not s["prompts"]):
        return None
    return s

def parse_any(path: Path):
    """Sniff format: Codex rollouts are named rollout-* or open with session_meta."""
    if path.name.startswith("rollout-"):
        return parse_codex_session(path)
    try:
        head = path.open(encoding="utf-8", errors="replace").readline()
    except Exception:
        return None
    if '"session_meta"' in head or '"response_item"' in head:
        return parse_codex_session(path)
    sess = parse_session(path)
    if sess: sess["agent"] = "claude"
    return sess

def all_transcript_files():
    """Every JSONL Claude Code may have written, live + archived."""
    roots = [CLAUDE_DIR / "projects", CLAUDE_DIR / "sessions", CODEX_DIR / "sessions", ARCHIVE_DIR]
    for root in roots:
        if root.exists():
            yield from root.rglob("*.jsonl")

def norm(p):
    return str(p).replace("\\", "/").rstrip("/").lower()

def fingerprint(sess):
    """Content identity: survives renamed/duplicated transcript files (resume copies)."""
    first = sess["prompts"][0][1][:80] if sess["prompts"] else ""
    start = sess["start"].isoformat()[:16] if sess["start"] else ""
    return (start, first)

def find_sessions(repo: Path):
    repo_n = norm(repo.resolve())
    best = {}   # fingerprint -> session (keep the most complete copy)
    for f in all_transcript_files():
        sess = parse_any(f)
        if not sess: continue
        cwd_n = norm(sess["cwd"]) if sess["cwd"] else ""
        if cwd_n and (cwd_n == repo_n or cwd_n.startswith(repo_n + "/") or repo_n.startswith(cwd_n + "/")):
            key = fingerprint(sess)
            old = best.get(key)
            if not old or sess["n_msgs"] > old["n_msgs"]:
                best[key] = sess
    return list(best.values())

# ------------------------- archiver -------------------------

def archive():
    """Copy every live transcript into ~/.gitwhy/archive before cleanup eats it."""
    saved = skipped = 0
    for root in (CLAUDE_DIR / "projects", CLAUDE_DIR / "sessions", CODEX_DIR / "sessions"):
        if not root.exists(): continue
        for f in root.rglob("*.jsonl"):
            rel = f.relative_to(root)
            dest = ARCHIVE_DIR / root.name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size >= f.stat().st_size:
                skipped += 1; continue
            shutil.copy2(f, dest); saved += 1
    print(f"[✓] archived {saved} transcript(s), {skipped} already safe -> {ARCHIVE_DIR}")
    if saved == 0 and skipped == 0:
        print("[!] no transcripts found. Also set \"cleanupPeriodDays\": 3650 in "
              f"{CLAUDE_DIR / 'settings.json'} so future sessions survive.")

# ------------------------- git -------------------------

def is_git_repo(repo: Path):
    return (repo / ".git").exists()

def git_commits(repo: Path):
    fmt = "%H%x1f%an%x1f%aI%x1f%s"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log", f"--pretty=format:{fmt}", "--numstat"],
            capture_output=True, text=True, check=True,
            encoding="utf-8", errors="replace").stdout
    except Exception as e:
        print(f"[!] git log failed: {e}")
        return []
    commits, cur = [], None
    for line in out.splitlines():
        if "\x1f" in line:
            h, an, at, subj = line.split("\x1f", 3)
            cur = {"hash": h, "author": an, "time": iso(at), "subject": subj, "files": []}
            commits.append(cur)
        elif line.strip() and cur:
            parts = line.split("\t")
            if len(parts) == 3: cur["files"].append(parts[2])
    return commits

# ------------------------- linking -------------------------

def relativize(s, repo: Path):
    repo_n = norm(repo.resolve())
    rel = set()
    for f in s["files"]:
        fn = norm(f)
        rel.add(fn[len(repo_n):].lstrip("/") if fn.startswith(repo_n) else fn.lstrip("/"))
    s["files_rel"] = rel

def rel_one(f, s):
    fn = norm(f)
    for r in s.get("files_rel", set()):
        if fn.endswith("/" + r) or fn == r: return r
    return fn.lstrip("/")

def link(sessions, commits, repo: Path):
    links = []
    for s in sessions:
        relativize(s, repo)
        lo = s["start"] - timedelta(minutes=SLACK_MIN)
        hi = s["end"] + timedelta(minutes=SLACK_MIN)
        for c in commits:
            if not c["time"]: continue
            ct = c["time"].astimezone(timezone.utc)
            in_window = lo <= ct <= hi
            overlap = s["files_rel"] & set(c["files"])
            committed_inside = any(
                t and abs((ct - t.astimezone(timezone.utc)).total_seconds()) < 900
                for t, _ in s["git_commits_in_session"])
            score = len(overlap) * 2 + (3 if in_window else 0) + (5 if committed_inside else 0)
            if (in_window and overlap) or committed_inside:
                links.append({"session": s["id"], "commit": c["hash"], "score": score,
                              "overlap": sorted(overlap), "inside": committed_inside})
    return links

# ------------------------- demo data -------------------------

def demo_data():
    now = datetime.now(timezone.utc)
    def S(i, hrs_ago, prompt, files, excerpt, n=34):
        agent = "codex" if i % 2 == 0 else "claude"
        st = now - timedelta(hours=hrs_ago)
        return {"id": f"sess-{i}", "path": "demo", "cwd": "/home/mehrta/keystroke-decoder",
                "branches": {"main"}, "agent": agent,
                "start": st, "end": st + timedelta(minutes=52),
                "n_msgs": n, "prompts": [(st, prompt)], "files": set(files), "files_rel": set(files),
                "git_commits_in_session": [(st + timedelta(minutes=48), "git commit")],
                "excerpts": [(st + timedelta(minutes=20), excerpt)],
                "stories": [{"file": f, "t": st + timedelta(minutes=10), "prompt": prompt, "reason": excerpt} for f in files]}
    sessions = [
        S(1, 76, "the per-user calibration is overfitting — LOSO accuracy drops 14 points on unseen subjects. can we try adversarial training to strip subject identity from the features?",
          ["train_v4.py", "models/dann.py"],
          "The cleanest fix is a DANN-style gradient reversal layer: a subject classifier head whose gradient is reversed into the encoder, so the encoder is explicitly punished for encoding subject identity. I'll add a lambda warmup schedule since flipping it on at full strength destabilises the main task early in training."),
        S(2, 52, "rolling EMA baseline for wrist posture — sessions drift within minutes, the fixed baseline from calibration is stale by minute 3",
          ["features/posture.py", "train_v4.py"],
          "Replacing the static calibration frame with an exponential moving average (half-life ≈ 90 s) of the wrist-relative origin. Chronological evaluation is the honest test here — random splits were hiding the drift entirely."),
        S(3, 29, "add a Weibull-based OOD gate so the decoder abstains instead of guessing on out-of-distribution hand shapes",
          ["models/ood_gate.py", "eval/chrono.py"],
          "Fitting a Weibull to the tail of per-class max activation distances (EVT, OpenMax-style). Below threshold → abstain token rather than a forced key guess. This trades 2% coverage for a 9-point precision gain on the chronological split."),
        S(4, 6, "eval report generator — one HTML page per run with confusion matrix, per-subject LOSO table, drift curves",
          ["eval/report.py", "eval/chrono.py"],
          "Single self-contained HTML per run, embedding the confusion matrix as inline SVG so reports stay diffable in git and viewable without a server."),
    ]
    def C(h, hrs_ago, subj, files):
        return {"hash": h, "author": "Mehrta Eslami", "time": now - timedelta(hours=hrs_ago),
                "subject": subj, "files": files}
    commits = [
        C("a3f9c21", 75.1, "Add DANN subject-adversarial branch with GRL warmup", ["train_v4.py", "models/dann.py"]),
        C("b7d0e55", 51.0, "Replace static posture baseline with rolling EMA (90s half-life)", ["features/posture.py", "train_v4.py"]),
        C("c1a8f02", 28.2, "Weibull OOD gate: abstain on out-of-distribution hand shapes", ["models/ood_gate.py", "eval/chrono.py"]),
        C("d9e4b77", 5.1, "Per-run HTML eval reports with inline-SVG confusion matrix", ["eval/report.py"]),
        C("e2c6a90", 120.0, "Initial chronological eval protocol", ["eval/chrono.py"]),
    ]
    return sessions, commits

# ------------------------- report -------------------------

def clip(txt, n=380):
    txt = (txt or "").strip()
    if len(txt) <= n: return txt
    return txt[:n].rsplit(" ", 1)[0] + " …"

def render(sessions, commits, links, repo_name, has_git):
    payload = {
        "repo": repo_name, "hasGit": has_git,
        "sessions": [{"id": s["id"], "agent": s.get("agent", "claude"),
                      "start": s["start"].isoformat(), "n": s["n_msgs"],
                      "prompt": clip(s["prompts"][0][1] if s["prompts"] else ""),
                      "excerpt": s["excerpts"][0][1] if s["excerpts"] else "",
                      "stories": [{"file": rel_one(st["file"], s), "t": st["t"].isoformat() if st["t"] else "",
                                   "prompt": clip(st["prompt"]), "reason": st.get("reason") or ""}
                                  for st in s.get("stories", [])],
                      "files": sorted(s.get("files_rel", set()))} for s in sessions],
        "commits": [{"hash": c["hash"][:7], "subject": c["subject"],
                     "time": c["time"].isoformat() if c["time"] else "",
                     "files": c["files"]} for c in commits],
        "links": [{"s": L["session"], "c": L["commit"][:7], "score": L["score"],
                   "overlap": L["overlap"], "inside": L["inside"]} for L in links],
    }
    tpl = Path(__file__).with_name("report_template.html").read_text(encoding="utf-8")
    return tpl.replace("__DATA__", json.dumps(payload, ensure_ascii=False))

def report(repo: Path, demo=False):
    if demo:
        sessions, commits = demo_data()
        links = link(sessions, commits, Path("."))
        name, has_git = "keystroke-decoder (demo data)", True
    else:
        print("[*] scanning Claude Code transcripts (live + archive)…")
        sessions = find_sessions(repo)
        print(f"[*] {len(sessions)} session(s) found for this project")
        has_git = is_git_repo(repo)
        commits = git_commits(repo) if has_git else []
        if has_git: print(f"[*] {len(commits)} commit(s) in git log")
        else: print("[i] no git repo here — session-only report (run `git init` to start linking commits)")
        for s in sessions: relativize(s, repo)
        links = link(sessions, commits, repo) if commits else []
        name = repo.resolve().name
        if not sessions:
            print("[!] no sessions matched this folder. Tips: run `python gitwhy.py archive` first;"
                  " make sure you've used Claude Code inside this exact folder.")
    print(f"[*] {len(links)} session↔commit link(s)")
    out = (repo if repo.exists() else Path(".")) / "gitwhy-report.html"
    out.write_text(render(sessions, commits, links, name, has_git), encoding="utf-8")
    print(f"[✓] wrote {out.resolve()}")
    write_agent_context(repo if repo.exists() else Path("."), sessions, commits, links, name)

def write_agent_context(base: Path, sessions, commits, links, name):
    """Token-cheap provenance digest for coding agents (Claude Code, Codex, Cursor).
    Point the agent at GITWHY.md instead of letting it re-grep history."""
    cmap = {c["hash"][:7]: c for c in commits}
    per_file = {}
    for s in sessions:
        for st in s.get("stories", []):
            f = rel_one(st["file"], s)
            per_file.setdefault(f, []).append((st["t"], st["prompt"], s.get("agent", "?")))
    best_commit = {}
    for L in links:
        for f in L["overlap"]:
            cur = best_commit.get(f)
            if not cur or L["score"] > cur[0]:
                best_commit[f] = (L["score"], L["commit"][:7])
    lines = [f"# GITWHY \u00b7 provenance digest \u00b7 {name}",
             "",
             "Purpose: WHY each file exists, recovered from AI coding sessions.",
             "Agents: read this before exploring; it replaces re-deriving intent from grep.",
             ""]
    for f in sorted(per_file):
        lines.append(f"## {f}")
        eps = sorted(per_file[f], key=lambda x: (x[0] is None, x[0]))
        seen_p = set()
        shown = 0
        for t, prompt, agent in eps:
            key = prompt[:60]
            if key in seen_p: continue
            seen_p.add(key)
            day = t.date().isoformat() if t else "?"
            lines.append(f"- {day} [{agent}] {clip(prompt, 110)}")
            shown += 1
            if shown >= 4: break
        bc = best_commit.get(f)
        if bc and bc[1] in cmap:
            lines.append(f"- commit {bc[1]}: {cmap[bc[1]]['subject']}")
        lines.append("")
    md = "\n".join(lines)
    (base / "GITWHY.md").write_text(md, encoding="utf-8")
    (base / "gitwhy.json").write_text(json.dumps({
        "repo": name,
        "files": {f: [{"date": (t.date().isoformat() if t else None), "agent": a,
                       "prompt": clip(p, 200)} for t, p, a in v]
                  for f, v in per_file.items()},
        "commits": {f: bc[1] for f, bc in best_commit.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[\u2713] wrote GITWHY.md + gitwhy.json  ({len(md)//4} tokens approx \u2014 "
          "add 'Read GITWHY.md first' to your CLAUDE.md / AGENTS.md)")

def main():
    args = [a for a in sys.argv[1:]]
    if "--demo" in args: report(Path("."), demo=True); return
    if args and args[0] == "archive": archive(); return
    if not args: print(__doc__); sys.exit(1)
    repo = Path(args[0])
    if not repo.exists(): print(f"[!] path not found: {repo}"); sys.exit(1)
    report(repo)

if __name__ == "__main__":
    main()
