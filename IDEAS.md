# gitwhy — feature ideas

Grounded in the current pipeline (`gitwhy.py`: parsing → linking → render →
digest) and the gaps found while building `test_gitwhy_full.py` /
`TEST_REPORT.md`. Distinct from `## Roadmap` in the README — these are
concrete extensions, not restatements of it.

## Capture & retention

- **`gitwhy install-hook`** — the README already shows the exact
  `post-commit` snippet for regenerating the digest on every commit, but asks
  the user to create it by hand. A one-line subcommand that writes
  `.git/hooks/post-commit` (and chmods it) turns a copy-paste step into a
  real feature, and is a natural companion to the roadmap's "auto-archive on
  session end" — one hook covers commits, the other covers sessions.
- **Secret redaction before archiving** — `archive()` copies transcripts
  verbatim into `~/.gitwhy/archive`. Pasted API keys, tokens, or `.env`
  contents in a prompt get archived right along with everything else, and
  now live *forever* instead of rotating out in 30 days. A cheap regex pass
  (AWS keys, `sk-`/`ghp_`-style tokens, common `PRIVATE KEY` blocks) run at
  archive time — redacting into the copy, never the live transcript — would
  close a real exposure window the tool currently introduces.
- **`gitwhy doctor`** — reads `~/.claude/settings.json`, reports the actual
  configured `cleanupPeriodDays` (flagging the risky `0` value from
  [#23710](https://github.com/anthropics/claude-code/issues/23710)), how
  stale `~/.gitwhy/archive` is vs. the newest live transcript, and whether
  any session for the current repo has files but no commit link. Surfaces
  exactly the kind of gap `TEST_REPORT.md`'s "0 links" case study hit, before
  the user has to debug it manually.

## Linking & trust

- **Explain the zero-link case** — from the real run in `TEST_REPORT.md`:
  `report()` can print `0 session↔commit link(s)` even with sessions and
  commits both present, with no hint why. Since `link()` already computes
  overlap/window/inside per pair, it can cheaply report the *closest miss*
  ("session X came within 6h of commit Y but touched no shared files") instead
  of a bare count.
- **Confidence tier on each link** — `score` already exists
  (`overlap*2 + window(3) + inside(5)`); expose it in the report as
  low/medium/high rather than a raw number, so a reader can tell a
  file-overlap guess from an in-session-commit certainty at a glance.
- **Per-line, not just per-file, provenance** — `git blame -L` pinpoints the
  commit for one line; that commit hash is already a key into
  `gitwhy.json["commits"]`. `gitwhy why <file>:<line>` chaining the two would
  answer "why does *this line* exist" instead of "why does this file exist."

## Scale

- **Split `GITWHY.md` per top-level directory** for large repos. The
  benchmark's own LARGE scale (200 sessions) already produces a ~16k-token
  digest; a repo an order of magnitude bigger would make agents load context
  for files they'll never touch in a given session. A `GITWHY.md` per
  package/module (mirroring `per_file` grouping that already exists in
  `write_agent_context`) lets an agent read only the slice near its cwd.
- **Local BM25-style search over `gitwhy.json`** — stdlib-only keyword
  scoring (no embeddings, matching the project's "no dependencies" stance)
  over the `prompt` text already stored per file, exposed as
  `gitwhy search "auth token"`. Turns the README's own "provenance-aware RAG"
  idea into something that ships zero-dependency today, ahead of the
  embedding-based version that would need one.

## Agent & tooling integration

- **CI PR-comment bot** using `gitwhy.json` — the README already names this
  as something people could build; a small script pairing
  `gh pr diff --name-only` with `commits`/`files` from the JSON turns it into
  an actual shippable GitHub Action rather than a suggestion.
- **`gitwhy.json` schema version field** — nothing currently marks the shape
  of the JSON, so any external tool built against it (CI bots, the planned
  MCP server) has no way to detect a future breaking change. A `"schema": 1`
  key costs nothing now and avoids a silent breakage later.
