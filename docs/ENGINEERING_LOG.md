# Engineering log — problems and what fixed them

A record of what went wrong in this project and why, kept because most of it
was **not** obvious from the symptom. Organised by theme rather than by date:
the pattern is more useful than the chronology.

180 commits, 594 tests across 40 files, as of 21 September 2026.

---

## The shape almost every bug had

Nearly every real problem here looked like success while doing nothing.

| What it looked like | What was happening |
|---|---|
| Deploys going green for ten commits | The build never copied a file the app imports |
| An advisor answering normally | Half its instructions were missing from the request |
| A reply that just stopped | The model hit a token cap and nobody was told |
| A search box that cleared itself | The query was dropped before the request was sent |
| A faculty directory that worked | 37 real people were being deleted on every build |
| A migration that ran fine | It hit a table that did not exist yet and swallowed the error |

None of these raised anything. None appeared in a log. They were found by
somebody noticing something odd about the output, or by reading code for an
unrelated reason.

**The practice that came out of it:** when fixing one of these, make the
failure *loud* first, then fix the behaviour, then add a test that fails if
the silence returns. A surprising number of the tests in this repo exist to
turn a silent wrong answer into a noisy one.

---

## 1. Deployment

### The builder stage never copied a file the app imports
`8cbad5e` · broke every deploy for ten commits

`text_clean.py` was added at the repo root and imported by `search.py` at
module scope. The Docker **builder** stage is deliberately minimal — it copies
a handful of files and bakes the embedding indexes — and it still copied only
`search.py`. The index step died with `ModuleNotFoundError` and every deploy
after it failed. The live site sat ten commits behind while `main` was correct
the whole time.

**Fix.** Copy the file. **Prevention:** a test AST-scans `search.py` for
module-scope imports that resolve to repo-root modules and asserts the builder
copies each one. Reproduced exactly in an empty directory before fixing.

### Out of memory during startup
`9306fe2` · the deploy that failed after upgrading to a paid instance

`Out of memory (used over 512Mi)`, killed before the health check ran. Three
contributing causes, and one that made it invisible:

- **Invisible:** Python block-buffers stdout when it is not a terminal, so
  every `print()` sat in the buffer and died with the process. The log showed
  only the onnxruntime warning, which goes to stderr. `PYTHONUNBUFFERED=1`.
- **ONNX Runtime's CPU arena** pre-allocates a pool, grows it geometrically,
  and never returns it, so resident memory settles near the largest batch ever
  run. Disabled, along with memory patterns.
- **The paper index** — 56 MB of embeddings for 18,681 papers with exactly one
  caller, the Stage 4 collaborator search — was loaded at boot, putting it
  inside the memory high-water mark. Now lazy.

Startup now prints its own peak RSS, so the next failure states its number
instead of being estimated.

> **A wrong guess worth recording.** I assumed the 43,000-row seeding
> transaction was the cause and rewrote it. Measured, it cost about 4 MB. The
> rewrite was still an improvement but it was not the bug. Measure before
> concluding.

### A persistent disk arrives empty
`048284b`

Attaching a disk and pointing `DATA_DIR` at it makes `DB_PATH` a file that does
not exist. The app boots, creates its profile tables, and serves a directory
with **nobody in it** — process up, health check green, every search empty. The
documented remedy was to copy the seed over SSH, a manual step to remember on
the one day you change hosting plans.

**Fix.** The app detects an empty `DATA_DIR` and seeds it. Two safeguards make
that safe to run every boot: it only ever fills a gap, and it copies
**reference data only** (`faculty`, `papers`, `scholar_papers`,
`faculty_overrides`) through an allowlist.

> That allowlist is not caution for its own sake. The first end-to-end run
> copied `users`, `auth_sessions`, `profiles` and `proposals` out of the local
> development database, because that is what baked seeds are built from.

### Seeding could stop the app booting
`050ddbd`

`os.makedirs`, the gzip unpack and `sqlite3.connect` sat outside any
try/except. All three are filesystem operations against a disk mounted seconds
earlier. Any of them raising propagated out of startup and killed the process —
trading "the directory is empty" for "the site is gone".

### Configuring a plan before paying for it
`e3b145a`, `c1ba506`

`plan: starter` with a disk was committed before the upgrade actually happened.
Render rejects a disk on a free instance outright. Then the revert overcorrected
and would have downgraded an instance that *had* been upgraded.

**Prevention.** One test asserts plan, disk and `DATA_DIR` move as a unit:

```
free    + no disk + no DATA_DIR    deploys, loses data on redeploy
starter + disk    + DATA_DIR       deploys, keeps data
```

Every mixture is a bug, and they fail differently. A disk on free fails loudly.
**A disk without `DATA_DIR` is the quiet one** — the app writes to the container
filesystem while the paid volume sits empty beside it, and the data loss the
disk was bought to prevent happens anyway.

---

## 2. The advisor

### It crashed for every project past Stage 2
`5f1d6a9`

The prompt-caching split left `stable = ...` indented inside the `else:` branch
handling Stages 1–2. Those stages worked; nothing else did. The moment a
project saved a novelty claim, the next turn raised `UnboundLocalError`.

Because the stage is derived from saved database columns — deliberately, so it
survives restarts — **the crash was permanent for that project**. A reload
could not clear it. Anyone who got as far as a novelty claim could never use
the advisor on that project again.

Found while reading the prompt for an unrelated reason.

### A 72KB prompt nobody could edit safely
`020a2a2`

The whole instruction set was one f-string inside `web_app.py`. Past roughly
50KB it stopped being editable: sections drifted into contradicting each other,
the stage rules and the lens rules disagreed about when feasibility could be
raised, and an indentation slip inside it took the advisor down (above).

**Fix.** Fourteen text files in `prompts/advisor/`, assembled at request time.
Verified **byte-identical** to the previous f-string, so nothing about the
advisor's behaviour changed when it moved.

Order comes from a declared list, not the filesystem — alphabetical order works
until somebody adds `tone_extra.md`, and that failure is a silently reordered
prompt rather than an error. A missing file is fatal at import, because the
natural failure is an advisor that still answers but has no send check.

### Every turn carried instructions for stages it was not in
`c4f4271`

A project in Stage 1 shipped the entire Stage 4 proposal-building apparatus —
4,500 tokens it would not touch for an hour. **20,661 → ~14,950 tokens, 28%
off.** Cost is the lesser reason: Stage 4 is where the stance inverts and
offering options becomes correct, and Stage 1 is where doing that destroys the
interview. Those rules are now *absent* rather than present-and-forbidden.

Each stage also ships the *next* one. Stage is derived once at the top of a
turn, but a turn can cross a boundary — the advisor saves a novelty claim and
is in Stage 3 from that moment, still holding the prompt it started with.

### It agreed with everything
`325a4c8`, `c94ce99`, `e07495d`

Reported as: the language reads as supportive, and a professor needs to be
argued with. Three separate rules came out of it — never validate by default
(with a banned-phrase list), never capitulate when pushed back on, and go at
the weakest part of every answer. Then a send check that runs on every drafted
message, scanning for an evaluative first sentence, question count, and praise
of any polarity.

> Two of these rules were later **dropped by my own rewrite** of the Explore
> prompt and caught only by existing tests. That is the argument for structural
> tests on prompt text: a rule deleted in a rewrite is invisible until somebody
> notices the bot stopped doing something.

---

## 3. Getting the answer to the screen

`1b30b62`, `9995bb1` · reported as "why is this message cut off?"

One report, three independent defects, none of which logged anything. Notably,
the obvious explanation was **wrong**: the message was ~290 tokens against an
1,800 cap.

| Defect | Effect |
|---|---|
| `finish_reason == "length"` treated as `"stop"` | A truncated reply arrived looking complete |
| Only the final message returned | *"Let me check the literature"* vanished before search results |
| Option parser scanned forward with a latching flag | Everything after the first `[n]` was deleted |

The third is the worst. Stage 2 discusses numbered literature, so
`"As Smith [1] showed, this is settled."` turned that sentence into a
**clickable button** and dropped the rest of the message.

Also fixed: the chat called `res.json()` on whatever came back, so a proxy
timeout returning an HTML error page produced *"Network error: The string did
not match the expected pattern"* — a parser message about a pattern the user
never wrote. And the tool loop was `while True` with no cap, and the model call
had no timeout.

---

## 4. Data quality

### DePaul's site footer inside 729 research summaries
`10b410a`, `049e47e`

The bio scrape took a page container that included the footer. One faculty
member's entire research summary was their research area, the university's
postal address, and then another research area.

**Excision, not truncation** — on 45 records the footer sits in the *middle*,
and cutting from it onward drops half of somebody's research.

> **A fix that caused a worse bug.** A blanket "under 25 characters is useless"
> rule was added to clear the debris. Once the footer padding came off it
> deleted every genuinely short answer: *Screenwriting*, *Irish History*,
> *Computer Science*. Caught in the run output, restored from backup, replaced
> with a rule that distinguishes a short real answer from a torn-out fragment.

### 37 real people deleted on every build
`6ef1e75`

`_dedupe` merged distinct faculty who shared an inbox, or whose scraped email
was garbage. Rebuilt around identity: an identifying email, shared-inbox
detection, and a dual email/name index.

### Other data fixes

- **`81bb736`** — section headings matched inside prose, so bios landed in the
  research-interests slot. Anchored to line starts.
- **`ee597c4`** — 224 records had entire paragraphs as "topic chips". Fixed,
  then 167 survived because the merge preserves prior enrichment; needed
  targeted clearing.
- **`e78e344`** — inline markup shattered biographies mid-sentence.
- **`80975d5`, `38b3427`** — a full-time professor was missing entirely, and an
  email DePaul had changed. Produced a one-person recovery tool and a
  one-command roster refresh.
- **`b19d6ae`** — OpenAlex returned "not found" when the real problem was an
  exhausted daily budget. Retrying into a wall, reported as absence of data.

Current state: **1,440 faculty, 1,211 with a bio, 18,681 papers, 0 records with
page furniture.**

---

## 5. Schema

### Migrations that ran before the table they altered
`e6a83e1`

All four `ALTER TABLE projects` calls sat **above** the `CREATE TABLE`. On an
existing database the columns were already there, so nothing looked wrong. On a
fresh one the ALTER hit a table that did not exist, was swallowed by a bare
`except sqlite3.OperationalError: pass`, and produced an install missing four
columns. **It could only ever have broken a first deploy.**

**Prevention:** the guard helper re-raises anything that is not "duplicate
column name", and a structural test parses the source to assert no migration
appears before its own `CREATE TABLE`.

### Four hand-maintained column lists
`5f1d6a9`

The proposal's columns were written out in four places, kept in index order
against a `dict(zip(...))`. A section inserted anywhere but the end would have
shifted every later column's contents by one, silently. Now derived from one
list; a mutation test confirms reordering is harmless.

---

## 6. What is still open

| | |
|---|---|
| **Nobody has used it** | 594 tests prove instructions are *present*, not that the advisor *behaves*. Both real bot messages reviewed so far had problems. |
| **One truncated message undiagnosed** | Three causes ruled out; the stored transcript was never retrieved. |
| **Replies drifting long** | 160 words of methodology before a question, and an em dash the send check bans. The nineteen lenses may be the cause. |
| **961 of 1,440 faculty have no publications** | In the directory, nearly invisible to matching. |
| **OpenAlex budget** | $0.10/day unauthenticated. A free key raises it to $1/day; Stage 2 makes several searches per novelty test. |
| **Hosting undecided** | 512 MB is marginal. Fly.io is configured at 1 GB for ~$6/mo; DePaul IT may host it for nothing. |
| **Bamshad's spec contradicts itself** | His instruction sheet promises "potential avenues for exploration"; his lens 18 forbids proposing. Currently resolved as *only on explicit request*, which was a judgment call, not a decision. |

---

## 7. Practices that came out of this

1. **Make the failure loud before fixing the behaviour.** Most bugs here
   returned a wrong answer cheerfully.
2. **Mutation-test the guard.** Reintroduce the bug and confirm the new test
   fails. Several tests in this repo were written, passed, and only earned
   trust at that step.
3. **Measure before concluding.** The seeding transaction, the option-block
   theory, and the token-cap theory were all confidently wrong.
4. **Test the config, not just the code.** `DATA_DIR` matching a mount path,
   two host files agreeing on a model, a declared field having a migration —
   these break silently and in production only.
5. **Prompts are code.** They are versioned, assembled from files, and covered
   by structural tests, because a deleted rule is invisible until a professor
   notices.
