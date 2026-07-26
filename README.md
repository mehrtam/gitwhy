# gitwhy
![tests](https://github.com/mehrtam/gitwhy/actions/workflows/tests.yml/badge.svg)

**`git blame` tells you who wrote the line. This tells you why.**

Your AI coding agents — Claude Code, Codex — make design decisions with you every day: the alternatives rejected, the bug that forced the rewrite, the constraint you explained at 11pm. All of that lives in session transcripts on your disk. **And by default, it's deleted on a 30-day timer.** Claude Code's `cleanupPeriodDays` silently wipes transcripts on startup — no warning, no trash, no recovery ([#59248](https://github.com/anthropics/claude-code/issues/59248), [#62476](https://github.com/anthropics/claude-code/issues/62476)).

gitwhy does three things about that:

1. **Archives** your transcripts before the reaper runs (`gitwhy archive`)
2. **Links** sessions to the git commits and files they produced, and renders a local provenance report — an interactive graph plus a per-file "origin story": *the human asked → the agent reasoned → the commit*
3. **Feeds it back to your agents**: writes a token-cheap `GITWHY.md` + `gitwhy.json` digest so Claude Code / Codex stop re-deriving intent from `grep` every session

Everything runs 100% locally. Nothing is uploaded anywhere. Stdlib only — no dependencies.

<img width="1257" height="747" alt="Screenshot 2026-07-26 205022" src="https://github.com/user-attachments/assets/a317ac26-9826-4b7c-afa5-663037333a6a" />


<img width="1243" height="720" alt="Screenshot 2026-07-26 205136" src="https://github.com/user-attachments/assets/7620240c-b527-4408-9cce-8b0c932b2b00" />

*Provenance graph of a 200-session benchmark history — your real project's graph shows your real files.*


## Quickstart

Requires Python 3.9+ and (optionally) git.

```bash
# 1. protect your history first — copy every transcript to ~/.gitwhy/archive
python gitwhy.py archive

# 2. provenance report for any project you've used Claude Code or Codex in
python gitwhy.py /path/to/your/repo
#    → gitwhy-report.html   (interactive graph + per-file origin stories)
#    → GITWHY.md            (token-cheap digest for your agents)
#    → gitwhy.json          (machine-readable provenance)

# no data yet? preview the UI:
python gitwhy.py --demo
```

Windows: same commands with `python`; paths like `C:\Users\you\project` work fine.

**Also do this once** — stop the deletions at the source. In `~/.claude/settings.json`:

```json
{ "cleanupPeriodDays": 3650 }
```

(Do **not** use `0` — a known bug makes 0 disable transcript writing entirely: [#23710](https://github.com/anthropics/claude-code/issues/23710).)

## Give your agent the memory

Add one line to your `CLAUDE.md` or `AGENTS.md`:

> Before exploring this codebase, read `GITWHY.md` — it explains why each file exists.

A three-month, 51-commit project digests to ~3k tokens — versus the tens of thousands an agent burns cold-starting with grep/read loops, every single session.

## The three outputs

One pipeline, three consumers:

| file | for | what's inside |
|---|---|---|
| `gitwhy-report.html` | humans | interactive provenance graph + per-file origin stories |
| `GITWHY.md` | agents | token-cheap digest, read at session start via `CLAUDE.md` / `AGENTS.md` |
| `gitwhy.json` | machines | structured provenance for scripts and tooling |

`gitwhy.json` shape:

```json
{
  "repo": "myproject",
  "files": {
    "src/auth.py": [
      { "date": "2026-04-01", "agent": "claude",
        "prompt": "refactor the auth flow to use short-lived tokens with refresh rotation" }
    ]
  },
  "commits": { "src/auth.py": "aa61cae" }
}
```

Things people build on it: a CI bot that comments each PR with the origin story of
the files it touches, provenance-aware RAG over your codebase, or "which files did
we change for X?" scripts. It's also the substrate the roadmap MCP server will
query. If you build something on it, open an issue — I'd love to see it.

## How the linking works

- **Files**: extracted from `Edit`/`Write` tool calls (Claude Code) and `apply_patch` payloads (Codex)
- **Commits**: matched by three stacked signals — file overlap with `git log --numstat`, session time-window containment, and the strongest one: `git commit` commands observed *inside* the session
- **Stories**: each file edit is attributed to the prompt that caused it, with the agent's adjacent reasoning; login errors, rate-limit noise, and CLI commands are filtered out
- **Dedupe**: resumed/duplicated transcripts are collapsed by content fingerprint

## Supported agents

| Agent | Transcripts read from | Status |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | ✅ |
| OpenAI Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` | ✅ |
| Cursor / Gemini CLI | — | roadmap (adapters welcome — see `parse_codex_session` for the pattern) |

## Honest limitations

- Linking is heuristic. Time windows + file overlap + in-session commits catch the common case well; exotic workflows (committing days later from another terminal, moved repos changing absolute paths) will thin the links.
- "The agent reasoned" excerpts are chosen by adjacency, not summarization — they're real quotes, occasionally not the *best* quote.
- Transcripts deleted before your first `archive` run are gone. gitwhy can't resurrect them; it can only make sure it never happens again.

## Roadmap

- MCP server so agents can *query* provenance ("why does this function exist?") instead of reading a static digest
- Cursor and Gemini CLI adapters
- Auto-archive via agent hooks (archive on every session end)
- Graph: time-axis layout mode and label physics for very dense projects

## Benchmarks

Measured by `benchmark_gitwhy.py` on a consumer Windows laptop (synthetic
histories with realistic multi-MB transcripts dominated by tool output, real git
repos, exact counts via tiktoken cl100k_base):

| scale | sessions | commits | transcripts | parse+link | render | raw corpus | GITWHY.md | compression |
|---|---|---|---|---|---|---|---|---|
| SMALL | 10 | 24 | 0.1 MB | 0.17 s | 0.03 s | 20,149 tok | 2,157 | 9× |
| MEDIUM | 60 | 150 | 0.6 MB | 0.78 s | 0.02 s | 161,000 tok | 8,018 | 20× |
| LARGE | 200 | 300 | 79.5 MB | 2.47 s | 0.05 s | 13,762,575 tok | 16,156 | 852× |

Honest framing: compression measures digest vs. the raw history it summarizes —
the ceiling, not a measured per-session saving inside a live agent. The digest
costs roughly **~130 tokens per file with provenance**, so even a 120-file,
200-session project's entire "why" fits in ~16k tokens. Run the benchmark on
your own machine: `python benchmark_gitwhy.py` (install `tiktoken` for exact
token counts).

## FAQ

**How is this different from Claude Code's `/compact`?**
Different jobs. `/compact` is *working memory*: it summarizes the current
conversation inside one session to free context, its summary lives only in that
session, and it dies with the transcript. gitwhy is *long-term memory*: it works
across all sessions and both agents, links reasoning to durable artifacts
(commits, files), lives in your repo, and archives the raw transcripts.
`/compact` is RAM; gitwhy is disk.

**Is this a knowledge graph?**
It's a **provenance graph** — sessions, commits, and files as nodes, with
produced/touched edges — built deterministically from transcripts and `git log`.
Every edge is a verifiable fact, and building it costs zero LLM tokens. What it
is *not* (yet) is a semantic knowledge graph: no entity extraction, embeddings,
or concept nodes. That query layer is the MCP-server roadmap item; the
deterministic core stays free and trustworthy underneath it.

## Tests

```bash
python test_gitwhy.py    # builds Claude + Codex fixtures in a sandbox, 18 checks
```

## License

@"
MIT License

Copyright (c) 2026 Fateme (Mehrta) Eslami

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"@ | Set-Content -Encoding utf8 LICENSE
git add LICENSE
git commit -m "Add MIT license"
git push
