#!/usr/bin/env python3
"""
benchmark_gitwhy.py — run next to gitwhy.py + report_template.html:

    python benchmark_gitwhy.py

Generates synthetic-but-realistic agent histories at three scales
(SMALL / MEDIUM / LARGE — up to 200 sessions with multi-MB transcripts,
real git repos with hundreds of commits), then measures:

  PERFORMANCE : parse+link time, render time, MB/s, sessions/s
  TOKENS      : raw transcript corpus vs GITWHY.md digest (compression),
                using tiktoken if installed, else chars/4 heuristic

Sandboxed in ./bench-workspace — never touches ~/.claude or ~/.codex.
Writes BENCHMARK.md with the results table. Total runtime ~1-3 minutes.
"""
import json
import os
import random
import subprocess
import sys
import time
import importlib.util
import shutil
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE / "bench-workspace"
random.seed(42)

# ---------- token counting ----------


def make_counter():
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return (lambda s: len(enc.encode(s))), "tiktoken cl100k_base (real tokenizer)"
    except Exception:
        return (lambda s: len(s) // 4), "chars/4 heuristic (install tiktoken for exact counts)"


count_tokens, TOKENIZER = make_counter()


def wipe(path: Path):
    if not path.exists():
        return

    def onerr(fn, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            fn(p)
        except Exception:
            pass
    for _ in range(3):
        shutil.rmtree(path, onerror=onerr)
        if not path.exists():
            return
        time.sleep(0.3)


PROMPTS = ["refactor the auth flow to use short-lived tokens with refresh rotation",
           "the eval numbers look wrong on the chronological split, investigate and fix why",
           "add caching in front of the model server, redis with a five minute ttl and jitter",
           "write integration tests for the ingest pipeline including malformed-row cases",
           "the worker is leaking memory on long runs, profile it and patch the accumulator",
           "add a metrics endpoint exposing p50 p95 latency and error rates per route",
           "the DANN branch destabilises early training, add a lambda warmup schedule",
           "deploy script should do canary then full rollout with automatic rollback"]
REASONS = ["Profiling shows the leak is in the result accumulator: it retains full tensors instead of scalars, so I'll detach and item() them, then add a regression test tracking RSS across five hundred iterations to lock the fix in.",
           "The chronological split reveals within-session drift that the random split averages away entirely; resampling per subject into fixed windows and reporting both makes the gap itself the headline metric rather than a footnote.",
           "Short-lived access tokens with rotating refresh tokens where refresh reuse triggers family revocation, which is the standard defence against token theft replay in this class of system."]
BLOAT = "tool output line with stack context and payload dump " * 40  # ~2KB unit


def gen_history(n_sessions, files_pool, commits_target, bloat_every=6, bloat_mult=1):
    """Write claude-format sessions + build a matching git repo. Returns paths."""
    tag = f"s{n_sessions}"
    claude_dir = WS / tag / "claude"
    proj = WS / tag / "proj"
    cproj = claude_dir / "projects" / "x"
    cproj.mkdir(parents=True, exist_ok=True)
    proj.mkdir(parents=True, exist_ok=True)

    def git(*a, env=None):
        subprocess.run(["git", "-C", str(proj), *a],
                       capture_output=True, env=env)
    git("init", "-q", ".")
    git("config", "user.email", "b@b.com")
    git("config", "user.name", "B")
    t = datetime.now(timezone.utc) - timedelta(days=120)
    commits_made = 0
    genv = os.environ.copy()
    for i in range(n_sessions):
        t += timedelta(hours=random.randint(4, 30))
        lines = []
        rounds = random.randint(2, 10)
        session_files = []
        for r in range(rounds):
            t += timedelta(minutes=random.randint(2, 15))
            ts = t.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            lines.append(json.dumps({"type": "user", "uuid": f"{i}-u{r}", "timestamp": ts,
                                     "cwd": str(proj), "gitBranch": "main",
                                     "message": {"role": "user", "content": random.choice(PROMPTS)}}))
            fs = random.sample(files_pool, random.randint(1, 3))
            session_files += fs
            content = [{"type": "text", "text": random.choice(REASONS)}]
            for k, f in enumerate(fs):
                content.append({"type": "tool_use", "id": f"{i}-t{r}{k}", "name": "Edit",
                                "input": {"file_path": str(proj / f)}})
            # realistic bloat: giant tool_result-style noise lines
            if r % bloat_every == 0:
                content.append({"type": "text", "text": BLOAT * bloat_mult})
            t += timedelta(minutes=random.randint(1, 8))
            lines.append(json.dumps({"type": "assistant", "uuid": f"{i}-a{r}",
                                     "timestamp": t.strftime('%Y-%m-%dT%H:%M:%S.000Z'), "cwd": str(proj),
                                     "message": {"role": "assistant", "content": content}}))
            if commits_made < commits_target and (r == rounds - 1 or random.random() < 0.35):
                lines.append(json.dumps({"type": "assistant", "uuid": f"{i}-g{r}",
                                         "timestamp": t.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                                         "message": {"role": "assistant", "content": [{"type": "tool_use",
                                                                                       "id": f"{i}-gc{r}", "name": "Bash",
                                                                                       "input": {"command": "git add . && git commit -m 'work'"}}]}}))
                for f in set(fs):
                    p = proj / f
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(f"# {random.random()}\n")
                genv["GIT_AUTHOR_DATE"] = genv["GIT_COMMITTER_DATE"] = t.isoformat()
                git("add", ".")
                git("commit", "-qm", random.choice(PROMPTS)[:60], env=genv)
                commits_made += 1
        (cproj /
         f"sess-{i:04d}.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return claude_dir, proj, commits_made


def mb(path: Path):
    return sum(f.stat().st_size for f in path.rglob("*.jsonl")) / 1e6


def run_scale(gw, name, n_sessions, n_files, commits_target, bloat_every=6, bloat_mult=1):
    files_pool = ([f"src/mod{i}.py" for i in range(n_files - 6)] +
                  ["train.py", "eval.py", "api.py", "worker.py", "configs/base.yaml", "README.md"])
    print(f"\n── {name}: generating {n_sessions} sessions …")
    g0 = time.perf_counter()
    claude_dir, proj, commits = gen_history(
        n_sessions, files_pool, commits_target, bloat_every, bloat_mult)
    size_mb = mb(claude_dir)
    print(
        f"   generated in {time.perf_counter()-g0:.1f}s · {size_mb:.1f} MB transcripts · {commits} commits")
    os.environ["CLAUDE_DIR"] = str(claude_dir)
    os.environ["CODEX_DIR"] = str(WS / "nonexistent-codex")
    os.environ["GITWHY_ARCHIVE"] = str(WS / "nonexistent-arch")
    # fresh module load so it picks up the env-configured directories
    spec = importlib.util.spec_from_file_location(
        f"gitwhy_{name}", HERE / "gitwhy.py")
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    if not hasattr(gw, "find_sessions"):
        size = (HERE / "gitwhy.py").stat().st_size
        print(
            f"\n[!] gitwhy.py loaded but is missing its functions (file is {size} bytes).")
        print("    This usually means an empty/partial download or a OneDrive cloud placeholder.")
        print(
            "    Fix: re-download gitwhy.py, confirm it is ~20+ KB, and ideally run from a")
        print("    non-OneDrive folder like C:\\dev\\gitwhy-bench. Then run: python gitwhy.py --demo")
        sys.exit(1)

    t0 = time.perf_counter()
    sessions = gw.find_sessions(proj)
    commits_parsed = gw.git_commits(proj)
    links = gw.link(sessions, commits_parsed, proj)
    t_parse = time.perf_counter() - t0

    t1 = time.perf_counter()
    for s in sessions:
        gw.relativize(s, proj)
    html = gw.render(sessions, commits_parsed, links, name, True)
    (proj / "gitwhy-report.html").write_text(html, encoding="utf-8")
    gw.write_agent_context(proj, sessions, commits_parsed, links, name)
    t_render = time.perf_counter() - t1

    digest = (proj / "GITWHY.md").read_text(encoding="utf-8")
    raw_chars = sum(f.stat().st_size for f in claude_dir.rglob("*.jsonl"))
    # raw token corpus: count on a sample for speed, scale up
    sample = ""
    for f in list(claude_dir.rglob("*.jsonl"))[:8]:
        sample += f.read_text(encoding="utf-8", errors="replace")
    sample_tokens = count_tokens(sample[:400_000])
    raw_tokens = int(raw_chars * (sample_tokens /
                     max(1, len(sample[:400_000]))))
    digest_tokens = count_tokens(digest)

    row = dict(name=name, sessions=len(sessions), commits=len(commits_parsed),
               links=len(links), size_mb=size_mb, t_parse=t_parse, t_render=t_render,
               mbps=size_mb / max(t_parse, 1e-9), report_kb=len(html)//1024,
               raw_tokens=raw_tokens, digest_tokens=digest_tokens,
               ratio=raw_tokens / max(1, digest_tokens))
    print(f"   parse+link {t_parse:.2f}s ({row['mbps']:.0f} MB/s) · render {t_render:.2f}s · "
          f"report {row['report_kb']} KB")
    print(f"   tokens: raw corpus ≈{raw_tokens:,} → digest {digest_tokens:,} "
          f"({row['ratio']:.0f}× smaller)")
    return row


def main():
    for f in ("gitwhy.py", "report_template.html"):
        if not (HERE / f).exists():
            print(f"[!] {f} must be in the same folder.")
            sys.exit(1)
    wipe(WS)
    WS.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("gitwhy", HERE / "gitwhy.py")
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    if not hasattr(gw, "find_sessions"):
        size = (HERE / "gitwhy.py").stat().st_size
        print(f"[!] gitwhy.py is missing its functions (file is {size} bytes) — "
              "re-download it; it should be ~20+ KB.")
        sys.exit(1)

    print(f"tokenizer: {TOKENIZER}")
    rows = [run_scale(gw, "SMALL",  10,  20,  30),
            run_scale(gw, "MEDIUM", 60,  60, 150),
            run_scale(gw, "LARGE", 200, 120, 300, bloat_every=2, bloat_mult=60)]

    # ---- results ----
    hdr = f"{'scale':7}{'sessions':>9}{'commits':>8}{'links':>7}{'MB':>7}{'parse s':>9}{'MB/s':>7}{'render s':>10}{'raw tokens':>13}{'digest':>9}{'ratio':>8}"
    lines = ["# gitwhy benchmark", "", f"Tokenizer: {TOKENIZER}",
             f"Machine: {sys.platform}, Python {sys.version.split()[0]}", "",
             "| scale | sessions | commits | links | transcripts | parse+link | throughput | render | raw corpus tokens | GITWHY.md tokens | compression |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    print("\n" + "=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name']:7}{r['sessions']:>9}{r['commits']:>8}{r['links']:>7}"
              f"{r['size_mb']:>7.1f}{r['t_parse']:>9.2f}{r['mbps']:>7.0f}{r['t_render']:>10.2f}"
              f"{r['raw_tokens']:>13,}{r['digest_tokens']:>9,}{r['ratio']:>7.0f}x")
        lines.append(f"| {r['name']} | {r['sessions']} | {r['commits']} | {r['links']} | "
                     f"{r['size_mb']:.1f} MB | {r['t_parse']:.2f} s | {r['mbps']:.0f} MB/s | "
                     f"{r['t_render']:.2f} s | {r['raw_tokens']:,} | {r['digest_tokens']:,} | "
                     f"{r['ratio']:.0f}× |")
    lines += ["",
              "## How to read the token numbers (honest version)",
              "",
              "- **Raw corpus tokens** = the full session transcripts for the project. That is what an",
              "  agent (or human) would have to read to recover *why* the code is the way it is.",
              "- **GITWHY.md tokens** = the digest gitwhy generates for agents to read at session start.",
              "- **Compression** = raw ÷ digest. This measures how much smaller the provenance digest is",
              "  than the history it summarizes — NOT a measured per-session saving inside a live agent.",
              "  Real-world agent savings depend on how much history the agent would actually have",
              "  re-explored; treat the ratio as the ceiling and 'digest is a few thousand tokens' as",
              "  the practical claim.",
              "- Synthetic transcripts include realistic bloat (large tool-output lines) but real",
              "  histories vary; run this benchmark on your own machine for your own numbers."]
    (HERE / "BENCHMARK.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[✓] wrote {HERE / 'BENCHMARK.md'}")
    print("[i] Reports to eyeball: bench-workspace/s200/proj/gitwhy-report.html")


if __name__ == "__main__":
    main()
