# Research Proposal Chat: Structured Proposal-Building Before Matching

## Problem

Today, `/advisor` (`web_app.py`, `_advisor_system_prompt` at `web_app.py:523-574`) runs a
quick conversational intake — at most one round of 2-3 questions — before calling
`search_faculty` and returning matches. That's a reasonable minimum, but it means
faculty are matched against a thin picture: whatever the researcher typed in their
one-line `project_description` plus 1-2 quick answers. There's no room to actually
develop the research idea — no back-and-forth on methodology options, no place to
capture hypotheses or related work, no structured artifact that reflects the
research the way an actual proposal would.

The [profile expansion feature](2026-07-09-profile-expansion-design.md) already made
the researcher's own profile richer (bio, research interests). This feature makes the
*research idea itself* richer before matching happens, by having the advisor's
existing chat build a structured research proposal — background, objectives, research
questions/hypotheses, related work, methodology, expected outcomes — through natural
conversation, then use that proposal (not just a one-line description) as the basis for
suggesting AI approaches and finding collaborators.

## Goals

- Replace the advisor's current quick-intake phase with a deeper, structured
  conversation that builds a research proposal across six sections, while still
  feeling like a normal chat (not a rigid form) — the researcher can push back,
  ask for alternative methodologies, add hypotheses, or request related-work
  suggestions at any point.
- Persist the finished (or evolving) proposal as structured data — separate
  fields per section — linked to the researcher's profile.
- After the proposal is built (or a vague-answer fallback triggers), the
  conversation proceeds exactly as it does today: 3-4 concrete AI-integration
  suggestions, then `search_faculty` with up to 10 results.
- Preserve a lightweight fallback: if the researcher gives vague/short answers
  across a couple of exchanges, skip the full proposal and fall back to today's
  quick suggestion menu — the same accommodation the advisor already makes, just
  triggered by "unwilling/unable to go deep on the full proposal" rather than
  "vague on the first two questions."
- Add a simple, read-only "View your research proposal" panel to `/advisor` once
  a proposal exists.

## Non-goals

- No dedicated proposal-builder page or step separate from `/advisor` — this
  extends the existing advisor chat in place.
- No editing UI for the proposal — the view panel is read-only; changes happen
  by continuing the conversation (the LLM can re-call the save tool to update
  it).
- No literature search / citation verification — when the LLM suggests related
  work, it draws on its own training knowledge, not a live search. This is
  disclosed to the researcher as suggestions to verify, not authoritative
  citations.
- No automated testing of the LLM conversation itself (there is none today for
  the existing advisor tool-calling loop either, since it requires a live
  `CHATBOT_MODEL`/API key) — only the new DB-level pieces (schema, upsert, GET
  endpoint) get automated tests. The conversation flow itself is verified
  manually.
- No versioning/history of proposal edits — each save overwrites the one row
  for that profile.

## Design

### 1. Data model

New `proposals` table, one row per profile (upserted, not versioned):

```sql
CREATE TABLE IF NOT EXISTS proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id          INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    background          TEXT,
    objectives          TEXT,
    research_questions  TEXT,
    related_work        TEXT,
    methodology         TEXT,
    expected_outcomes   TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(profile_id)
)
```

Unlike `faculty_overrides` (which had to key on email because `faculty.id` churns
across pipeline re-imports), `profiles` is pure user-generated data never touched
by any pipeline script — keying on `profile_id` directly is safe here.

### 2. Conversation flow — replacing the intake in `_advisor_system_prompt`

The current "CONVERSATION FLOW" section (`web_app.py:554-569`) is replaced:

- **Greeting** is unchanged: reference the researcher's bio/project from their
  profile.
- **Building the proposal**: the LLM works through background, objectives,
  research questions/hypotheses, related work, and methodology conversationally.
  It should build on what's already in `bio`/`project_description` rather than
  re-asking for things already known. It can:
  - Suggest 2-3 candidate methodological approaches and let the researcher
    react/pick/critique (mirroring the "Methodology" section of a real proposal).
  - Suggest related literature from its own knowledge when relevant, framed
    explicitly as suggestions the researcher should verify, not citations to
    trust blindly.
  - Accept hypotheses, additional context, or expansions to any section at any
    point in the conversation, not just when first asked.
- **Vague-answer fallback** (preserved behavior, retargeted): if the researcher's
  answers stay short/uncertain across a couple of exchanges, the LLM skips the
  full proposal — offers today's quick suggestion menu instead — and proceeds
  straight to suggestions + `search_faculty` without ever calling
  `save_proposal`.
- **Saving**: once background, objectives, research questions, and methodology
  are reasonably clear, the LLM calls `save_proposal` (related_work and
  expected_outcomes are a bonus, not blocking — see required-fields below). It
  may call `save_proposal` again later in the same conversation if the proposal
  evolves.
- **After saving (or after the fallback triggers)**: unchanged from today — 3-4
  concrete AI-integration suggestions, then `search_faculty`, exactly as
  currently implemented.

### 3. New tool: `save_proposal`

Added alongside the existing `search_faculty` in `_ADVISOR_TOOLS`
(`web_app.py:577-597`):

```python
{
    "type": "function",
    "function": {
        "name": "save_proposal",
        "description": (
            "Save the structured research proposal once you and the researcher "
            "have worked through it together. Can be called again later in the "
            "conversation to update it as it evolves."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "background": {"type": "string", "description": "The research problem/context, 2-4 sentences."},
                "objectives": {"type": "string", "description": "The primary research objectives."},
                "research_questions": {"type": "string", "description": "Research questions and/or hypotheses being tested."},
                "related_work": {"type": "string", "description": "Relevant related work/literature discussed."},
                "methodology": {"type": "string", "description": "The chosen or discussed methodological approach(es)."},
                "expected_outcomes": {"type": "string", "description": "What the research is expected to produce or show."}
            },
            "required": ["background", "objectives", "research_questions", "methodology"]
        }
    }
}
```

### 4. Backend: handling the tool call in `api_advisor_chat`

`api_advisor_chat`'s existing tool-call loop (`web_app.py`, currently only
branches on `search_faculty`) gets a second branch: when
`tc.function.name == "save_proposal"`, parse the arguments and upsert into
`proposals` keyed by `profile_id`:

```sql
INSERT INTO proposals
    (profile_id, background, objectives, research_questions, related_work, methodology, expected_outcomes, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(profile_id) DO UPDATE SET
    background = excluded.background,
    objectives = excluded.objectives,
    research_questions = excluded.research_questions,
    related_work = excluded.related_work,
    methodology = excluded.methodology,
    expected_outcomes = excluded.expected_outcomes,
    updated_at = excluded.updated_at
```

The tool result fed back to the LLM is a small confirmation, e.g.
`{"status": "saved"}`, so the conversation continues naturally. If `profile_id`
is missing from the request (shouldn't happen in practice — the advisor page
requires a profile to start a session), the write is skipped and the tool
result is `{"status": "error", "reason": "no profile"}` rather than crashing the
request.

### 5. New endpoint + view panel

`GET /api/profile/{profile_id}/proposal` returns the six fields (empty strings
if no row exists yet — absence is the normal case, not a 404, matching the
`faculty-overrides` endpoint's convention).

In `advisor.html`, after each assistant turn completes, the client makes a
follow-up `fetch` to this endpoint. If any field is non-empty, a "View your
research proposal" toggle appears near the existing profile bar; expanding it
shows the six sections read-only, labeled the same as the tool's field
descriptions. No editing affordance — changes happen by continuing the chat.

## Error handling

- `save_proposal` called with a missing optional field (`related_work`,
  `expected_outcomes`) → stored as empty string, not an error.
- `save_proposal` called without a valid `profile_id` → skipped, tool result
  reports the error back to the LLM rather than raising a server error.
- `GET /api/profile/{profile_id}/proposal` for a profile with no saved proposal
  → returns empty-string defaults for all six fields, status 200.
- Multiple `save_proposal` calls in one conversation → each upsert cleanly
  overwrites the previous values for that profile.
- The LLM never calls `save_proposal` (model doesn't support tool use well, or
  the conversation ends early) → no proposal row exists; the view panel simply
  never appears. This mirrors the existing, accepted risk profile of
  `search_faculty` in the current advisor.

## Testing plan

- Automated (mirroring the profile-expansion feature's test style — direct
  async calls with a fake request, monkeypatched `DB_PATH`):
  - `proposals` table creation is idempotent and has the expected columns.
  - The tool-call handler upserts correctly on first save and on a second save
    with different values (confirming the overwrite behavior).
  - The tool-call handler skips the write and returns an error result when
    `profile_id` is missing/invalid, without raising.
  - `GET /api/profile/{profile_id}/proposal` returns empty defaults when no row
    exists, and the stored values when one does.
- Manual: a full `/advisor` conversation walkthrough — describe a research
  problem, work through methodology options, add a hypothesis mid-conversation,
  confirm the proposal panel appears and reflects what was discussed; confirm
  the conversation still proceeds to concrete suggestions and faculty matches
  afterward.
- Manual: give deliberately vague/short answers and confirm the advisor falls
  back to today's quick suggestion menu and proceeds to matches without ever
  showing a proposal panel.
