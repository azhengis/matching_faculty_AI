# Profile Expansion: Research Interests, Manual Entry, and Faculty Self-Edit

## Problem

The `profiles` table (`web_app.py`, created ~line 65-84) already holds more than
just publications — `name`, `email`, `bio_text`, `project_description`,
`confirmed_paper_ids` — but two gaps limit it:

1. There's no structured way to capture research interests. `bio_text` is free
   text only; nothing is tag-shaped, so nothing can be displayed as chips or
   easily reused when the research-proposal chat (a separate, later feature)
   pulls context from a profile.
2. Step 1 of the profile wizard (`templates/profile.html:163-175`,
   `searchFaculty()`) requires finding yourself in the `faculty` table via name
   search. There is no path for someone not in that table — and no way for
   someone who *is* in the table to actually improve what's shown about them.
   `bio_text` is silently copied from the scraped `faculty.research_summary`
   (`selectFaculty()`, `profile.html:362` area) and is never fed back — so a
   faculty member's self-written bio only ever powers their own advisor
   session, never what other users see when matching against them.

## Goals

- Add a structured `research_interests` tag field to `profiles`.
- Let someone not found in the `faculty` table create a profile manually
  (no `faculty_id` link).
- Let someone who *is* found in the `faculty` table edit their own bio and
  research interests, and have that edit visible to everyone else who
  searches/matches against them — not just stored for their own session.
- Do this without risking corruption of the pipeline-scraped faculty data:
  self-edits must survive (and never be overwritten by) a re-run of
  `pipeline/4_db_setup.py`, which does `DELETE FROM faculty` and re-inserts
  every row from `data/depaul_faculty_enriched.json` on every run.

## Non-goals

- No login/auth system (explicitly deferred). Editing a faculty record's
  bio/interests is not gated behind identity verification in this pass.
- No immediate SPECTER2 re-embedding of edited bios. Self-edits affect display
  text, keyword scoring, cross-encoder input, and LLM reranking (all of which
  read plain text at query time) but do not trigger a new embedding — that
  stays a pipeline-time concern for later.
- No new fields beyond research interest tags (no role/affiliation/link
  fields this round).
- No public "faculty profile page" view — self-edits enhance existing search
  result displays (baseline/search/chat/advisor), not a new page.
- No change to `POST /api/profile/extract-file` or Step 3 (project
  description) — those are unchanged from the prior redesign
  (`2026-07-08-profile-project-description-design.md`).

## Design

### 1. Data model

**`profiles` table** — one additive column:

```sql
ALTER TABLE profiles ADD COLUMN research_interests TEXT DEFAULT '[]';
```

JSON array of tag strings, same convention as `confirmed_paper_ids`.
Non-destructive; existing rows default to `'[]'`.

**New `faculty_overrides` table**, created alongside the existing `profiles`
table setup in `web_app.py`:

```sql
CREATE TABLE IF NOT EXISTS faculty_overrides (
    email                    TEXT PRIMARY KEY,
    self_bio                 TEXT,
    self_research_interests  TEXT DEFAULT '[]',
    self_editor_email        TEXT,
    updated_at               TEXT DEFAULT (datetime('now'))
)
```

Keyed by the faculty member's **lowercased email**, not `faculty.id`. This is
deliberate: `faculty.id` is an `AUTOINCREMENT` primary key that gets
reassigned every time `4_db_setup.py` re-runs (`DELETE FROM faculty` followed
by a fresh `INSERT` of every row from the JSON — see `pipeline/4_db_setup.py:59-64`),
so any foreign key into `faculty.id` recorded today can point at a different
person (or nobody) after the next pipeline refresh. Email is the one
reasonably stable natural key already present in the scraped data. Keeping
overrides in their own table (rather than new columns on `faculty`) also means
a pipeline re-import can never touch or wipe them, and the original scraped
`research_summary` is never overwritten in place — self-edits are purely an
overlay that can be cleared by truncating one table if needed.

`self_editor_email` and `updated_at` exist purely as an audit trail (who
touched this record, when) since there's no verification gate — consistent
with the app's current no-login, testing-phase posture.

### 2. Step 1: "Not listed? Continue manually"

Below the existing name search box in `templates/profile.html`, add a link:
**"Not listed? Continue manually →"**. Clicking it reveals two inline inputs
(name, email) and a "Continue" button that skips straight to Step 2 in
**manual mode**, setting `_selected = null` (so `saveProfile()`'s existing
`faculty_id: _selected ? _selected.id : null` logic naturally sends `null`).

### 3. Step 2: "Confirm & enrich your bio" (renamed from "Confirm your publications")

Forks by mode:

- **Matched mode** (came from search + "This is me"): on entering Step 2,
  `GET /api/profile/faculty-overrides/{email}` (new endpoint, see below) fetches
  any existing override for that email and pre-fills:
  - A bio textarea, pre-filled with `self_bio` if present, else the faculty
    record's scraped bio (today's behavior).
  - A tag input for research interests, pre-filled with
    `self_research_interests` if present, else empty.
  - The existing publications checklist, unchanged.
- **Manual mode**: same bio textarea (empty) + tag input (empty), no
  publications section — replaced with the existing note about adding
  context in the project description instead.

Tag input: simple client-side chip control — type, press Enter/comma to add a
tag, click × to remove. Trimmed and de-duplicated client-side before save. No
fixed taxonomy — free text tags.

### 4. Save behavior (`POST /api/profile/save`)

Extends the existing endpoint (`web_app.py:375-397`):

- Always writes `bio_text` and the new `research_interests` (JSON-encoded)
  into `profiles`, as it does today for the existing fields.
- If `faculty_id` is present (matched mode), **also** upserts
  `faculty_overrides` keyed by that faculty record's email:

  ```sql
  INSERT INTO faculty_overrides (email, self_bio, self_research_interests, self_editor_email, updated_at)
  VALUES (?, ?, ?, ?, datetime('now'))
  ON CONFLICT(email) DO UPDATE SET
      self_bio = excluded.self_bio,
      self_research_interests = excluded.self_research_interests,
      self_editor_email = excluded.self_editor_email,
      updated_at = excluded.updated_at
  ```

  `self_editor_email` is the profile's own `email` field — no separate input.
- If a faculty record has a blank email (a handful of scraped records do),
  the override upsert is skipped silently; the requester-side profile save
  still succeeds. This is a known, accepted limitation — those faculty can't
  be self-edited until/unless their email is filled in by the pipeline.

### 5. New endpoint: `GET /api/profile/faculty-overrides/{email}`

Returns `{"self_bio": "...", "self_research_interests": [...]}` (empty
string/array if no row exists) so Step 2 can pre-fill the matched-mode form
when a user re-enters the wizard for someone already overridden by a prior
edit (their own or someone else's, e.g. a colleague who edited it first).

### 6. Matching & display integration

All faculty text used for search, ranking, and explanation flows through one
function: `load_faculty()` in `search.py:227-266`. It builds `p["research_summary"]`
once per record; every downstream consumer (`explain_match`, `first_sentence`,
Stage 1 keyword scoring, Stage 2 cross-encoder pairs at `search.py:756`, Stage
3 LLM rerank prompt at `search.py:786`) reads that same field. This is the
single integration point:

- After the existing `SELECT * FROM faculty` query, also load
  `faculty_overrides` and build a lookup keyed by lowercased email.
- For each person, look up their override by `p["email"]` (lowercased,
  trimmed):
  - If `self_bio` is non-empty, it **replaces** the base text entirely —
    skip `fix_summary`/course-merging for that record, since self-authored
    text is already clean prose and doesn't need the scraped-bio heuristics.
  - If `self_research_interests` is non-empty, prepend a line before the
    summary, mirroring the existing "Courses taught:" convention
    (`search.py:255`): `f"Research interests: {', '.join(interests)}\n\n{summary}"`.
    This makes interests flow into keyword scoring, the cross-encoder, and
    the LLM reranker automatically, with no changes to those functions.
- If a faculty record has no override row, behavior is byte-for-byte
  identical to today.

## Data flow summary

```
Step 1 (search)          ──"This is me"──▶ Step 2, matched mode
Step 1 ("Not listed?")   ──manual entry──▶ Step 2, manual mode
                                              │
                              (matched mode fetches existing override
                               via GET /api/profile/faculty-overrides/{email})
                                              ▼
                          bio textarea + interest tags (+ papers if matched)
                                              ▼
                          POST /api/profile/save
                            │                              │
                            ▼                              ▼
                   profiles.bio_text,              faculty_overrides upsert
                   profiles.research_interests      (only if faculty_id present
                                                      and email is non-blank)
                                                              │
                                                              ▼
                                              load_faculty() merges override
                                              into research_summary at query time
                                                              │
                                                              ▼
                                    search / chat / advisor results reflect
                                    the self-edited bio + interests
```

## Error handling

- `GET /api/profile/faculty-overrides/{email}`: no matching row → return
  empty defaults (`{"self_bio": "", "self_research_interests": []}`), not a
  404 — absence of an override is the normal case, not an error.
- `POST /api/profile/save` with `faculty_id` present but that faculty's email
  is blank: override upsert is skipped; response is unchanged (still returns
  `{profile_id, name}`) since the requester-side save itself succeeded.
- Manual mode name/email inputs: same non-empty `name` validation the
  endpoint already enforces (`web_app.py:385-386`); email is optional, as it
  is today.
- Research interest tags: no server-side validation beyond JSON-encoding the
  list; malformed client input is impossible since the tag control only ever
  produces an array of trimmed strings.

## Testing plan

- Manual: search for an existing faculty member, edit their bio and add 2-3
  interest tags, save. Confirm both `profiles` and `faculty_overrides` rows
  are written correctly.
- Manual: re-enter the wizard for the same person (fresh profile), confirm
  Step 2 pre-fills from the existing `faculty_overrides` row rather than the
  original scraped bio.
- Manual: use "Not listed? Continue manually", fill in bio + interests +
  project description, save. Confirm a `profiles` row is created with
  `faculty_id = NULL` and no `faculty_overrides` row is touched.
- Manual: search/chat/advisor for the edited faculty member from a different
  "session" (different profile), confirm the self-edited bio and interest
  tags appear in results and match explanations.
- Pipeline re-run safety: create an override, run `python3
  pipeline/4_db_setup.py`, confirm the `faculty_overrides` row is untouched
  and still joins correctly by email even though `faculty.id` values shifted.
- Manual: attempt an override for a faculty record with a blank email,
  confirm the profile save still succeeds and no crash/500 occurs.
