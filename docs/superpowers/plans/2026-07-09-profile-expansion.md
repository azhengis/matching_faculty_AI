# Profile Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured research-interest tags to the requester profile, let someone not found in the `faculty` table create a profile manually, and let a faculty member self-edit their own bio/interests so the edit is visible to everyone matching against them — not just their own advisor session.

**Architecture:** One new SQLite table, `faculty_overrides`, keyed by lowercased faculty email (not `faculty.id`, which churns on every pipeline re-import) holds self-edited bio/interests as an overlay that never touches the pipeline-scraped `faculty` table. `web_app.py`'s existing `POST /api/profile/save` upserts into it whenever a profile is linked to a real faculty record; a new `GET /api/profile/faculty-overrides/{email}` lets the wizard pre-fill a prior edit. `search.py`'s `load_faculty()` — the single choke point all matching/ranking/explanation code reads faculty text through — merges the overlay in at load time, so every consumer (cross-encoder, LLM reranker, `explain_match`) picks it up with no changes to those functions. `templates/profile.html` gets a manual-entry path for Step 1 and an editable bio + tag input for Step 2 (replacing the previous silent bio copy-through).

**Tech Stack:** FastAPI + `sqlite3` + vanilla JS (all already in use). No new dependencies.

**Design spec:** `docs/superpowers/specs/2026-07-09-profile-expansion-design.md`

## Global Constraints

- No login/auth system — self-edits are open, unverified, tracked only via `self_editor_email`/`updated_at` audit columns (spec Non-goals).
- No immediate SPECTER2 re-embedding on self-edit save — the overlay affects display text, keyword scoring, cross-encoder input, and LLM reranking (all read plain text at query time), not the cached embeddings (spec Non-goals / Design §6).
- `faculty_overrides` is keyed by lowercased `email`, not `faculty.id`, because `pipeline/4_db_setup.py` does `DELETE FROM faculty` + re-`INSERT` on every run, reassigning `AUTOINCREMENT` ids (spec Design §1).
- The scraped `faculty.research_summary` column is never overwritten in place — self-edits live only in `faculty_overrides` (spec Goals).
- No new profile fields this round beyond research-interest tags (no role/affiliation/link fields) (spec Non-goals).
- No new "faculty profile page" view — the overlay only changes what already-existing search/chat/advisor result displays show (spec Non-goals).
- The live app process runs via `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000` with `reload=False` (confirmed running as PID visible via `ps aux | grep uvicorn`) — must be restarted after backend/template changes to take effect.

---

### Task 1: DB schema — `profiles.research_interests` + `faculty_overrides` table

**Files:**
- Modify: `web_app.py:64-85` (`_init_profiles_db`)
- Test: `tests/test_profiles_db_schema.py`

**Interfaces:**
- Produces: `profiles` table gains a `research_interests TEXT DEFAULT '[]'` column. New `faculty_overrides` table: `email TEXT PRIMARY KEY, self_bio TEXT, self_research_interests TEXT DEFAULT '[]', self_editor_email TEXT, updated_at TEXT DEFAULT (datetime('now'))`. Both created/altered by `web_app._init_profiles_db()`, which Task 2 and Task 4 both rely on having already run.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profiles_db_schema.py`:

```python
import sqlite3

import web_app


def _columns(con, table):
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def test_init_profiles_db_adds_research_interests_column(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    assert "research_interests" in _columns(con, "profiles")
    con.close()


def test_init_profiles_db_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()
    web_app._init_profiles_db()  # must not raise on second run

    con = sqlite3.connect(db_path)
    assert _columns(con, "profiles").count("research_interests") == 1
    con.close()


def test_init_profiles_db_creates_faculty_overrides_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    cols = _columns(con, "faculty_overrides")
    assert cols == ["email", "self_bio", "self_research_interests", "self_editor_email", "updated_at"]
    con.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_profiles_db_schema.py -v`
Expected: FAIL on all three — `test_init_profiles_db_adds_research_interests_column` fails because the column doesn't exist yet; the `faculty_overrides` test fails because the table doesn't exist (`sqlite3.OperationalError: no such table`).

- [ ] **Step 3: Update `_init_profiles_db()` in `web_app.py`**

Replace the current function (`web_app.py:65-85`):

```python
def _init_profiles_db():
    """Create profiles table if it doesn't exist.
    Designed to accept a user_id / auth column later without migration pain.
    """
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id           INTEGER REFERENCES faculty(id) ON DELETE SET NULL,
            name                 TEXT NOT NULL,
            email                TEXT,
            bio_text             TEXT,
            project_description  TEXT,
            confirmed_paper_ids  TEXT DEFAULT '[]',
            created_at           TEXT DEFAULT (datetime('now')),
            updated_at           TEXT DEFAULT (datetime('now'))
            -- future columns: user_id INTEGER, auth_provider TEXT
        )
    """)
    con.commit()
    con.close()
```

with:

```python
def _init_profiles_db():
    """Create profiles table if it doesn't exist.
    Designed to accept a user_id / auth column later without migration pain.
    """
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id           INTEGER REFERENCES faculty(id) ON DELETE SET NULL,
            name                 TEXT NOT NULL,
            email                TEXT,
            bio_text             TEXT,
            project_description  TEXT,
            confirmed_paper_ids  TEXT DEFAULT '[]',
            created_at           TEXT DEFAULT (datetime('now')),
            updated_at           TEXT DEFAULT (datetime('now'))
            -- future columns: user_id INTEGER, auth_provider TEXT
        )
    """)
    try:
        con.execute("ALTER TABLE profiles ADD COLUMN research_interests TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass  # column already exists from a prior run

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_profiles_db_schema.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add web_app.py tests/test_profiles_db_schema.py
git commit -m "$(cat <<'EOF'
Add research_interests column and faculty_overrides table

profiles gains a research_interests TEXT column (JSON array of tags).
New faculty_overrides table holds faculty self-edits, keyed by email
rather than faculty.id so it survives pipeline/4_db_setup.py's
delete-and-reinsert refresh cycle.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend — persist interests, upsert overlay, add lookup endpoint

**Files:**
- Modify: `web_app.py:5-21` (docstring route list), `web_app.py:375-397` (`api_profile_save`), `web_app.py:425-450` (`api_profile_get`), new route after `api_profile_get`
- Test: `tests/test_faculty_overrides.py`

**Interfaces:**
- Consumes: `web_app._init_profiles_db()` (Task 1) must have run against the same `DB_PATH` before these functions are called.
- Produces: `POST /api/profile/save` now accepts `research_interests: list[str]` in its JSON body and persists it; when `faculty_id` is present and that faculty's `email` column is non-blank, it also upserts `faculty_overrides`. New route `api_profile_faculty_overrides(email: str) -> JSONResponse` at `GET /api/profile/faculty-overrides/{email}`, returning `{"self_bio": str, "self_research_interests": list[str]}` (empty defaults if no row). `GET /api/profile/{profile_id}` (`api_profile_get`) now also returns `research_interests` in its response body.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_faculty_overrides.py`:

```python
import asyncio
import json
import sqlite3

import web_app
from web_app import api_profile_save, api_profile_faculty_overrides, api_profile_get


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _make_db(tmp_path):
    db_path = tmp_path / "test_faculty.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, research_summary TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary) VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Studies AI.')"
    )
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary) VALUES (2, 'No Email Guy', '', 'Studies things.')"
    )
    con.commit()
    con.close()
    return db_path


def test_save_profile_persists_research_interests_and_upserts_override(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": 1,
        "name": "Jane Doe",
        "email": "jane@depaul.edu",
        "bio_text": "I study reinforcement learning.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["reinforcement learning", "robotics"],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200
    profile_id = _body(response)["profile_id"]

    con = sqlite3.connect(db_path)
    profile_row = con.execute(
        "SELECT bio_text, research_interests FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    assert profile_row[0] == "I study reinforcement learning."
    assert json.loads(profile_row[1]) == ["reinforcement learning", "robotics"]

    override_row = con.execute(
        "SELECT self_bio, self_research_interests, self_editor_email FROM faculty_overrides WHERE email = ?",
        ("jane@depaul.edu",)
    ).fetchone()
    assert override_row[0] == "I study reinforcement learning."
    assert json.loads(override_row[1]) == ["reinforcement learning", "robotics"]
    assert override_row[2] == "jane@depaul.edu"
    con.close()


def test_save_profile_skips_override_when_faculty_id_is_none(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": None,
        "name": "Manual Person",
        "email": "manual@example.com",
        "bio_text": "I am not in the directory.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["ethics"],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM faculty_overrides").fetchone()[0]
    assert count == 0
    con.close()


def test_save_profile_skips_override_when_faculty_email_is_blank(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": 2,
        "name": "No Email Guy",
        "email": "",
        "bio_text": "I study things.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": [],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200  # profile save still succeeds

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM faculty_overrides").fetchone()[0]
    assert count == 0
    con.close()


def test_faculty_overrides_endpoint_returns_defaults_when_no_row(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    response = _run(api_profile_faculty_overrides("nobody@example.com"))
    assert response.status_code == 200
    assert _body(response) == {"self_bio": "", "self_research_interests": []}


def test_faculty_overrides_endpoint_returns_existing_row_case_insensitively(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "I study RL.", json.dumps(["reinforcement learning"]))
    )
    con.commit()
    con.close()

    response = _run(api_profile_faculty_overrides("JANE@DEPAUL.EDU"))
    assert response.status_code == 200
    body = _body(response)
    assert body["self_bio"] == "I study RL."
    assert body["self_research_interests"] == ["reinforcement learning"]


def test_get_profile_includes_research_interests(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    save_response = _run(api_profile_save(_FakeRequest({
        "faculty_id": 1,
        "name": "Jane Doe",
        "email": "jane@depaul.edu",
        "bio_text": "I study reinforcement learning.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["reinforcement learning"],
    })))
    profile_id = _body(save_response)["profile_id"]

    get_response = _run(api_profile_get(profile_id))
    assert get_response.status_code == 200
    assert _body(get_response)["research_interests"] == ["reinforcement learning"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_faculty_overrides.py -v`
Expected: FAIL — `ImportError: cannot import name 'api_profile_faculty_overrides' from 'web_app'`, plus (once that's fixed in isolation) the `research_interests`/override assertions would fail since the current `api_profile_save`/`api_profile_get` don't handle that field yet.

- [ ] **Step 3: Update the module docstring route list**

In `web_app.py`, the docstring (`web_app.py:16-21`) currently reads:

```python
  POST /api/profile/search      — fuzzy name search in faculty DB
  GET  /api/profile/papers/{id} — papers already on file for a faculty member
  POST /api/profile/save        — create/update profile
  GET  /api/profile/{id}        — load a saved profile
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
  POST /api/advisor/chat        — personalized advisor chat turn
```

- [ ] **Step 4: Update `api_profile_save` in `web_app.py:375-397`**

Replace:

```python
@app.post("/api/profile/save")
async def api_profile_save(req: Request):
    """Create a new profile. Returns profile_id stored in browser localStorage."""
    body         = await req.json()
    faculty_id   = body.get("faculty_id")
    name         = (body.get("name") or "").strip()
    email        = (body.get("email") or "").strip()
    bio_text     = (body.get("bio_text") or "").strip()
    project_desc = (body.get("project_description") or "").strip()
    paper_ids    = json.dumps(body.get("confirmed_paper_ids", []))
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        """INSERT INTO profiles
           (faculty_id, name, email, bio_text, project_description, confirmed_paper_ids, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
        (faculty_id, name, email, bio_text, project_desc, paper_ids)
    )
    profile_id = cur.lastrowid
    con.commit()
    con.close()
    return JSONResponse({"profile_id": profile_id, "name": name})
```

with:

```python
@app.post("/api/profile/save")
async def api_profile_save(req: Request):
    """Create a new profile. Returns profile_id stored in browser localStorage."""
    body         = await req.json()
    faculty_id   = body.get("faculty_id")
    name         = (body.get("name") or "").strip()
    email        = (body.get("email") or "").strip()
    bio_text     = (body.get("bio_text") or "").strip()
    project_desc = (body.get("project_description") or "").strip()
    paper_ids    = json.dumps(body.get("confirmed_paper_ids", []))
    interests    = json.dumps(body.get("research_interests", []))
    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        """INSERT INTO profiles
           (faculty_id, name, email, bio_text, project_description, confirmed_paper_ids, research_interests, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (faculty_id, name, email, bio_text, project_desc, paper_ids, interests)
    )
    profile_id = cur.lastrowid
    con.commit()

    # Faculty self-edit overlay: only when this profile is linked to a real
    # faculty record with a usable email. The key is the faculty record's own
    # (authoritative) email, not the client-submitted `email` field, which is
    # only trusted as an audit note (self_editor_email).
    if faculty_id:
        row = con.execute("SELECT email FROM faculty WHERE id = ?", (faculty_id,)).fetchone()
        faculty_email = (row[0] or "").strip().lower() if row else ""
        if faculty_email:
            con.execute(
                """INSERT INTO faculty_overrides
                       (email, self_bio, self_research_interests, self_editor_email, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(email) DO UPDATE SET
                       self_bio = excluded.self_bio,
                       self_research_interests = excluded.self_research_interests,
                       self_editor_email = excluded.self_editor_email,
                       updated_at = excluded.updated_at""",
                (faculty_email, bio_text, interests, email)
            )
            con.commit()

    con.close()
    return JSONResponse({"profile_id": profile_id, "name": name})
```

- [ ] **Step 5: Add the new endpoint after `api_profile_get` (`web_app.py`, after line 450)**

```python
@app.get("/api/profile/faculty-overrides/{email}")
async def api_profile_faculty_overrides(email: str):
    """Look up any existing self-edit overlay for a faculty email, to pre-fill Step 2."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT self_bio, self_research_interests FROM faculty_overrides WHERE email = ?",
        (email.strip().lower(),)
    ).fetchone()
    con.close()
    if not row:
        return JSONResponse({"self_bio": "", "self_research_interests": []})
    self_bio, interests_json = row
    try:
        interests = json.loads(interests_json or "[]")
    except Exception:
        interests = []
    return JSONResponse({"self_bio": self_bio or "", "self_research_interests": interests})
```

- [ ] **Step 6: Update `api_profile_get` (`web_app.py:425-450`)**

Replace:

```python
@app.get("/api/profile/{profile_id}")
async def api_profile_get(profile_id: int):
    """Load a saved profile by ID."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, faculty_id, name, email, bio_text, project_description, confirmed_paper_ids "
        "FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    con.close()
    if not row:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    pid, fid, name, email, bio, proj, papers_json = row
    try:
        paper_ids = json.loads(papers_json or "[]")
    except Exception:
        paper_ids = []
    papers = []
    if paper_ids:
        con = sqlite3.connect(DB_PATH)
        ph  = ",".join("?" * len(paper_ids))
        prows = con.execute(f"SELECT id, title, year FROM papers WHERE id IN ({ph})", paper_ids).fetchall()
        con.close()
        papers = [{"id": r[0], "title": r[1] or "", "year": r[2]} for r in prows]
    return JSONResponse({"id": pid, "faculty_id": fid, "name": name, "email": email or "",
                         "bio": bio or "", "project_description": proj or "",
                         "confirmed_paper_ids": paper_ids, "papers": papers})
```

with:

```python
@app.get("/api/profile/{profile_id}")
async def api_profile_get(profile_id: int):
    """Load a saved profile by ID."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id, faculty_id, name, email, bio_text, project_description, confirmed_paper_ids, research_interests "
        "FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    con.close()
    if not row:
        return JSONResponse({"error": "Profile not found"}, status_code=404)
    pid, fid, name, email, bio, proj, papers_json, interests_json = row
    try:
        paper_ids = json.loads(papers_json or "[]")
    except Exception:
        paper_ids = []
    try:
        interests = json.loads(interests_json or "[]")
    except Exception:
        interests = []
    papers = []
    if paper_ids:
        con = sqlite3.connect(DB_PATH)
        ph  = ",".join("?" * len(paper_ids))
        prows = con.execute(f"SELECT id, title, year FROM papers WHERE id IN ({ph})", paper_ids).fetchall()
        con.close()
        papers = [{"id": r[0], "title": r[1] or "", "year": r[2]} for r in prows]
    return JSONResponse({"id": pid, "faculty_id": fid, "name": name, "email": email or "",
                         "bio": bio or "", "project_description": proj or "",
                         "confirmed_paper_ids": paper_ids, "research_interests": interests,
                         "papers": papers})
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_faculty_overrides.py -v`
Expected: 6 passed.

- [ ] **Step 8: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 18 passed (9 pre-existing + 3 from Task 1 + 6 from Task 2).

- [ ] **Step 9: Commit**

```bash
git add web_app.py tests/test_faculty_overrides.py
git commit -m "$(cat <<'EOF'
Persist research interests and upsert faculty self-edit overlay on save

POST /api/profile/save now stores research_interests and, when linked
to a real faculty record with a usable email, upserts faculty_overrides
so the edit is visible to everyone matching against that person — not
just their own advisor session. Adds GET
/api/profile/faculty-overrides/{email} to pre-fill a prior edit, and
GET /api/profile/{id} now round-trips research_interests.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `search.py` — merge the overlay into matching/display

**Files:**
- Modify: `search.py:227-266` (`load_faculty`)
- Test: `tests/test_load_faculty_overrides.py`

**Interfaces:**
- Consumes: `faculty_overrides` table (Task 1's schema; this task also defensively creates it so `load_faculty()` works even when the DB was set up without ever starting `web_app.py`).
- Produces: `load_faculty()` return value unchanged in shape (`list[dict]`), but for any person with a matching `faculty_overrides.email` row, `p["research_summary"]` reflects `self_bio` (replacing the scraped summary/course-merge logic entirely) with a prepended `"Research interests: ..."` line when `self_research_interests` is non-empty. Every downstream consumer (`explain_match`, `first_sentence`, Stage 1 keyword scoring, Stage 2 cross-encoder pairs at `search.py:756`, Stage 3 LLM rerank prompt at `search.py:786`) reads `p["research_summary"]`, so no other function needs to change.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_load_faculty_overrides.py`:

```python
import json
import sqlite3

import search as sm


def _make_db(tmp_path):
    db_path = tmp_path / "test_faculty.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, research_summary TEXT, classes_taught TEXT
        )
    """)
    con.execute("CREATE TABLE papers (faculty_id INTEGER, title TEXT)")
    con.commit()
    con.close()
    return db_path


def test_load_faculty_uses_self_bio_and_interests_when_override_exists(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Dr. Jane Doe is a professor.', '')"
    )
    con.execute("""
        CREATE TABLE faculty_overrides (
            email TEXT PRIMARY KEY, self_bio TEXT,
            self_research_interests TEXT DEFAULT '[]',
            self_editor_email TEXT, updated_at TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "I study reinforcement learning for robotics.",
         json.dumps(["reinforcement learning", "robotics"]))
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    summary = people[0]["research_summary"]
    assert summary.startswith("Research interests: reinforcement learning, robotics")
    assert "I study reinforcement learning for robotics." in summary
    assert "Dr. Jane Doe is a professor." not in summary


def test_load_faculty_unchanged_when_no_override_exists(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'John Smith', 'john@depaul.edu', 'John studies computer vision.', '')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    assert people[0]["research_summary"] == "John studies computer vision."


def test_load_faculty_override_survives_faculty_id_churn(tmp_path, monkeypatch):
    """Simulates a pipeline re-run: faculty.id changes, but the override still
    joins correctly because faculty_overrides is keyed by email, not id."""
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Old scraped bio.', '')"
    )
    con.execute("""
        CREATE TABLE faculty_overrides (
            email TEXT PRIMARY KEY, self_bio TEXT,
            self_research_interests TEXT DEFAULT '[]',
            self_editor_email TEXT, updated_at TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "My real self-written bio.", json.dumps(["nlp"]))
    )
    con.commit()

    # Simulate pipeline/4_db_setup.py's DELETE FROM faculty + re-INSERT, which
    # reassigns a new AUTOINCREMENT id even though it's the same person.
    con.execute("DELETE FROM faculty")
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (50, 'Jane Doe', 'jane@depaul.edu', 'Old scraped bio.', '')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    assert people[0]["id"] == 50
    assert "My real self-written bio." in people[0]["research_summary"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_load_faculty_overrides.py -v`
Expected: FAIL — the first and third tests fail because `load_faculty()` doesn't yet read `faculty_overrides` at all (summary stays the scraped/course-merged text).

- [ ] **Step 3: Update `load_faculty()` in `search.py:227-266`**

Replace:

```python
def load_faculty():
    if not os.path.exists(DB):
        sys.exit(f"Run db_setup.py first to create {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Load faculty who have a research summary OR courses taught
    rows = con.execute("""
        SELECT * FROM faculty
        WHERE TRIM(research_summary) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
    """).fetchall()

    # Load paper titles per faculty for keyword boosting at query time
    paper_title_rows = con.execute(
        "SELECT faculty_id, GROUP_CONCAT(title, ' | ') as titles FROM papers GROUP BY faculty_id"
    ).fetchall()
    paper_titles_by_id = {r["faculty_id"]: r["titles"] for r in paper_title_rows}
    con.close()

    people = [dict(r) for r in rows]
    for p in people:
        p["pub_titles"] = paper_titles_by_id.get(p["id"], "")

    for p in people:
        summary = fix_summary(p.get("research_summary") or "")
        courses = clean_courses(p.get("classes_taught") or "")

        if is_biographical(summary, p["name"]) and courses:
            summary = f"Courses taught: {courses}\n\n{summary}"
            p["summary_source"] = "courses"
        elif not summary and courses:
            summary = f"Courses taught: {courses}"
            p["summary_source"] = "courses"
        else:
            p["summary_source"] = "research"

        p["research_summary"] = summary

    # Drop anyone who still has nothing useful
    return [p for p in people if p["research_summary"].strip()]
```

with:

```python
def load_faculty():
    if not os.path.exists(DB):
        sys.exit(f"Run db_setup.py first to create {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    # Self-edited overlay (faculty-facing profile editing). Created here too,
    # not just in web_app.py's _init_profiles_db, so load_faculty() works
    # even when the DB was set up without ever starting the web app.
    con.execute("""
        CREATE TABLE IF NOT EXISTS faculty_overrides (
            email                    TEXT PRIMARY KEY,
            self_bio                 TEXT,
            self_research_interests  TEXT DEFAULT '[]',
            self_editor_email        TEXT,
            updated_at               TEXT DEFAULT (datetime('now'))
        )
    """)

    # Load faculty who have a research summary OR courses taught
    rows = con.execute("""
        SELECT * FROM faculty
        WHERE TRIM(research_summary) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
    """).fetchall()

    # Load paper titles per faculty for keyword boosting at query time
    paper_title_rows = con.execute(
        "SELECT faculty_id, GROUP_CONCAT(title, ' | ') as titles FROM papers GROUP BY faculty_id"
    ).fetchall()
    paper_titles_by_id = {r["faculty_id"]: r["titles"] for r in paper_title_rows}

    # Load self-edited overrides, keyed by lowercased email
    override_rows = con.execute(
        "SELECT email, self_bio, self_research_interests FROM faculty_overrides"
    ).fetchall()
    overrides_by_email = {r["email"].lower(): r for r in override_rows if r["email"]}
    con.close()

    people = [dict(r) for r in rows]
    for p in people:
        p["pub_titles"] = paper_titles_by_id.get(p["id"], "")

    for p in people:
        override = overrides_by_email.get((p.get("email") or "").strip().lower())
        self_bio = (override["self_bio"] or "").strip() if override else ""
        self_interests = json.loads(override["self_research_interests"] or "[]") if override else []

        if self_bio:
            # Self-authored text is already clean prose — skip the scraped-bio
            # heuristics (fix_summary/course-merging) and use it as-is.
            summary = self_bio
            p["summary_source"] = "research"
        else:
            summary = fix_summary(p.get("research_summary") or "")
            courses = clean_courses(p.get("classes_taught") or "")

            if is_biographical(summary, p["name"]) and courses:
                summary = f"Courses taught: {courses}\n\n{summary}"
                p["summary_source"] = "courses"
            elif not summary and courses:
                summary = f"Courses taught: {courses}"
                p["summary_source"] = "courses"
            else:
                p["summary_source"] = "research"

        if self_interests:
            summary = f"Research interests: {', '.join(self_interests)}\n\n{summary}"

        p["research_summary"] = summary

    # Drop anyone who still has nothing useful
    return [p for p in people if p["research_summary"].strip()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_load_faculty_overrides.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 21 passed (9 pre-existing + 3 Task 1 + 6 Task 2 + 3 Task 3).

- [ ] **Step 6: Commit**

```bash
git add search.py tests/test_load_faculty_overrides.py
git commit -m "$(cat <<'EOF'
Merge faculty self-edit overlay into load_faculty()

When a faculty_overrides row exists for a person's email, their
self-written bio replaces the scraped/course-merged research_summary,
and any research interest tags are prepended as a line — flowing into
keyword scoring, cross-encoder input, and LLM reranking automatically
since all of those read research_summary from the same dict. Keyed by
email so a pipeline re-run (which reassigns faculty.id) doesn't orphan
the override.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Frontend — manual entry path + bio/interests editor

**Files:**
- Modify: `templates/profile.html` (CSS, Step 1 panel, Step 2 panel, JS)

**Interfaces:**
- Consumes: `POST /api/profile/save` (now accepting `research_interests`, Task 2), `GET /api/profile/faculty-overrides/{email}` (Task 2).
- Produces: no new interfaces consumed by other files — this is the outermost layer.

No automated test exists for this file (no JS test runner in the repo, matching the prior profile-wizard plan's approach) — verification is a manual, in-browser walkthrough.

- [ ] **Step 1: Add tag-chip CSS**

In `templates/profile.html`, immediately after the `.mode-tab:hover{color:var(--navy)}` rule (`templates/profile.html:102`), add:

```css
.tag-input-wrap{display:flex;flex-wrap:wrap;gap:6px;padding:10px 12px;border:1.5px solid var(--border);background:var(--surface)}
.tag-input-wrap:focus-within{border-color:var(--navy)}
.tag-chip{display:flex;align-items:center;gap:6px;background:var(--teal-bg);color:var(--teal);border:1px solid var(--teal);padding:4px 8px;font-size:13px}
.tag-chip button{background:none;border:none;color:var(--teal);cursor:pointer;font-size:14px;line-height:1;padding:0}
#tag-input{border:none;outline:none;flex:1;min-width:160px;font-size:14px;font-family:inherit;padding:4px 0}
```

- [ ] **Step 2: Update the step indicator label**

In `templates/profile.html:153-156`, replace:

```html
    <div class="step" id="step-dot-2">
      <div class="step-num">2</div>
      <div class="step-label">Confirm papers</div>
    </div>
```

with:

```html
    <div class="step" id="step-dot-2">
      <div class="step-num">2</div>
      <div class="step-label">Your bio</div>
    </div>
```

- [ ] **Step 3: Add the manual-entry link and form to Step 1**

In `templates/profile.html:169-175`, replace:

```html
    <div class="input-row">
      <input type="text" class="text-input" id="name-input" placeholder="e.g.  Smith  or  Maria Rodriguez" />
      <button class="btn-primary" id="btn-search" onclick="searchFaculty()">Search</button>
    </div>
    <div class="search-status" id="search-status"></div>
    <div id="faculty-results"></div>
  </div>
```

with:

```html
    <div class="input-row">
      <input type="text" class="text-input" id="name-input" placeholder="e.g.  Smith  or  Maria Rodriguez" />
      <button class="btn-primary" id="btn-search" onclick="searchFaculty()">Search</button>
    </div>
    <div class="search-status" id="search-status"></div>
    <div id="faculty-results"></div>

    <div style="margin-top:14px">
      <button type="button" class="btn-tiny" id="btn-manual-entry" onclick="showManualEntry()">Not listed? Continue manually &rarr;</button>
    </div>
    <div id="manual-entry" style="display:none;margin-top:14px;padding:16px;border:1.5px solid var(--border);background:var(--surface)">
      <div class="textarea-wrap" style="margin-bottom:14px">
        <div class="field-label">Your name</div>
        <input type="text" class="text-input" id="manual-name" placeholder="e.g. Maria Rodriguez" style="width:100%">
      </div>
      <div class="textarea-wrap" style="margin-bottom:14px">
        <div class="field-label">Your email <span style="text-transform:none;font-weight:400">(optional)</span></div>
        <input type="text" class="text-input" id="manual-email" placeholder="you@depaul.edu" style="width:100%">
      </div>
      <button class="btn-primary" onclick="continueManually()">Continue &rarr;</button>
    </div>
  </div>
```

- [ ] **Step 4: Rework the Step 2 panel to add bio + interest tags and fork by mode**

In `templates/profile.html:177-203`, replace:

```html
  <!-- ── Step 2: Confirm papers ── -->
  <div id="panel-2" class="panel">
    <div class="selected-banner" id="selected-banner">
      <div><strong id="selected-name"></strong><br><span id="selected-meta" style="font-size:13px;color:var(--teal)"></span></div>
      <button class="btn-change" onclick="goToStep(1)">Not me &rarr; search again</button>
    </div>

    <div class="section-tag">Step 2 of 3</div>
    <h2>Confirm your publications</h2>
    <p class="section-desc">We found these papers in our database. Check the ones that are yours — uncheck any misattributions.</p>

    <div class="papers-section" id="papers-section">
      <div class="papers-note">Don't see all your papers? That's fine — our database pulls from OpenAlex and may be incomplete. You can always add more context in your project description.</div>
      <div class="papers-head">
        <span class="papers-head-label" id="papers-count-label">Publications</span>
        <div class="select-all-row">
          <button class="btn-tiny" onclick="toggleAll(true)">Select all</button>
          <button class="btn-tiny" onclick="toggleAll(false)">Deselect all</button>
        </div>
      </div>
      <div id="papers-list">
        <span class="spinner"></span>Loading papers...
      </div>
    </div>

    <button class="btn-primary" onclick="goToStep(3)" style="width:100%;margin-top:4px">Continue &rarr;</button>
  </div>
```

with:

```html
  <!-- ── Step 2: Confirm & enrich your bio ── -->
  <div id="panel-2" class="panel">
    <div class="selected-banner" id="selected-banner">
      <div><strong id="selected-name"></strong><br><span id="selected-meta" style="font-size:13px;color:var(--teal)"></span></div>
      <button class="btn-change" onclick="goToStep(1)">Not me &rarr; search again</button>
    </div>

    <div class="section-tag">Step 2 of 3</div>
    <h2>Confirm &amp; enrich your bio</h2>
    <p class="section-desc">This bio and these research interests are shown to others matching against you &mdash; edit them to make sure they're accurate and current.</p>

    <div class="textarea-wrap">
      <div class="field-label">Your bio</div>
      <textarea class="proj-textarea" id="bio-text" placeholder="A short description of your research background."></textarea>
    </div>

    <div class="textarea-wrap">
      <div class="field-label">Research interests</div>
      <div class="tag-input-wrap">
        <div id="tag-chips" style="display:flex;flex-wrap:wrap;gap:6px"></div>
        <input type="text" id="tag-input" placeholder="Type a topic and press Enter">
      </div>
    </div>

    <div class="papers-section" id="papers-section">
      <div class="papers-note">Don't see all your papers? That's fine — our database pulls from OpenAlex and may be incomplete. You can always add more context in your project description.</div>
      <div class="papers-head">
        <span class="papers-head-label" id="papers-count-label">Publications</span>
        <div class="select-all-row">
          <button class="btn-tiny" onclick="toggleAll(true)">Select all</button>
          <button class="btn-tiny" onclick="toggleAll(false)">Deselect all</button>
        </div>
      </div>
      <div id="papers-list">
        <span class="spinner"></span>Loading papers...
      </div>
    </div>
    <div id="papers-manual-note" style="display:none;color:var(--ink-3);font-size:13.5px;padding:12px 0">No publications to confirm for a manual profile — you can add context in your project description on the next step.</div>

    <button class="btn-primary" onclick="goToStep(3)" style="width:100%;margin-top:4px">Continue &rarr;</button>
  </div>
```

- [ ] **Step 5: Add state + manual-entry/tag/override-loading JS**

In `templates/profile.html`, the state declarations (`templates/profile.html:269-271`) currently read:

```js
let _selected  = null;   // { id, name, title, dept, email, bio }
let _paperIds  = [];     // all paper ids loaded for this faculty member
let _step      = 1;
```

Replace with:

```js
let _selected    = null;   // { id, name, title, dept, email, bio }
let _paperIds    = [];     // all paper ids loaded for this faculty member
let _step        = 1;
let _manualName  = null;   // set when the user chose "Not listed? Continue manually"
let _manualEmail = null;
let _tags        = [];     // current research interest tags
```

Then, immediately after the `selectFaculty` function (`templates/profile.html:361-372`), replace:

```js
async function selectFaculty(p) {
  _selected = p;

  // Fill step 2 and 3 banners
  document.getElementById('selected-name').textContent = p.name;
  document.getElementById('selected-meta').textContent = [p.title, p.dept].filter(Boolean).join(' · ');
  document.getElementById('s3-name').textContent = p.name;
  document.getElementById('s3-meta').textContent = [p.title, p.dept].filter(Boolean).join(' · ');

  goToStep(2);
  loadPapers(p.id);
}
```

with:

```js
async function selectFaculty(p) {
  _selected = p;
  _manualName = null;
  _manualEmail = null;

  // Fill step 2 and 3 banners
  document.getElementById('selected-name').textContent = p.name;
  document.getElementById('selected-meta').textContent = [p.title, p.dept].filter(Boolean).join(' · ');
  document.getElementById('s3-name').textContent = p.name;
  document.getElementById('s3-meta').textContent = [p.title, p.dept].filter(Boolean).join(' · ');

  setStepMode('matched');
  goToStep(2);
  loadPapers(p.id);
  loadOverrides(p.email, p.bio || '');
}

function showManualEntry() {
  document.getElementById('manual-entry').style.display = 'block';
  document.getElementById('btn-manual-entry').style.display = 'none';
}

function continueManually() {
  const name = document.getElementById('manual-name').value.trim();
  if (!name) {
    document.getElementById('search-status').textContent = 'Please enter your name to continue.';
    document.getElementById('search-status').className = 'search-status err';
    return;
  }
  _selected = null;
  _manualName = name;
  _manualEmail = document.getElementById('manual-email').value.trim();

  document.getElementById('selected-name').textContent = name;
  document.getElementById('selected-meta').textContent = 'Manual profile';
  document.getElementById('s3-name').textContent = name;
  document.getElementById('s3-meta').textContent = 'Manual profile';

  setStepMode('manual');
  goToStep(2);
  loadOverrides(null);
}

function setStepMode(mode) {
  document.getElementById('papers-section').style.display = mode === 'matched' ? 'block' : 'none';
  document.getElementById('papers-manual-note').style.display = mode === 'manual' ? 'block' : 'none';
}

async function loadOverrides(email, fallbackBio) {
  document.getElementById('bio-text').value = fallbackBio || '';
  _tags = [];
  renderTags();

  if (!email) return;

  try {
    const res  = await fetch('/api/profile/faculty-overrides/' + encodeURIComponent(email));
    const data = await res.json();
    if (data.self_bio) document.getElementById('bio-text').value = data.self_bio;
    _tags = data.self_research_interests || [];
    renderTags();
  } catch (e) {
    // No existing override or network hiccup — bio stays at the scraped fallback.
  }
}

function renderTags() {
  document.getElementById('tag-chips').innerHTML = _tags.map((t, i) => `
    <span class="tag-chip">${esc(t)}<button type="button" onclick="removeTag(${i})">&times;</button></span>
  `).join('');
}

function addTag(raw) {
  const t = raw.trim();
  if (!t) return;
  if (!_tags.some(existing => existing.toLowerCase() === t.toLowerCase())) {
    _tags.push(t);
    renderTags();
  }
}

function removeTag(i) {
  _tags.splice(i, 1);
  renderTags();
}

document.getElementById('tag-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    addTag(e.target.value);
    e.target.value = '';
  }
});
```

- [ ] **Step 6: Update `saveProfile()`'s payload**

In `templates/profile.html:511-518`, replace:

```js
  const payload = {
    faculty_id:           _selected ? _selected.id : null,
    name:                 _selected ? _selected.name : 'Unknown',
    email:                _selected ? _selected.email || '' : '',
    bio_text:             _selected ? _selected.bio || '' : '',
    project_description:  proj,
    confirmed_paper_ids:  getCheckedPaperIds(),
  };
```

with:

```js
  const payload = {
    faculty_id:           _selected ? _selected.id : null,
    name:                 _selected ? _selected.name : (_manualName || 'Unknown'),
    email:                _selected ? (_selected.email || '') : (_manualEmail || ''),
    bio_text:             document.getElementById('bio-text').value.trim(),
    project_description:  proj,
    confirmed_paper_ids:  getCheckedPaperIds(),
    research_interests:   _tags,
  };
```

- [ ] **Step 7: Restart the running server**

The live server runs with `reload=False`, so it won't pick up the `web_app.py` changes from Tasks 1-2 or these template changes automatically.

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

- [ ] **Step 8: Manual verification in the browser**

Open `http://localhost:8000/profile` and walk through:

1. Search for and select a real faculty member with a known email. Confirm Step 2 now shows an editable "Your bio" textarea pre-filled with their scraped bio, and an empty "Research interests" tag input.
2. Type over the bio, add 2-3 tags (press Enter after each), confirm/uncheck a couple of papers, continue to Step 3, fill it out, save. Confirm it redirects to `/advisor`.
3. Go to `/profile` again (via "Update my profile"), search for and select the *same* faculty member. Confirm Step 2 now pre-fills the bio and tags from the edit made in step 2 above (not the original scraped bio) — this confirms the `GET /api/profile/faculty-overrides/{email}` round-trip works.
4. Go to `/baseline`, `/search`, or `/chat` and run a query matching the new bio/interest tags you just typed (from a query you wouldn't expect to match the *original* scraped bio). Confirm the edited faculty member appears in results and the match explanation reflects the new text.
5. Go to `/profile`, search for a name that returns no results, click "Not listed? Continue manually", fill in name + email, click Continue. Confirm Step 2 shows the bio/tags editor with the "No publications to confirm..." note instead of the papers checklist.
6. Fill in bio + tags + Step 3, save. Confirm it redirects to `/advisor` and shows the manually-entered name.
7. Query the SQLite DB directly to confirm the manual profile has `faculty_id = NULL` and no matching row was added to `faculty_overrides`:
   ```bash
   sqlite3 faculty.db "SELECT faculty_id, name, research_interests FROM profiles ORDER BY id DESC LIMIT 1;"
   ```

- [ ] **Step 9: Commit**

```bash
git add templates/profile.html
git commit -m "$(cat <<'EOF'
Add manual profile entry and an editable bio/interests step

Step 1 gains a "Not listed? Continue manually" path for people not in
the faculty directory. Step 2 becomes "Confirm & enrich your bio": an
editable bio textarea (previously silently copied from the scraped
record and never actually editable) plus a research-interest tag
input, pre-filled from any prior self-edit via
GET /api/profile/faculty-overrides/{email}. Manual-mode profiles hide
the publications checklist in favor of a short note.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review notes

- **Spec coverage:** `profiles.research_interests` column + `faculty_overrides` table keyed by email (Task 1); save-time upsert with faculty-email-as-key and blank-email skip (Task 2 Step 4); `GET /api/profile/faculty-overrides/{email}` pre-fill lookup (Task 2 Step 5); `load_faculty()` merge with self_bio replacing scraped text and interests prepended, plus id-churn resilience (Task 3); Step 1 manual-entry link (Task 4 Step 3); Step 2 editable bio + tag input forking matched/manual mode (Task 4 Steps 4-5); save payload sending the editable bio and tags instead of the old silent `_selected.bio` copy (Task 4 Step 6); manual end-to-end verification including the pipeline-safety concern and the search/chat/advisor reflection check (Task 4 Step 8) — all covered.
- **No placeholders:** every step has complete, runnable code, exact SQL, or exact commands with expected output.
- **Type/name consistency checked:** `faculty_overrides` column names (`email`, `self_bio`, `self_research_interests`, `self_editor_email`, `updated_at`) match across Task 1's `CREATE TABLE`, Task 2's `INSERT`/`SELECT` statements, and Task 3's `load_faculty()` query. `api_profile_faculty_overrides` name matches between Task 2's route definition and Task 2's test import. Frontend element/state names (`bio-text`, `tag-input`, `tag-chips`, `manual-name`, `manual-email`, `manual-entry`, `btn-manual-entry`, `papers-manual-note`, `_manualName`, `_manualEmail`, `_tags`, `setStepMode`, `loadOverrides`, `renderTags`, `addTag`, `removeTag`) are consistent between Task 4's HTML and JS steps, and `research_interests` is the field name used consistently in the save payload (Task 4 Step 6), the save endpoint (Task 2 Step 4), and `load_faculty()`'s override lookup (Task 3).
