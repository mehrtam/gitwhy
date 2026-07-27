# gitwhy
![tests](https://github.com/mehrtam/gitwhy/actions/workflows/tests.yml/badge.svg) 

[![Live Demo](https://img.shields.io/badge/Live_Demo-try_it_now-2ea44f)](https://mehrtam.github.io/gitwhy/)

[![Understand--Anything](https://img.shields.io/badge/reads-Understand--Anything_graphs-6b94b8)](https://github.com/Egonex-AI/Understand-Anything)
**`git blame` tells you who wrote the line. This tells you why.**

## What is this?

When you build software with AI agents (Claude Code, Codex), the *reasoning* —
what you asked for, what the agent decided, which alternatives were rejected —
lives in session transcripts on your disk. gitwhy turns those transcripts into
three useful things:

- **A visual report**: click any file in your project and see the conversations
  that created it, in order — *you asked → the agent reasoned → the commit*.
- **A safety copy** of all your transcripts, because Claude Code **deletes them
  after 30 days by default** — silently, with no undo
  ([#59248](https://github.com/anthropics/claude-code/issues/59248),
  [#62476](https://github.com/anthropics/claude-code/issues/62476)).
- **A small memory file (`GITWHY.md`)** your agents read at the start of each
  session, so they already know *why* the code is the way it is instead of
  re-discovering it every time.

One Python file. No dependencies. 100% local — nothing is uploaded anywhere.

<img width="1257" height="747" alt="gitwhy provenance graph" src="https://github.com/user-attachments/assets/a317ac26-9826-4b7c-afa5-663037333a6a" />

<img width="1243" height="720" alt="gitwhy origin story view" src="https://github.com/user-attachments/assets/7620240c-b527-4408-9cce-8b0c932b2b00" />

*Graph and origin-story view from a 200-session benchmark history — or skip the screenshots and
[click around the live demo](https://mehrtam.github.io/gitwhy/). Your real project's report shows
your real files and your real conversations.*

## Try it in 60 seconds

No setup, no data needed — just Python 3.9+:

```bash
python gitwhy.py --demo
```

Open the `gitwhy-report.html` it creates. That's the tool, running on sample
data. Now do it for real:

```bash
# 1. Protect your history FIRST (copies every transcript to ~/.gitwhy/archive)
python gitwhy.py archive

# 2. Build the report for any project you've used Claude Code or Codex in
python gitwhy.py /path/to/your/repo
```

This writes three files next to your code:

| file | who it's for | what it is |
|---|---|---|
| `gitwhy-report.html` | you | interactive graph + per-file origin stories |
| `GITWHY.md` | your AI agents | compact "why each file exists" memory |
| `gitwhy.json` | scripts & tools | the same provenance as structured data |

Works the same on Windows (`python gitwhy.py C:\Users\you\project`), macOS, and Linux.

## Stop the deletions at the source

Do this once. In `~/.claude/settings.json`:

```json
{ "cleanupPeriodDays": 3650 }
```

⚠️ Do **not** use `0` — a known bug makes `0` stop transcripts being written at
all ([#23710](https://github.com/anthropics/claude-code/issues/23710)).

## Give your agent the memory

Add one line to your project's `CLAUDE.md` (or `AGENTS.md` for Codex):

> Before exploring this codebase, read `GITWHY.md` — it explains why each file exists.

Agents read that file automatically at session start. The digest costs roughly
**~130 tokens per file**, so even a large project's entire "why" fits in a few
thousand tokens — instead of the agent re-deriving intent with grep/read loops
every single session.

## Plays well with Understand-Anything

If your repo has a committed [Understand-Anything](https://github.com/Egonex-AI/Understand-Anything)
knowledge graph (`.ua/` — the 73k⭐ codebase mapper), gitwhy detects it automatically and merges
the two views: their **structure** (layers, functions, classes, summaries) appears alongside its
**provenance** in every file's panel, and their relationships render as structure edges in the
graph. No setup — run their `/understand` once, then run gitwhy as normal. Validated against
plugin v2.9.4. Without it, gitwhy works exactly the same, just structure-free.

## What can I build on `gitwhy.json`?

The JSON has a simple shape:

```json
{
  "repo": "myproject",
  "files": {
    "src/auth.py": [
      { "date": "2026-04-01", "agent": "claude",
        "prompt": "refactor the auth flow to use short-lived tokens" }
    ]
  },
  "commits": { "src/auth.py": "aa61cae" }
}
```

Ideas people can build in an afternoon: a CI bot that comments each PR with the
origin story of the files it touches; provenance-aware search over your
codebase; "which files did we change for feature X?" scripts. It's also the
foundation the planned MCP server will query. If you build something on it,
open an issue — I'd love to see it.

## How the linking works

gitwhy connects sessions to commits using three stacked signals, strongest last:

1. **File overlap** — files edited in the session (from `Edit`/`Write` tool
   calls in Claude Code, `apply_patch` payloads in Codex) vs. files changed in
   each commit (`git log --numstat`)
2. **Time windows** — the commit happened during (or shortly after) the session
3. **In-session commits** — a `git commit` command visibly executed *inside*
   the session transcript; this is near-certain evidence

Each file edit is attributed to the prompt that caused it, paired with the
agent's adjacent reasoning. Login errors, rate-limit noise, and CLI commands
are filtered out; duplicated/resumed transcripts are collapsed by content
fingerprint.

## Supported agents

| Agent | Transcripts read from | Status |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` | ✅ |
| OpenAI Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl` | ✅ |
| Cursor / Gemini CLI | — | roadmap — adapters welcome; see `parse_codex_session` for the pattern |

## Honest limitations

- **Linking is heuristic.** The three signals catch the common case well, but
  unusual workflows (committing days later from another terminal, moving a repo
  so absolute paths change) will thin the links.
- **Reasoning excerpts are real quotes chosen by adjacency**, not AI summaries —
  occasionally the excerpt isn't the *best* quote from the session.
- **Already-deleted transcripts are gone.** gitwhy can't resurrect what the
  cleanup already removed; it can only make sure it never happens again.

## Roadmap

- MCP server so agents can *query* provenance ("why does this function exist?")
- Cursor and Gemini CLI adapters
- Auto-archive on session end via agent hooks
- Graph: time-axis layout and label physics for very dense projects

## Benchmarks

Measured by `benchmark_gitwhy.py` on a consumer Windows laptop (synthetic
histories with realistic multi-MB transcripts dominated by tool output, real
git repos, exact token counts via tiktoken cl100k_base):

| scale | sessions | commits | transcripts | parse+link | render | raw corpus | GITWHY.md | compression |
|---|---|---|---|---|---|---|---|---|
| SMALL | 10 | 24 | 0.1 MB | 0.17 s | 0.03 s | 20,149 tok | 2,157 | 9× |
| MEDIUM | 60 | 150 | 0.6 MB | 0.78 s | 0.02 s | 161,000 tok | 8,018 | 20× |
| LARGE | 200 | 300 | 79.5 MB | 2.47 s | 0.05 s | 13,762,575 tok | 16,156 | 852× |

Honest framing: compression measures the digest against the raw history it
summarizes — a ceiling, not a measured per-session saving inside a live agent.
Run it yourself: `python benchmark_gitwhy.py` (install `tiktoken` for exact
counts).

## FAQ

**How is this different from Claude Code's `/compact`?**
Different jobs. `/compact` is *working memory*: it summarizes the current
conversation, inside one session, and its summary dies with that session's
transcript. gitwhy is *long-term memory*: it spans all sessions and both
agents, links reasoning to durable artifacts (commits, files), lives in your
repo, and archives the raw transcripts. `/compact` is RAM; gitwhy is disk.

**How is this different from `CLAUDE.md`?**
`CLAUDE.md` is *instructions*, written by a human, about how the agent should
behave now — and it goes stale unless someone updates it. `GITWHY.md` is
*memory*, generated from history, about why the code became what it is — and it
regenerates from the transcripts, so it can't go stale. They work together:
your `CLAUDE.md` simply points the agent at `GITWHY.md`.

**Is this a knowledge graph?**
It's a **provenance graph** — sessions, commits, and files as nodes with
produced/touched edges — built *deterministically* from transcripts and
`git log`. Every edge is a verifiable fact and costs zero LLM tokens to build.
It is *not* (yet) a semantic knowledge graph — no entity extraction or
embeddings. That query layer is the MCP-server roadmap item.

## Tests

```bash
python test_gitwhy.py    # builds Claude + Codex fixtures in a sandbox, 18 checks
```

## License

MIT — see [LICENSE](LICENSE).

Built because I lost months of my own research context to the 30-day cleanup
and decided that should never happen to anyone again.

— Mehrta (Fateme) Eslami · [mehrtam.github.io](https://mehrtam.github.io)
