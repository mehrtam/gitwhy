# gitwhy — test suite report

## What was built

`test_gitwhy_full.py`: a git-based, stdlib-only (`unittest`, no third-party deps —
matching gitwhy's own "no dependencies" design) test suite that exercises every
behavior documented in `README.md`, independently of the existing smoke test
(`test_gitwhy.py`, kept as-is, 18 checks, still passing).

Coverage, mapped to the README sections that specify them:

| README section | Test class | What's verified |
|---|---|---|
| "Archives your transcripts before the reaper runs" | `TestArchive` | copies from both agent dirs, skips already-safe files, re-copies grown files, doesn't crash on empty state |
| "Files extracted from Edit/Write tool calls" | `TestClaudeParsing` | `Edit`, `Write`, `MultiEdit`, `NotebookEdit` all extract their file path; noise (`API error`, `/login`, `oauth`, `traceback`, `exit code N`, `Error:`, `fatal:`) is filtered from prompts; CLI-only prompts (`/compact`, `claude`, `claude doctor`, `exit`, `yes`, …) are filtered; sub-20-char and `isMeta` messages ignored; reasoning is backfilled onto the story it explains; `git commit` inside a `Bash` call is detected; malformed JSON lines don't crash the parser |
| "`apply_patch` payloads (Codex)" | `TestCodexParsing` | file extraction from `Add File:`/`Update File:` patches, in-session commit detection, same noise/CLI filtering as Claude |
| "Sniff format" (`parse_any`) | `TestFormatSniffing` | `rollout-*` filename forces the Codex parser; content-sniffing (`session_meta`/`response_item`) works even without that prefix; anything else defaults to the Claude parser |
| "Resumed/duplicated transcripts are collapsed by content fingerprint" | `TestDedupeAndScoping` | a resume-copy transcript collapses into one session (keeping the richer copy); sessions whose `cwd` doesn't match the repo are excluded; Windows-style case/slash differences in `cwd` still match |
| "Three stacked signals" (file overlap, time window, in-session commit) | `TestGitAndLinking` | `git log --numstat` parsing (hash/author/time/subject/files); non-repo path handled without crashing; each signal scored independently (overlap×2, window+3, inside+5); the in-session-commit signal outranks overlap-only, matching the README's claim that it's "the strongest one"; no link when no signal fires |
| "The three outputs" / `gitwhy.json` shape | `TestReportAndDigest` | all three files written; HTML contains both agent badges and zero noise leakage; `gitwhy.json` matches the documented `{repo, files: {file: [{date, agent, prompt}]}, commits: {file: hash}}` shape; `GITWHY.md` covers every touched file and stays small; report degrades gracefully with no git repo; `--demo` needs no real data at all |
| CLI (`archive`, `<path>`, `--demo`) | `TestCLI` | each invoked as a real subprocess (not just as library calls), including the no-args usage message and the not-found-path error path |

**43 tests** (44 after the regression test added below), all green, ~90s wall
time (dominated by real `git` subprocess calls per test — this is the
"git-based" fixture cost, not test overhead).

## What was going wrong

### 1. Leaked file handle in `parse_any()` (real bug, fixed)

`gitwhy.py:192` did:

```python
head = path.open(encoding="utf-8", errors="replace").readline()
```

The file object is never closed — it's only reclaimed when the garbage
collector gets to it. Confirmed with `warnings.catch_warnings` +
`ResourceWarning`, and directly observed as `ResourceWarning: unclosed file`
noise during the full test run (`find_sessions` calls `parse_any` once per
transcript file, so this scales with the number of sessions on disk).

**Fix applied**: wrapped it in a `with` block (`gitwhy.py:191-193`). Harmless
on small setups, but on a real machine with hundreds of historical transcripts
this was accumulating open handles for the lifetime of the process.

### 2. `test_gitwhy.py` crashes on its second consecutive run on Windows (real bug, fixed)

Running the project's own documented smoke test twice in a row —
`python test_gitwhy.py` then `python test_gitwhy.py` again — crashed the
second time with:

```
FileExistsError: [WinError 183] Cannot create a file when that file already
exists: '...\test-workspace\proj'
```

Root cause: `test_gitwhy.py:29` did `shutil.rmtree(WS, ignore_errors=True)`
to clear the sandbox before each run. On Windows, git leaves committed
objects under `.git/objects/**` **read-only**; `ignore_errors=True` silently
swallows the resulting `PermissionError` on those files, leaving
`test-workspace/proj/.git/...` partially in place, so the next run's
`PROJ.mkdir(parents=True)` hits a directory that already (partially) exists.
Confirmed directly: 6 read-only files were left under `test-workspace/proj/.git`
after a normal run.

This matters because CI (`.github/workflows/tests.yml`) runs this exact
script on `windows-latest`, and any local dev loop on Windows (this
environment) hits it on the very next invocation.

The project already has the correct fix pattern in `benchmark_gitwhy.py`'s
`wipe()` — clear the read-only bit and retry via `onerror`. `test_gitwhy.py`
just wasn't using it.

**Fix applied** (`test_gitwhy.py:28-31`): replaced the bare
`shutil.rmtree(WS, ignore_errors=True)` with an `onerror` callback that
`os.chmod`s the offending path writable and retries the removal, matching
`benchmark_gitwhy.py`'s existing approach. Verified by running the script
twice back-to-back post-fix: both runs now report `18 passed, 0 failed`.

### 3. Real run against this repo produced 0 session↔commit links (not a bug — the README's own disclosed limitation, observed live)

Running `python gitwhy.py .` against this actual repo (at the user's request)
found 3 sessions and 10 commits, but **0 links**. Investigating with
`find_sessions()`:

- The only session with any edited files is *this* live conversation
  (uncommitted at the time of the run).
- The other two matches are Codex sessions from March/May 2026 with prompts
  but zero files touched — unrelated leftovers that happen to share this
  folder's `cwd`.
- Every one of the 10 real commits (all dated 2026‑07‑26) has **no
  corresponding transcript** under `~/.claude/projects/...gitwhy` — those
  sessions were never captured here (repo built elsewhere / transcripts
  already rotated out).

This is precisely the scenario the README's "Honest limitations" section
warns about ("exotic workflows... will thin the links"; "transcripts deleted
before your first `archive` run are gone"). The pipeline behaved correctly —
it just had no evidence to link. It surfaced a **UX gap**, not a correctness
bug: `report()` prints a bare `[*] 0 session↔commit link(s)` with no hint
about *why*, even though it has enough information (sessions > 0, commits > 0,
links == 0) to suggest a likely cause.

**Strategy (not applied — a product decision, flagged for the maintainer)**:
when `links == 0` but both `sessions` and `commits` are non-empty, print an
additional diagnostic line, e.g.:

```
[i] 0 links despite N session(s) and M commit(s) — likely cause: none of
    these sessions touched files inside the commit time-windows, or their
    transcripts predate what's retained. Run `gitwhy archive` right after
    each work session to stop losing this evidence.
```

### 4. Two minor doc/behavior gaps (not bugs, flagged for documentation)

- `all_transcript_files()` also scans `CLAUDE_DIR/sessions`, not just
  `CLAUDE_DIR/projects` as the "Supported agents" table states. Harmless
  (forward-compatible with a possible future Claude Code transcript layout),
  but undocumented.
- The Codex root is globbed as `**/*.jsonl`, not `**/rollout-*.jsonl` as
  documented — the `rollout-` filename is actually just one of two ways
  `parse_any()` *sniffs* the format (the other being content-sniffing), so
  the code is intentionally more permissive than the table implies. Worth a
  one-line footnote in the README rather than a code change, since tightening
  it would silently drop any future non-`rollout-`-named Codex transcript.

### 5. Out of scope for this suite

The interactive graph and per-file "origin story" UI in
`report_template.html` were checked structurally (every field the JS reads —
`s.prompt`, `s.stories`, `l.overlap`, `l.inside`, etc. — matches exactly what
`render()` emits, so there's no payload/template contract mismatch), but
actual browser rendering wasn't exercised — that needs a browser, and the
project's own test philosophy (stdlib only, sandboxed) doesn't reach that far.
Flagging as a known gap rather than silently claiming full UI coverage.

## Changes made

- Added `test_gitwhy_full.py` (44 checks, stdlib-only, git-based fixtures).
- Fixed the file-handle leak in `gitwhy.py`'s `parse_any()`.
- Fixed the Windows double-run crash in `test_gitwhy.py`'s sandbox cleanup.
- Added `test_gitwhy_full.py -v` to both CI jobs in
  `.github/workflows/tests.yml`.
- Documented the new suite in the README's `## Tests` section.

## Net result

- `test_gitwhy.py`: 18/18 passing, and now safely re-runnable back-to-back on
  Windows (previously crashed on the second run).
- `test_gitwhy_full.py`: 44/44 passing.
- Two real bugs found and fixed: a leaked file handle in `parse_any()`, and a
  Windows-only cleanup crash in the project's own documented test entry point.
- One live, real-repo run surfaced the exact edge case the README already
  documents as a known limitation — validated the honesty of that section
  rather than contradicting it — plus a concrete UX improvement suggestion
  (print *why* when links == 0 despite having sessions and commits).
- Two minor README/code permissiveness mismatches flagged for a documentation
  follow-up, not urgent.
