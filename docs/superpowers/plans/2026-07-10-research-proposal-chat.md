# Research Proposal Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the advisor's quick 2-3 question intake with a structured, conversational proposal-building phase (background, objectives, research questions, related work, methodology, expected outcomes), persisted via a new `save_proposal` tool call, with a simple read-only view panel on `/advisor`.

**Architecture:** The existing `/advisor` chat (`web_app.py`'s `_advisor_system_prompt` + `api_advisor_chat`) already runs an LLM tool-calling loop with one tool, `search_faculty`. This plan adds a second tool, `save_proposal`, and rewrites the system prompt's conversation-flow instructions so the LLM builds a structured proposal before moving to (unchanged) AI-integration suggestions and faculty search. The proposal is stored in a new `proposals` table (one row per profile, upserted). A new `GET /api/profile/{profile_id}/proposal` endpoint powers a read-only view panel added to `templates/advisor.html`.

**Tech Stack:** FastAPI + `sqlite3` + `litellm` (all already in use, no new dependencies) + vanilla JS.

**Design spec:** `docs/superpowers/specs/2026-07-10-research-proposal-chat-design.md`

## Global Constraints

- No new dependencies — sqlite3/json/FastAPI/litellm only.
- `proposals` is keyed by `profile_id` directly (not email) — unlike `faculty_overrides`, `profiles` is pure user data never touched by any pipeline re-import, so there's no id-churn risk here (spec Design §1).
- One proposal row per profile — each `save_proposal` call upserts, it does not create a new row or a new version (spec Non-goals).
- Refinement over the spec's literal "each upsert cleanly overwrites" language: when the LLM's `save_proposal` call omits an optional field (`related_work`, `expected_outcomes`) that was set on a prior call, the prior value must be preserved, not wiped to empty — otherwise an incremental update (e.g. adding a hypothesis to `research_questions` later in the conversation) would silently erase unrelated sections the LLM didn't repeat. `background`, `objectives`, `research_questions`, and `methodology` are the tool's `required` fields, so the LLM is expected to supply them every call; `related_work`/`expected_outcomes` are the ones this merge behavior protects.
- No dedicated proposal-builder page — this extends the existing `/advisor` chat in place (spec Non-goals).
- No editing UI — the view panel is read-only; changes happen by continuing the conversation (spec Non-goals).
- No automated test exists (or is expected) for the LLM conversation itself — this is true today for the existing `search_faculty` tool loop too, since it requires a live `CHATBOT_MODEL`. Automated tests cover only the DB-level pieces (schema, upsert/merge logic, the new GET endpoint); the conversation flow and system prompt wording are verified manually (spec Non-goals / Testing plan).
- The live app process runs via `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000` with `reload=False` — must be restarted after backend/template changes to take effect.

---

### Task 1: DB schema — `proposals` table

**Files:**
- Modify: `web_app.py:65-106` (`_init_profiles_db`)
- Test: `tests/test_proposals_db_schema.py`

**Interfaces:**
- Produces: new `proposals` table: `id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, background TEXT, objectives TEXT, research_questions TEXT, related_work TEXT, methodology TEXT, expected_outcomes TEXT, created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')), UNIQUE(profile_id)`. Created/idempotent via `web_app._init_profiles_db()`, which Task 2 relies on having already run.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proposals_db_schema.py`:

```python
import sqlite3

import pytest

import web_app


def _columns(con, table):
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def test_init_profiles_db_creates_proposals_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    cols = _columns(con, "proposals")
    assert cols == [
        "id", "profile_id", "background", "objectives", "research_questions",
        "related_work", "methodology", "expected_outcomes", "created_at", "updated_at",
    ]
    con.close()


def test_init_profiles_db_proposals_table_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()
    web_app._init_profiles_db()  # must not raise on second run

    con = sqlite3.connect(db_path)
    cols = _columns(con, "proposals")
    assert cols.count("profile_id") == 1
    con.close()


def test_proposals_table_enforces_one_row_per_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Test Person')")
    con.execute("INSERT INTO proposals (profile_id, background) VALUES (1, 'first')")
    con.commit()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO proposals (profile_id, background) VALUES (1, 'second')")
        con.commit()
    con.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_proposals_db_schema.py -v`
Expected: FAIL on all three — the `proposals` table doesn't exist yet (`sqlite3.OperationalError: no such table: proposals`).

- [ ] **Step 3: Update `_init_profiles_db()` in `web_app.py`**

The current function (`web_app.py:65-106`) ends with:

```python
    # Self-edited overlay for faculty-facing profile editing. Keyed by email,
    # not faculty.id, because pipeline/4_db_setup.py deletes and re-inserts
    # every faculty row on each run, reassigning AUTOINCREMENT ids. Keeping
    # this in its own table (rather than columns on `faculty`) means a
    # pipeline re-import can never touch or wipe a self-edit, and the
    # original scraped research_summary is never overwritten in place.
    con.execute("""
        CREATE TABLE IF NOT EXISTS faculty_overrides (
            email                    TEXT PRIMARY KEY,
            self_bio                 TEXT,
            self_research_interests  TEXT DEFAULT '[]',
            self_editor_email        TEXT,
            updated_at               TEXT DEFAULT (datetime('now'))
        )
    """)
    con.commit()
    con.close()
```

Replace it with:

```python
    # Self-edited overlay for faculty-facing profile editing. Keyed by email,
    # not faculty.id, because pipeline/4_db_setup.py deletes and re-inserts
    # every faculty row on each run, reassigning AUTOINCREMENT ids. Keeping
    # this in its own table (rather than columns on `faculty`) means a
    # pipeline re-import can never touch or wipe a self-edit, and the
    # original scraped research_summary is never overwritten in place.
    con.execute("""
        CREATE TABLE IF NOT EXISTS faculty_overrides (
            email                    TEXT PRIMARY KEY,
            self_bio                 TEXT,
            self_research_interests  TEXT DEFAULT '[]',
            self_editor_email        TEXT,
            updated_at               TEXT DEFAULT (datetime('now'))
        )
    """)

    # One structured research proposal per profile, built up by the advisor
    # chat's save_proposal tool. Keyed by profile_id (not email like
    # faculty_overrides) because `profiles` is pure user data never touched
    # by any pipeline re-import, so there's no id-churn risk here.
    con.execute("""
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
    """)
    con.commit()
    con.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_proposals_db_schema.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 24 passed (21 pre-existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add web_app.py tests/test_proposals_db_schema.py
git commit -m "$(cat <<'EOF'
Add proposals table for structured research proposals

One row per profile (UNIQUE on profile_id, upserted rather than
versioned). Keyed by profile_id directly, unlike faculty_overrides,
since profiles are pure user data never touched by a pipeline
re-import.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Advisor — `save_proposal` tool, system prompt rewrite, proposal lookup endpoint

**Files:**
- Modify: `web_app.py:1-22` (docstring route list), `web_app.py:523-597` (`_advisor_system_prompt`, `_ADVISOR_TOOLS`), `web_app.py` (new `_save_proposal` helper, new route, and the tool-dispatch loop inside `api_advisor_chat`)
- Test: `tests/test_advisor_proposal.py`

**Interfaces:**
- Consumes: `proposals` table (Task 1).
- Produces: `_save_proposal(profile_id, args: dict) -> dict` — upserts (with merge-on-omit for optional fields) and returns `{"status": "saved"}`, or `{"status": "error", "reason": "no profile"}` if `profile_id` doesn't parse to an int. New route `api_profile_proposal(profile_id: int) -> JSONResponse` at `GET /api/profile/{profile_id}/proposal`, returning the six fields (empty strings if no row). `_ADVISOR_TOOLS` gains a `save_proposal` tool definition alongside the existing `search_faculty`. `api_advisor_chat`'s tool-dispatch loop now branches by `tc.function.name`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_advisor_proposal.py`:

```python
import asyncio
import json
import sqlite3

import web_app
from web_app import _save_proposal, api_profile_proposal


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


def test_save_proposal_upserts_and_preserves_omitted_optional_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane Doe')")
    con.commit()
    con.close()

    result = _save_proposal(1, {
        "background": "AI ethics in imagery.",
        "objectives": "Understand consent issues.",
        "research_questions": "How does AI challenge consent?",
        "related_work": "Manovich, Paglen.",
        "methodology": "Historical analysis + case studies.",
        "expected_outcomes": "A framework for ethical guidelines.",
    })
    assert result == {"status": "saved"}

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT background, objectives, research_questions, related_work, methodology, expected_outcomes "
        "FROM proposals WHERE profile_id = 1"
    ).fetchone()
    con.close()
    assert row == (
        "AI ethics in imagery.", "Understand consent issues.",
        "How does AI challenge consent?", "Manovich, Paglen.",
        "Historical analysis + case studies.", "A framework for ethical guidelines.",
    )

    # Second call omits related_work/expected_outcomes — they must be
    # preserved from the first call, not wiped to empty.
    result2 = _save_proposal(1, {
        "background": "Updated background.",
        "objectives": "Understand consent issues.",
        "research_questions": "How does AI challenge consent, revisited?",
        "methodology": "Historical analysis + case studies.",
    })
    assert result2 == {"status": "saved"}

    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT background, research_questions, related_work, expected_outcomes FROM proposals WHERE profile_id = 1"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "Updated background."
    assert rows[0][1] == "How does AI challenge consent, revisited?"
    assert rows[0][2] == "Manovich, Paglen."
    assert rows[0][3] == "A framework for ethical guidelines."


def test_save_proposal_skips_write_when_profile_id_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    result = _save_proposal(None, {"background": "test"})
    assert result == {"status": "error", "reason": "no profile"}

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    con.close()
    assert count == 0


def test_get_proposal_returns_empty_defaults_when_none_saved(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    response = _run(api_profile_proposal(1))
    assert response.status_code == 200
    assert _body(response) == {
        "background": "", "objectives": "", "research_questions": "",
        "related_work": "", "methodology": "", "expected_outcomes": "",
    }


def test_get_proposal_returns_saved_values(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (2, 'John Smith')")
    con.execute(
        "INSERT INTO proposals (profile_id, background, objectives, research_questions, methodology) "
        "VALUES (2, 'bg', 'obj', 'rq', 'method')"
    )
    con.commit()
    con.close()

    response = _run(api_profile_proposal(2))
    assert response.status_code == 200
    body = _body(response)
    assert body["background"] == "bg"
    assert body["objectives"] == "obj"
    assert body["research_questions"] == "rq"
    assert body["methodology"] == "method"
    assert body["related_work"] == ""
    assert body["expected_outcomes"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_advisor_proposal.py -v`
Expected: FAIL — `ImportError: cannot import name '_save_proposal' from 'web_app'` (neither `_save_proposal` nor `api_profile_proposal` exist yet).

- [ ] **Step 3: Update the module docstring route list**

In `web_app.py`, the docstring (`web_app.py:16-22`) currently reads:

```python
  POST /api/profile/search      — fuzzy name search in faculty DB
  GET  /api/profile/papers/{id} — papers already on file for a faculty member
  POST /api/profile/save        — create/update profile
  GET  /api/profile/{id}        — load a saved profile
  GET  /api/profile/faculty-overrides/{email} — existing self-edit overlay for a faculty email
  POST /api/profile/extract-file — extract text from an uploaded .pdf/.docx
  POST /api/advisor/chat        — personalized advisor chat turn
```

Replace with:

```python
  POST /api/profile/search      — fuzzy name search in faculty DB
  GET  /api/profile/papers/{id} — papers already on file for a faculty member
  POST /api/profile/save        — create/update profile
  GET  /api/profile/{id}        — load a saved profile
  GET  /api/profile/faculty-overrides/{email} — existing self-edit overlay for a faculty email
  POST /api/profile/extract-file — extract text from an uploaded .pdf/.docx
  GET  /api/profile/{id}/proposal — saved research proposal for a profile, if any
  POST /api/advisor/chat        — personalized advisor chat turn
```

- [ ] **Step 4: Rewrite the CONVERSATION FLOW section of `_advisor_system_prompt`**

In `web_app.py:554-569`, the current section reads:

```python
━━━ CONVERSATION FLOW ━━━
• First message: Greet {name} by name. Reference their research area from their bio — show you read it. If they described a project, acknowledge it specifically. If not, ask: "What research problem are you currently working on?"

• Ask AT MOST one round of 2-3 focused intake questions before searching — never more than one round, even if answers are vague:
  - What kind of data do you have or could you collect? (text, images, surveys, sensor data, records...)
  - What is the core methodological challenge you are facing?
  - Are you looking for a technical co-investigator, or a consultant on AI methods?

• If their answers are vague or uncertain ("not sure", "I don't know", short non-answers), do NOT ask another round of clarifying questions. Instead, immediately pivot: offer a short menu of 3-4 broad, generally-applicable AI/data-science possibilities for their field (e.g. "text/document analysis," "predictive modeling from existing records," "survey or interview data analysis," "automating a manual review process") so they have something concrete to react to instead of another open question. Ask which sounds closest to what they need, then proceed — don't wait for a perfectly specific answer.

• Once you understand their needs (or once you've offered the fallback menu above), give 3-4 CONCRETE AI integration suggestions. Name actual methods — topic modeling, computer vision, NLP, predictive modeling, network analysis, etc. — and explain why each fits this specific research.

• Then call search_faculty. IMPORTANT: craft the query around the AI/DATA SKILLS needed, not the subject domain.
  Example: for a researcher studying political polarization via surveys who needs ML help, search:
  "machine learning natural language processing survey analysis text classification sentiment"
  NOT "political polarization sociology."

• Return up to 10 results. For each person, explain specifically what they bring to this collaboration — what method or expertise they have that matches the researcher's need.
```

Replace with:

```python
━━━ CONVERSATION FLOW ━━━
• First message: Greet {name} by name. Reference their research area from their bio — show you read it. If they described a project, acknowledge it specifically. If not, ask: "What research problem are you currently working on?"

• Work with {name} to build out a structured research proposal, using their bio and project description above as your starting material — don't re-ask for things you already know. Cover, conversationally (not as a rigid checklist):
  - Background: the problem and its context.
  - Objectives: what they're trying to find out or build.
  - Research questions / hypotheses: what specifically they're testing or investigating. Invite them to add more at any point.
  - Related work: when relevant, suggest literature or prior approaches you're aware of that connect to their work — always frame these as suggestions for them to verify, not citations to take on faith, since you don't have live literature search.
  - Methodology: suggest 2-3 candidate approaches when there's a real choice to be made, and let them react, pick one, or push back with their own idea.
  This should feel like a real conversation — let {name} steer, revisit earlier sections, or add detail at any point, not just when first asked.

• If {name}'s answers stay vague or uncertain ("not sure", "I don't know", short non-answers) across a couple of exchanges, do NOT keep pressing for a full proposal. Instead, pivot: offer a short menu of 3-4 broad, generally-applicable AI/data-science possibilities for their field (e.g. "text/document analysis," "predictive modeling from existing records," "survey or interview data analysis," "automating a manual review process") so they have something concrete to react to. Ask which sounds closest, then proceed straight to the AI integration suggestions below — skip the full proposal and do not call save_proposal.

• Once background, objectives, research questions, and methodology are reasonably clear, call save_proposal with the sections you've worked out together (related_work and expected_outcomes are a bonus — include them if you have them, but don't hold up saving for them). If the proposal keeps evolving later in the conversation (a new hypothesis, a refined methodology), call save_proposal again to update it.

• Once you understand their needs (whether from the full proposal or the fallback menu), give 3-4 CONCRETE AI integration suggestions. Name actual methods — topic modeling, computer vision, NLP, predictive modeling, network analysis, etc. — and explain why each fits this specific research.

• Then call search_faculty. IMPORTANT: craft the query around the AI/DATA SKILLS needed, not the subject domain.
  Example: for a researcher studying political polarization via surveys who needs ML help, search:
  "machine learning natural language processing survey analysis text classification sentiment"
  NOT "political polarization sociology."

• Return up to 10 results. For each person, explain specifically what they bring to this collaboration — what method or expertise they have that matches the researcher's need.
```

- [ ] **Step 5: Add the `save_proposal` tool to `_ADVISOR_TOOLS`**

In `web_app.py:577-597`, the current definition reads:

```python
_ADVISOR_TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_faculty",
        "description": (
            "Search DePaul faculty for AI/data science collaborators. "
            "Query must describe the TECHNICAL SKILLS needed, not the subject domain. "
            "Returns up to 10 results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "AI/data science skills the collaborator should have."},
                "mode":  {"type": "string", "enum": ["semantic", "complementary"],
                          "description": "semantic = find these specific skills (default). complementary = adjacent fields."}
            },
            "required": ["query"]
        }
    }
}]
```

Replace with:

```python
_ADVISOR_TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_faculty",
        "description": (
            "Search DePaul faculty for AI/data science collaborators. "
            "Query must describe the TECHNICAL SKILLS needed, not the subject domain. "
            "Returns up to 10 results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "AI/data science skills the collaborator should have."},
                "mode":  {"type": "string", "enum": ["semantic", "complementary"],
                          "description": "semantic = find these specific skills (default). complementary = adjacent fields."}
            },
            "required": ["query"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "save_proposal",
        "description": (
            "Save the structured research proposal once you and the researcher have "
            "worked through it together. Can be called again later in the conversation "
            "to update it as it evolves."
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
}]
```

- [ ] **Step 6: Add the `_save_proposal` helper function**

Add this function in `web_app.py` immediately after `_advisor_search` (which ends at `web_app.py:663` with `return {"error": str(e), "results": []}`), before `@app.post("/api/advisor/chat")`:

```python
_PROPOSAL_FIELDS = [
    "background", "objectives", "research_questions",
    "related_work", "methodology", "expected_outcomes",
]


def _save_proposal(profile_id, args: dict) -> dict:
    """Upsert the structured research proposal for a profile.

    Merges over any existing row rather than overwriting wholesale: if the
    LLM omits an optional field (related_work/expected_outcomes) on a later
    call, the previously-saved value for that field is preserved rather than
    wiped to empty.
    """
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return {"status": "error", "reason": "no profile"}

    con = sqlite3.connect(DB_PATH)
    existing = con.execute(
        "SELECT background, objectives, research_questions, related_work, methodology, expected_outcomes "
        "FROM proposals WHERE profile_id = ?", (pid,)
    ).fetchone()
    existing_values = dict(zip(_PROPOSAL_FIELDS, existing)) if existing else {f: "" for f in _PROPOSAL_FIELDS}

    values = {
        field: (args[field].strip() if field in args and args[field] is not None else existing_values[field])
        for field in _PROPOSAL_FIELDS
    }

    con.execute(
        """INSERT INTO proposals
               (profile_id, background, objectives, research_questions, related_work, methodology, expected_outcomes, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(profile_id) DO UPDATE SET
               background = excluded.background,
               objectives = excluded.objectives,
               research_questions = excluded.research_questions,
               related_work = excluded.related_work,
               methodology = excluded.methodology,
               expected_outcomes = excluded.expected_outcomes,
               updated_at = excluded.updated_at""",
        (pid, values["background"], values["objectives"], values["research_questions"],
         values["related_work"], values["methodology"], values["expected_outcomes"])
    )
    con.commit()
    con.close()
    return {"status": "saved"}
```

- [ ] **Step 7: Add the new `GET /api/profile/{profile_id}/proposal` endpoint**

Add this route in `web_app.py` immediately after `api_profile_faculty_overrides` (which ends at `web_app.py:518` with `return JSONResponse({"self_bio": self_bio or "", "self_research_interests": interests})`), before the `# ── Advisor ───` comment:

```python
@app.get("/api/profile/{profile_id}/proposal")
async def api_profile_proposal(profile_id: int):
    """Load the saved research proposal for a profile, if any."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT background, objectives, research_questions, related_work, methodology, expected_outcomes "
        "FROM proposals WHERE profile_id = ?", (profile_id,)
    ).fetchone()
    con.close()
    if not row:
        return JSONResponse({
            "background": "", "objectives": "", "research_questions": "",
            "related_work": "", "methodology": "", "expected_outcomes": "",
        })
    background, objectives, research_questions, related_work, methodology, expected_outcomes = row
    return JSONResponse({
        "background": background or "", "objectives": objectives or "",
        "research_questions": research_questions or "", "related_work": related_work or "",
        "methodology": methodology or "", "expected_outcomes": expected_outcomes or "",
    })
```

- [ ] **Step 8: Update the tool-dispatch loop in `api_advisor_chat`**

In `web_app.py`, the current tool-dispatch loop reads:

```python
            for tc in msg.tool_calls:
                args   = json.loads(tc.function.arguments)
                result = _advisor_search(query=args.get("query", ""), mode=args.get("mode", "semantic"))
                tool_entry = {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
                history.append(tool_entry); messages.append(tool_entry)
```

Replace with:

```python
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                if tc.function.name == "save_proposal":
                    result = _save_proposal(profile_id, args)
                else:
                    result = _advisor_search(query=args.get("query", ""), mode=args.get("mode", "semantic"))
                tool_entry = {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)}
                history.append(tool_entry); messages.append(tool_entry)
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_advisor_proposal.py -v`
Expected: 4 passed.

- [ ] **Step 10: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 28 passed (21 pre-existing + 3 Task 1 + 4 Task 2).

- [ ] **Step 11: Commit**

```bash
git add web_app.py tests/test_advisor_proposal.py
git commit -m "$(cat <<'EOF'
Add save_proposal tool and rewrite advisor conversation flow

Replaces the advisor's quick 2-3 question intake with a structured
proposal-building conversation (background, objectives, research
questions, related work, methodology). The LLM calls the new
save_proposal tool to persist it, mergeable across repeated calls so
optional fields aren't wiped by a later update that omits them. Vague
answers still fall back to today's quick suggestion menu. Adds
GET /api/profile/{id}/proposal to read it back.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend — read-only proposal view panel on `/advisor`

**Files:**
- Modify: `templates/advisor.html` (CSS, HTML, JS)

**Interfaces:**
- Consumes: `GET /api/profile/{profile_id}/proposal` (Task 2).
- Produces: no new interfaces consumed by other files — this is the outermost layer.

No automated test exists for this file (no JS test runner in this repo, matching the pattern from the profile-expansion feature) — verification is a manual, in-browser walkthrough plus the curl-based checks described below.

- [ ] **Step 1: Add proposal panel CSS**

In `templates/advisor.html`, immediately after the `.btn-bar-link:hover{color:var(--navy)}` rule (`templates/advisor.html:43`), add:

```css
.proposal-panel{background:var(--surface);border-bottom:1.5px solid var(--border);padding:16px 24px;font-size:13.5px;color:var(--ink-2);line-height:1.6}
.proposal-section{margin-bottom:12px}
.proposal-section:last-child{margin-bottom:0}
.proposal-section-label{font-weight:700;color:var(--ink);margin-bottom:3px}
```

- [ ] **Step 2: Add the view-panel toggle button and panel markup**

In `templates/advisor.html:139-146`, the current profile bar reads:

```html
<!-- Profile bar (injected once profile loads) -->
<div id="profile-bar" style="display:none" class="profile-bar">
  <span class="profile-bar-name" id="pb-name"></span>
  <span class="profile-bar-meta" id="pb-meta"></span>
  <div class="profile-bar-actions">
    <button class="btn-bar-link" onclick="updateProject()">Update my project</button>
    <button class="btn-bar-link" onclick="window.location='/profile'">Edit profile</button>
  </div>
</div>
```

Replace with:

```html
<!-- Profile bar (injected once profile loads) -->
<div id="profile-bar" style="display:none" class="profile-bar">
  <span class="profile-bar-name" id="pb-name"></span>
  <span class="profile-bar-meta" id="pb-meta"></span>
  <div class="profile-bar-actions">
    <button class="btn-bar-link" onclick="updateProject()">Update my project</button>
    <button class="btn-bar-link" onclick="window.location='/profile'">Edit profile</button>
    <button class="btn-bar-link" id="btn-view-proposal" style="display:none" onclick="toggleProposalPanel()">View your research proposal</button>
  </div>
</div>

<!-- Research proposal panel (shown once a proposal exists) -->
<div id="proposal-panel" class="proposal-panel" style="display:none"></div>
```

- [ ] **Step 3: Add proposal state and refresh the toggle after each successful reply**

In `templates/advisor.html:180-184`, the current state block reads:

```js
// ── State ─────────────────────────────────────────────────────────────────────
let _profileId  = null;
let _sessionId  = null;
let _busy       = false;
let _profile    = null;
```

Replace with:

```js
// ── State ─────────────────────────────────────────────────────────────────────
let _profileId  = null;
let _sessionId  = null;
let _busy       = false;
let _profile    = null;
let _proposal   = null;
let _proposalPanelOpen = false;
```

In `templates/advisor.html:259-265`, the current success branch of `callAdvisorAPI` reads:

```js
    if (data.error) {
      addBotRaw('<div style="color:#9B2020;font-size:13.5px;padding:2px 0">' +
        esc(data.error) + '</div>');
    } else {
      if (data.session_id) _sessionId = data.session_id;
      renderBotReply(data.reply || '');
    }
```

Replace with:

```js
    if (data.error) {
      addBotRaw('<div style="color:#9B2020;font-size:13.5px;padding:2px 0">' +
        esc(data.error) + '</div>');
    } else {
      if (data.session_id) _sessionId = data.session_id;
      renderBotReply(data.reply || '');
      checkProposal();
    }
```

- [ ] **Step 4: Add the proposal panel functions**

In `templates/advisor.html`, immediately after `saveUpdatedProject()`'s closing brace (the function ending at `templates/advisor.html:464` with `}`), before the `// ── Input events` comment (`templates/advisor.html:466`), add:

```js
// ── Research proposal panel ────────────────────────────────────────────────────
const PROPOSAL_LABELS = {
  background: 'Background',
  objectives: 'Objectives',
  research_questions: 'Research Questions',
  related_work: 'Related Work',
  methodology: 'Methodology',
  expected_outcomes: 'Expected Outcomes',
};

async function checkProposal() {
  if (!_profileId) return;
  try {
    const res  = await fetch('/api/profile/' + _profileId + '/proposal');
    const data = await res.json();
    _proposal = data;
    const hasContent = Object.keys(PROPOSAL_LABELS).some(k => (data[k] || '').trim());
    document.getElementById('btn-view-proposal').style.display = hasContent ? 'inline' : 'none';
    if (_proposalPanelOpen) renderProposalPanel();
  } catch (e) {
    // Network hiccup — the toggle just won't appear/update this turn.
  }
}

function renderProposalPanel() {
  const panel = document.getElementById('proposal-panel');
  const sections = Object.keys(PROPOSAL_LABELS)
    .map(k => {
      const val = (_proposal && _proposal[k]) ? _proposal[k].trim() : '';
      if (!val) return '';
      return `<div class="proposal-section"><div class="proposal-section-label">${esc(PROPOSAL_LABELS[k])}</div><div>${esc(val)}</div></div>`;
    })
    .join('');
  panel.innerHTML = sections || '<em>No proposal saved yet.</em>';
}

function toggleProposalPanel() {
  _proposalPanelOpen = !_proposalPanelOpen;
  document.getElementById('proposal-panel').style.display = _proposalPanelOpen ? 'block' : 'none';
  if (_proposalPanelOpen) renderProposalPanel();
}
```

- [ ] **Step 5: Restart the running server**

The live server runs with `reload=False`, so it won't pick up the `web_app.py` changes from Task 2 or this template change automatically.

Find and stop the current process:
```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
```

Restart it in the background:
```bash
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
```

Verify it came up clean:
```bash
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; last line resembles `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 6: Curl-based verification of the new endpoint**

```bash
curl -s http://localhost:8000/api/profile/999999/proposal
```
Expected: `{"background":"","objectives":"","research_questions":"","related_work":"","methodology":"","expected_outcomes":""}` (no matching profile — empty defaults, status 200, no error).

- [ ] **Step 7: Manual verification in the browser**

If `CHATBOT_MODEL` is configured (check `echo $CHATBOT_MODEL` on the server), open `http://localhost:8000/advisor` with an existing profile and walk through:

1. Describe a research problem in enough depth that the advisor starts asking about objectives, research questions, or methodology rather than jumping straight to suggestions.
2. Continue the conversation through a couple of exchanges — react to a methodology suggestion, add a hypothesis.
3. Confirm the "View your research proposal" link appears in the profile bar at some point (this fires right after the turn where the LLM calls `save_proposal`).
4. Click it, confirm the panel shows the sections discussed so far, and collapses/expands correctly on repeated clicks.
5. Continue the conversation further (e.g. add another hypothesis) and confirm the panel's content updates on the next turn without needing a page reload.
6. Confirm the conversation still proceeds to concrete AI-integration suggestions and a `search_faculty` call with results afterward, matching today's existing behavior.
7. Separately, start a fresh conversation and give deliberately vague/short answers ("not sure", "I don't know") — confirm the advisor falls back to today's quick suggestion menu, proceeds to suggestions + matches, and the proposal panel never appears (no `save_proposal` call).

If `CHATBOT_MODEL` is not configured, note in the commit/report that this step was skipped and why — the endpoint/panel wiring can still be sanity-checked via Step 6's curl command and by reading the JS.

- [ ] **Step 8: Commit**

```bash
git add templates/advisor.html
git commit -m "$(cat <<'EOF'
Add read-only research proposal panel to the advisor page

Shows the saved proposal's sections once save_proposal has been
called, refreshed after each assistant turn. No editing — changes
happen by continuing the conversation.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review notes

- **Spec coverage:** `proposals` table schema (Task 1); conversation-flow rewrite covering background/objectives/research questions/related work/methodology, vague-answer fallback preserved, save-then-continue-to-suggestions behavior (Task 2 Step 4); `save_proposal` tool definition with required/optional fields matching the design (Task 2 Step 5); tool-call handling with the merge-on-omit refinement explicitly called out (Task 2 Step 6); new GET endpoint (Task 2 Step 7); tool-dispatch branching (Task 2 Step 8); read-only view panel refreshed after each turn (Task 3) — all covered.
- **No placeholders:** every step has complete, runnable code, exact SQL, or exact commands with expected output.
- **Type/name consistency checked:** `_PROPOSAL_FIELDS` order matches the column order in Task 1's `CREATE TABLE` and Task 2's `SELECT`/`INSERT` statements; `_save_proposal` and `api_profile_proposal` names match between Task 2's implementation and its test's imports; the six field names (`background`, `objectives`, `research_questions`, `related_work`, `methodology`, `expected_outcomes`) are identical across the DB schema, the tool's JSON schema, the GET endpoint's response, and the frontend's `PROPOSAL_LABELS` keys in Task 3.
