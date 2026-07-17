# Profile Documents & Advisor Topic Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user see their entire profile (bio, interests, publications, project) plus upload documents and add links, all stored server-side; and gate the advisor page's chat behind a "what would you like to talk about?" topic-block menu, with one working block (research) wired to today's existing conversation.

**Architecture:** A new `profile_documents` table holds both uploaded files and links, keyed by `profile_id`. Files are saved to a local `uploads/` directory under a randomly-generated name (never the client-submitted filename, to avoid path traversal) with PDFs/DOCX text-extracted via the existing `doc_extract.py` module. Five new endpoints (upload, add-link, list, delete, serve-file) are all gated behind `_current_user` and scoped to the caller's own profile. `templates/profile.html`'s existing "Welcome back" card is replaced with a full profile view (shown after the wizard or on return) that includes the new documents/links section. `templates/advisor.html` gets a topic-block menu between the profile bar and the chat interface — for now, a single "Tell me about my new research" block that reveals the chat and starts today's conversation unchanged.

**Tech Stack:** FastAPI + `sqlite3` (all already in use) + the existing `doc_extract.py` module — no new dependencies.

## Global Constraints

- No new third-party dependencies — reuse `doc_extract.extract_text` (already handles `.pdf`/`.docx`) for extraction; any other file type is still stored, just without extracted text.
- Uploaded files are stored under a generated filename (`secrets.token_hex(16)` + original extension), never the client-submitted filename — avoids path traversal and collisions. The original filename is kept separately for display/download purposes only.
- All new endpoints require a valid session (`_current_user`) and only ever operate on the calling user's own profile — verified via a `JOIN` against `profiles.user_id`, not a trusted client-submitted profile id.
- `uploads/` is added to `.gitignore` (matches the existing pattern for other generated/local artifacts like `faculty.db`).
- Only one topic block ("Tell me about my new research") is wired up now; the menu structure must not hardcode assumptions that block-specific system prompts vary per block — that's future work.
- The live app process runs via `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000` with `reload=False` — must be restarted after backend/template changes to take effect.

---

### Task 1: Backend — `profile_documents` table + upload/link/list/delete/serve endpoints

**Files:**
- Modify: `web_app.py:33-45` (imports), `web_app.py:57-65` (paths/state), `web_app.py:153-165` (`_init_profiles_db`), `web_app.py:1-24` (docstring), new routes after `api_profile_me` (`web_app.py:403-439` region — insert after it)
- Modify: `.gitignore`
- Test: `tests/test_profile_documents.py`

**Interfaces:**
- Consumes: `_current_user(req)` (existing), `doc_extract.extract_text(filename, content)` (existing).
- Produces: `profile_documents` table (`id, profile_id, kind, label, url, filename, stored_filename, extracted_text, created_at`). Routes: `api_profile_add_document(req, file, label)` at `POST /api/profile/documents`, `api_profile_add_link(req)` at `POST /api/profile/links`, `api_profile_list_documents(req)` at `GET /api/profile/documents`, `api_profile_delete_document(doc_id, req)` at `DELETE /api/profile/documents/{doc_id}`, `api_profile_document_file(doc_id, req)` at `GET /api/profile/documents/{doc_id}/file`. `UPLOADS_DIR` module-level constant, later consumed only by this task's own code.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profile_documents.py`:

```python
import asyncio
import io
import json
import os
import sqlite3

from docx import Document
from fastapi import UploadFile

import web_app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _init_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(web_app, "UPLOADS_DIR", str(uploads_dir))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    return db_path, uploads_dir


def _signup_and_save_profile(email="jane@depaul.edu"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    _run(web_app.api_profile_save(_FakeRequest(
        {"name": "Jane Doe", "bio_text": "bio", "project_description": "proj",
         "confirmed_paper_ids": [], "research_interests": []},
        cookies={"session_token": token}
    )))
    return token


def test_add_link_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    response = _run(web_app.api_profile_add_link(
        _FakeRequest({"label": "Site", "url": "https://x.com"}, cookies={})
    ))
    assert response.status_code == 401


def test_add_link_requires_existing_profile(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]

    response = _run(web_app.api_profile_add_link(
        _FakeRequest({"label": "Site", "url": "https://x.com"}, cookies={"session_token": token})
    ))
    assert response.status_code == 400


def test_add_link_creates_row(tmp_path, monkeypatch):
    db_path, _ = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    response = _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "Google Scholar", "url": "https://scholar.google.com/x"},
        cookies={"session_token": token}
    )))
    assert response.status_code == 200
    body = _body(response)
    assert body["kind"] == "link"
    assert body["label"] == "Google Scholar"

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT kind, label, url FROM profile_documents").fetchone()
    con.close()
    assert row == ("link", "Google Scholar", "https://scholar.google.com/x")


def test_add_document_extracts_docx_text_and_stores_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    doc = Document()
    doc.add_paragraph("My CV highlights reinforcement learning research.")
    buf = io.BytesIO()
    doc.save(buf)
    upload = UploadFile(file=io.BytesIO(buf.getvalue()), filename="cv.docx")

    response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="My CV"
    ))
    assert response.status_code == 200
    body = _body(response)
    assert body["has_text"] is True

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT label, filename, stored_filename, extracted_text FROM profile_documents"
    ).fetchone()
    con.close()
    assert row[0] == "My CV"
    assert row[1] == "cv.docx"
    assert "reinforcement learning" in row[3]
    assert os.path.exists(os.path.join(uploads_dir, row[2]))


def test_add_document_without_extractable_text_still_stores_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    upload = UploadFile(file=io.BytesIO(b"just some plain text"), filename="notes.txt")

    response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label=""
    ))
    assert response.status_code == 200
    body = _body(response)
    assert body["has_text"] is False
    assert body["label"] == "notes.txt"  # falls back to filename when no label given

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT stored_filename FROM profile_documents").fetchone()
    con.close()
    assert os.path.exists(os.path.join(uploads_dir, row[0]))


def test_list_documents_returns_both_kinds(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "Site", "url": "https://x.com"}, cookies={"session_token": token}
    )))
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")
    _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))

    response = _run(web_app.api_profile_list_documents(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    kinds = sorted(d["kind"] for d in _body(response)["documents"])
    assert kinds == ["file", "link"]


def test_delete_document_removes_row_and_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")
    add_response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))
    doc_id = _body(add_response)["id"]

    con = sqlite3.connect(db_path)
    stored_filename = con.execute(
        "SELECT stored_filename FROM profile_documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]
    con.close()
    assert os.path.exists(os.path.join(uploads_dir, stored_filename))

    response = _run(web_app.api_profile_delete_document(doc_id, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert not os.path.exists(os.path.join(uploads_dir, stored_filename))

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM profile_documents").fetchone()[0]
    con.close()
    assert count == 0


def test_delete_document_requires_ownership(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token_a = _signup_and_save_profile(email="a@depaul.edu")
    _run(web_app.api_auth_signup(_FakeRequest({"email": "b@depaul.edu", "password": "hunter222"})))
    token_b = list(web_app._auth_sessions.keys())[-1]
    _run(web_app.api_profile_save(_FakeRequest(
        {"name": "B", "bio_text": "", "project_description": "",
         "confirmed_paper_ids": [], "research_interests": []},
        cookies={"session_token": token_b}
    )))

    add_response = _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "A's site", "url": "https://a.com"}, cookies={"session_token": token_a}
    )))
    doc_id = _body(add_response)["id"]

    response = _run(web_app.api_profile_delete_document(doc_id, _FakeRequest(cookies={"session_token": token_b})))
    assert response.status_code == 404


def test_get_document_file_serves_only_owned_files(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    upload = UploadFile(file=io.BytesIO(b"hello world"), filename="notes.txt")
    add_response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))
    doc_id = _body(add_response)["id"]

    response = _run(web_app.api_profile_document_file(doc_id, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert response.path.endswith(".txt")

    response_401 = _run(web_app.api_profile_document_file(doc_id, _FakeRequest(cookies={})))
    assert response_401.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_profile_documents.py -v`
Expected: FAIL — `AttributeError: module 'web_app' has no attribute 'api_profile_add_link'` (none of these routes/the table exist yet).

- [ ] **Step 3: Update imports and add `UPLOADS_DIR`**

In `web_app.py`, the import block (`web_app.py:38-39`) currently reads:

```python
from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
```

Replace with:

```python
from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
```

The paths block (`web_app.py:57-60`) currently reads:

```python
# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT     = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(_ROOT, "faculty.db")
TEMPLATES = Path(_ROOT) / "templates"
```

Replace with:

```python
# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT       = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(_ROOT, "faculty.db")
TEMPLATES   = Path(_ROOT) / "templates"
UPLOADS_DIR = os.path.join(_ROOT, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
```

- [ ] **Step 4: Add the `profile_documents` table to `_init_profiles_db()`**

In `web_app.py`, immediately before the closing `con.commit()` / `con.close()` of `_init_profiles_db()` (`web_app.py:163-165`, right after the `users` table block), add:

```python
    # Uploaded documents and links attached to a profile (CVs, grant docs,
    # personal sites, Google Scholar, etc.) — separate from
    # confirmed_paper_ids (which references the scraped `papers` table)
    # since these are user-supplied sources, not OpenAlex-derived publications.
    con.execute("""
        CREATE TABLE IF NOT EXISTS profile_documents (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id       INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            kind             TEXT NOT NULL,
            label            TEXT,
            url              TEXT,
            filename         TEXT,
            stored_filename  TEXT,
            extracted_text   TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)
```

- [ ] **Step 5: Update the module docstring**

In `web_app.py`, change:

```python
  GET  /api/profile/me          — load the logged-in user's profile (requires login)
  GET  /api/profile/faculty-overrides/{email} — existing self-edit overlay for a faculty email (requires login)
```

to:

```python
  GET  /api/profile/me          — load the logged-in user's profile (requires login)
  POST /api/profile/documents   — upload a document attached to the profile (requires login)
  POST /api/profile/links       — add a link attached to the profile (requires login)
  GET  /api/profile/documents   — list the profile's documents/links (requires login)
  DELETE /api/profile/documents/{id} — remove a document/link (requires login)
  GET  /api/profile/documents/{id}/file — download an uploaded document (requires login)
  GET  /api/profile/faculty-overrides/{email} — existing self-edit overlay for a faculty email (requires login)
```

- [ ] **Step 6: Add the five new routes**

Add these routes in `web_app.py` immediately after `api_profile_me` (which ends around `web_app.py:439` with `"papers": papers})`), before `api_profile_faculty_overrides`:

```python
@app.post("/api/profile/documents")
async def api_profile_add_document(req: Request, file: UploadFile = File(...), label: str = Form("")):
    """Upload a document (PDF, DOCX, or any other file) attached to the logged-in user's profile."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    con = sqlite3.connect(DB_PATH)
    profile_row = con.execute("SELECT id FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()
    if not profile_row:
        con.close()
        return JSONResponse({"error": "Save your profile before adding documents."}, status_code=400)
    profile_id = profile_row[0]

    filename = file.filename or "upload"
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        con.close()
        return JSONResponse({"error": "File is too large (10MB max)."}, status_code=400)

    ext = os.path.splitext(filename)[1].lower()
    stored_filename = secrets.token_hex(16) + ext
    with open(os.path.join(UPLOADS_DIR, stored_filename), "wb") as f:
        f.write(content)

    extracted_text = None
    if ext in (".pdf", ".docx"):
        try:
            extracted_text = doc_extract.extract_text(filename, content)
        except ValueError:
            extracted_text = None  # not every file needs to yield usable text

    label = (label or "").strip() or filename
    cur = con.execute(
        """INSERT INTO profile_documents (profile_id, kind, label, filename, stored_filename, extracted_text)
           VALUES (?, 'file', ?, ?, ?, ?)""",
        (profile_id, label, filename, stored_filename, extracted_text)
    )
    doc_id = cur.lastrowid
    con.commit()
    con.close()
    return JSONResponse({
        "id": doc_id, "kind": "file", "label": label,
        "filename": filename, "has_text": extracted_text is not None,
    })


@app.post("/api/profile/links")
async def api_profile_add_link(req: Request):
    """Add a link (personal site, Google Scholar, etc.) attached to the logged-in user's profile."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    body  = await req.json()
    label = (body.get("label") or "").strip()
    url   = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "URL required"}, status_code=400)

    con = sqlite3.connect(DB_PATH)
    profile_row = con.execute("SELECT id FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()
    if not profile_row:
        con.close()
        return JSONResponse({"error": "Save your profile before adding links."}, status_code=400)
    profile_id = profile_row[0]

    cur = con.execute(
        "INSERT INTO profile_documents (profile_id, kind, label, url) VALUES (?, 'link', ?, ?)",
        (profile_id, label or url, url)
    )
    doc_id = cur.lastrowid
    con.commit()
    con.close()
    return JSONResponse({"id": doc_id, "kind": "link", "label": label or url, "url": url})


@app.get("/api/profile/documents")
async def api_profile_list_documents(req: Request):
    """List the logged-in user's uploaded documents and links."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    con = sqlite3.connect(DB_PATH)
    profile_row = con.execute("SELECT id FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()
    if not profile_row:
        con.close()
        return JSONResponse({"documents": []})
    rows = con.execute(
        "SELECT id, kind, label, url, filename, extracted_text IS NOT NULL FROM profile_documents "
        "WHERE profile_id = ? ORDER BY created_at DESC", (profile_row[0],)
    ).fetchall()
    con.close()
    documents = [{"id": r[0], "kind": r[1], "label": r[2] or "", "url": r[3] or "",
                  "filename": r[4] or "", "has_text": bool(r[5])} for r in rows]
    return JSONResponse({"documents": documents})


@app.delete("/api/profile/documents/{doc_id}")
async def api_profile_delete_document(doc_id: int, req: Request):
    """Delete a document/link, only if it belongs to the logged-in user's profile."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        """SELECT profile_documents.stored_filename FROM profile_documents
           JOIN profiles ON profiles.id = profile_documents.profile_id
           WHERE profile_documents.id = ? AND profiles.user_id = ?""",
        (doc_id, user["id"])
    ).fetchone()
    if not row:
        con.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    stored_filename = row[0]
    con.execute("DELETE FROM profile_documents WHERE id = ?", (doc_id,))
    con.commit()
    con.close()

    if stored_filename:
        try:
            os.remove(os.path.join(UPLOADS_DIR, stored_filename))
        except FileNotFoundError:
            pass

    return JSONResponse({"status": "deleted"})


@app.get("/api/profile/documents/{doc_id}/file")
async def api_profile_document_file(doc_id: int, req: Request):
    """Serve the original uploaded file, only if it belongs to the logged-in user's profile."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        """SELECT profile_documents.stored_filename, profile_documents.filename FROM profile_documents
           JOIN profiles ON profiles.id = profile_documents.profile_id
           WHERE profile_documents.id = ? AND profiles.user_id = ? AND profile_documents.kind = 'file'""",
        (doc_id, user["id"])
    ).fetchone()
    con.close()
    if not row or not row[0]:
        return JSONResponse({"error": "Not found"}, status_code=404)
    stored_filename, original_filename = row
    file_path = os.path.join(UPLOADS_DIR, stored_filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(file_path, filename=original_filename or stored_filename)
```

- [ ] **Step 7: Add `uploads/` to `.gitignore`**

Append to `.gitignore`:

```
# Uploaded profile documents (local storage, not committed)
uploads/
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_profile_documents.py -v`
Expected: 10 passed.

- [ ] **Step 9: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 65 passed (55 pre-existing + 10 new).

- [ ] **Step 10: Commit**

```bash
git add web_app.py .gitignore tests/test_profile_documents.py
git commit -m "$(cat <<'EOF'
Add profile_documents table and document/link endpoints

Uploaded files are stored under a random generated filename in a new
uploads/ directory (gitignored), with PDF/DOCX text extracted via the
existing doc_extract module. Links are stored alongside files in the
same table. All five endpoints (upload, add-link, list, delete,
serve-file) require login and are scoped to the caller's own profile.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Frontend — full profile view with document/link management

**Files:**
- Modify: `templates/profile.html` (CSS, HTML, JS)

**Interfaces:**
- Consumes: `GET /api/profile/me`, `GET /api/profile/documents`, `POST /api/profile/documents`, `POST /api/profile/links`, `DELETE /api/profile/documents/{id}` (Task 1).
- Produces: no new interfaces — outermost layer.

No automated test exists for this file (no JS test runner in this repo, matching every prior frontend task in this project) — verification is curl-based endpoint checks plus a manual walkthrough.

- [ ] **Step 1: Add CSS for the profile view and document sections**

In `templates/profile.html`, immediately after the `.btn-scarlet:hover{background:#a01828}` rule (`templates/profile.html:115`), add:

```css
.profile-summary{background:var(--surface);border:1.5px solid var(--border);padding:24px;margin-bottom:24px}
.profile-field{margin-bottom:16px}
.profile-field:last-child{margin-bottom:0}
.profile-text{font-size:14px;color:var(--ink-2);line-height:1.6;white-space:pre-wrap}
.documents-section{background:var(--surface);border:1.5px solid var(--border);padding:24px;margin-bottom:24px}
.doc-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--border)}
.doc-row:last-child{border-bottom:none}
.doc-label a{color:var(--navy);text-decoration:none;font-size:13.5px}
.doc-label a:hover{text-decoration:underline}
.doc-meta{font-size:11.5px;color:var(--ink-3);margin-top:1px}
.doc-actions button{font-size:12px;color:var(--navy);background:none;border:none;cursor:pointer;text-decoration:underline;font-family:inherit}
.doc-add-forms{display:flex;gap:16px;flex-wrap:wrap;margin-top:16px}
.doc-add-form{flex:1;min-width:240px;padding:14px;border:1px dashed var(--border-2)}
```

- [ ] **Step 2: Replace the "Welcome back" block with the full profile view**

In `templates/profile.html:139-149`, the current block reads:

```html
  <!-- Welcome back (shown if the logged-in user already has a saved profile) -->
  <div id="welcome-back" style="display:none">
    <div class="welcome-back">
      <h3 id="wb-name">Welcome back</h3>
      <p id="wb-desc">Your profile is saved. Head to the advisor to continue, or update your profile below.</p>
      <div class="btn-group">
        <a class="btn-scarlet" href="/advisor">Go to Advisor &rarr;</a>
        <button class="btn-outline" onclick="startFresh()">Update my profile</button>
      </div>
    </div>
  </div>
```

Replace with:

```html
  <!-- Full profile view (shown once the logged-in user has a saved profile) -->
  <div id="profile-view" style="display:none">
    <div class="profile-summary">
      <h2 id="pv-name"></h2>
      <div class="profile-field">
        <div class="field-label">Bio</div>
        <div id="pv-bio" class="profile-text"></div>
      </div>
      <div class="profile-field">
        <div class="field-label">Research Interests</div>
        <div id="pv-interests" class="profile-text"></div>
      </div>
      <div class="profile-field">
        <div class="field-label">Confirmed Publications</div>
        <div id="pv-papers" class="profile-text"></div>
      </div>
      <div class="profile-field">
        <div class="field-label">Current Project</div>
        <div id="pv-project" class="profile-text"></div>
      </div>
    </div>

    <div class="documents-section">
      <div class="section-tag">Documents &amp; Links</div>
      <h2>Add more sources</h2>
      <p class="section-desc">Upload CVs, papers, or grant documents, or link to your Google Scholar, personal site, or other sources.</p>

      <div id="documents-list"></div>

      <div class="doc-add-forms">
        <div class="doc-add-form">
          <div class="field-label">Upload a document</div>
          <input type="text" class="text-input" id="doc-label-input" placeholder="Label (e.g. 'CV', 'Grant proposal')" style="width:100%;margin-bottom:8px">
          <input type="file" id="doc-file-input">
          <button class="btn-primary" onclick="uploadDocument()" style="margin-top:8px">Upload</button>
          <div class="search-status" id="doc-upload-status"></div>
        </div>

        <div class="doc-add-form">
          <div class="field-label">Add a link</div>
          <input type="text" class="text-input" id="link-label-input" placeholder="Label (e.g. 'Google Scholar')" style="width:100%;margin-bottom:8px">
          <input type="text" class="text-input" id="link-url-input" placeholder="https://..." style="width:100%;margin-bottom:8px">
          <button class="btn-primary" onclick="addLink()">Add link</button>
          <div class="search-status" id="link-add-status"></div>
        </div>
      </div>
    </div>

    <div class="btn-group" style="margin-top:24px">
      <a class="btn-scarlet" href="/advisor">Continue to Advisor &rarr;</a>
      <button class="btn-outline" onclick="startFresh()">Update my profile</button>
    </div>
  </div>
```

- [ ] **Step 3: Update `init()` to show the full profile view instead of the old "Welcome back" card**

In `templates/profile.html:309-335`, the current block reads:

```js
// ── On load: require login, then check for an existing profile ───────────────
(async function init() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) throw new Error('not logged in');
  } catch (e) {
    window.location.href = '/login';
    return;
  }

  let existing = null;
  try {
    const res = await fetch('/api/profile/me');
    if (res.ok) existing = await res.json();
  } catch (e) {
    // Network hiccup — treat as no existing profile.
  }

  if (existing) {
    document.getElementById('welcome-back').style.display = 'block';
    document.getElementById('wb-name').textContent = 'Welcome back' + (existing.name ? ', ' + existing.name : '') + '!';
    document.getElementById('wb-desc').textContent =
      'Your profile is saved. Head to the advisor to continue, or update your profile below.';
    document.getElementById('steps').style.display = 'none';
    document.getElementById('panel-1').classList.remove('active');
  }
})();
```

Replace with:

```js
// ── On load: require login, then check for an existing profile ───────────────
(async function init() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) throw new Error('not logged in');
  } catch (e) {
    window.location.href = '/login';
    return;
  }

  let existing = null;
  try {
    const res = await fetch('/api/profile/me');
    if (res.ok) existing = await res.json();
  } catch (e) {
    // Network hiccup — treat as no existing profile.
  }

  if (existing) {
    showProfileView(existing);
  }
})();

function showProfileView(profile) {
  document.getElementById('steps').style.display = 'none';
  document.getElementById('panel-1').classList.remove('active');
  document.getElementById('panel-2').classList.remove('active');
  document.getElementById('panel-3').classList.remove('active');

  document.getElementById('pv-name').textContent = profile.name || '';
  document.getElementById('pv-bio').textContent = profile.bio || '(no bio yet)';
  const interests = profile.research_interests || [];
  document.getElementById('pv-interests').textContent = interests.length ? interests.join(', ') : '(none added yet)';
  const papers = profile.papers || [];
  document.getElementById('pv-papers').innerHTML = papers.length
    ? papers.map(p => `<div>${esc(p.title)}${p.year ? ' (' + p.year + ')' : ''}</div>`).join('')
    : '(none confirmed yet)';
  document.getElementById('pv-project').textContent = profile.project_description || '(not described yet)';

  document.getElementById('profile-view').style.display = 'block';
  loadDocuments();
}
```

- [ ] **Step 4: Update `startFresh()` to target the renamed view**

In `templates/profile.html:337-341`, the current function reads:

```js
function startFresh() {
  document.getElementById('welcome-back').style.display = 'none';
  document.getElementById('steps').style.display = 'flex';
  document.getElementById('panel-1').classList.add('active');
}
```

Replace with:

```js
function startFresh() {
  document.getElementById('profile-view').style.display = 'none';
  document.getElementById('steps').style.display = 'flex';
  document.getElementById('panel-1').classList.add('active');
}
```

- [ ] **Step 5: Update `saveProfile()` to land on the profile view instead of redirecting to the advisor**

In `templates/profile.html:650-673`, the current block reads:

```js
  try {
    const res  = await fetch('/api/profile/save', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.error) {
      status.textContent = 'Error: ' + data.error;
      status.className   = 'search-status err';
      btn.disabled       = false;
      return;
    }

    // Redirect to advisor
    window.location.href = '/advisor';

  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
    btn.disabled       = false;
  }
}
```

Replace with:

```js
  try {
    const res  = await fetch('/api/profile/save', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.error) {
      status.textContent = 'Error: ' + data.error;
      status.className   = 'search-status err';
      btn.disabled       = false;
      return;
    }

    // Show the full profile view (with document upload) instead of jumping
    // straight to the advisor — the user continues there when ready.
    const meRes = await fetch('/api/profile/me');
    if (meRes.ok) {
      showProfileView(await meRes.json());
    }
    btn.disabled = false;

  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
    btn.disabled       = false;
  }
}
```

- [ ] **Step 6: Add the documents/links JS functions**

In `templates/profile.html`, immediately after the `logout()` function (which ends at `templates/profile.html:678` with `}`), before the `// ── Utility ──` comment, add:

```js
// ── Documents & links ─────────────────────────────────────────────────────────
async function loadDocuments() {
  const list = document.getElementById('documents-list');
  list.innerHTML = '<span class="spinner"></span> Loading...';
  try {
    const res  = await fetch('/api/profile/documents');
    const data = await res.json();
    renderDocuments(data.documents || []);
  } catch (e) {
    list.innerHTML = '<div style="color:#9B2020;font-size:13px">Could not load documents: ' + esc(e.message) + '</div>';
  }
}

function renderDocuments(documents) {
  const list = document.getElementById('documents-list');
  if (!documents.length) {
    list.innerHTML = '<div style="color:var(--ink-3);font-size:13.5px;padding:8px 0">No documents or links added yet.</div>';
    return;
  }
  list.innerHTML = documents.map(d => {
    const link = d.kind === 'file'
      ? `<a href="/api/profile/documents/${d.id}/file" target="_blank">${esc(d.label)}</a>`
      : `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.label)}</a>`;
    return `
    <div class="doc-row">
      <div>
        <div class="doc-label">${link}</div>
        <div class="doc-meta">${d.kind === 'file' ? (d.has_text ? 'Document · text extracted' : 'Document') : 'Link'}</div>
      </div>
      <div class="doc-actions">
        <button onclick="deleteDocument(${d.id})">Remove</button>
      </div>
    </div>`;
  }).join('');
}

async function uploadDocument() {
  const fileInput = document.getElementById('doc-file-input');
  const label     = document.getElementById('doc-label-input').value.trim();
  const status    = document.getElementById('doc-upload-status');
  const file      = fileInput.files[0];

  if (!file) {
    status.textContent = 'Choose a file first.';
    status.className   = 'search-status err';
    return;
  }

  status.innerHTML = '<span class="spinner"></span> Uploading...';
  status.className = 'search-status';

  const formData = new FormData();
  formData.append('file', file);
  formData.append('label', label);

  try {
    const res  = await fetch('/api/profile/documents', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.error) {
      status.textContent = data.error;
      status.className   = 'search-status err';
      return;
    }
    fileInput.value = '';
    document.getElementById('doc-label-input').value = '';
    status.textContent = 'Uploaded.';
    loadDocuments();
  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
  }
}

async function addLink() {
  const label  = document.getElementById('link-label-input').value.trim();
  const url    = document.getElementById('link-url-input').value.trim();
  const status = document.getElementById('link-add-status');

  if (!url) {
    status.textContent = 'Enter a URL first.';
    status.className   = 'search-status err';
    return;
  }

  try {
    const res  = await fetch('/api/profile/links', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ label, url })
    });
    const data = await res.json();
    if (data.error) {
      status.textContent = data.error;
      status.className   = 'search-status err';
      return;
    }
    document.getElementById('link-label-input').value = '';
    document.getElementById('link-url-input').value = '';
    status.textContent = 'Added.';
    loadDocuments();
  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
  }
}

async function deleteDocument(id) {
  try {
    await fetch('/api/profile/documents/' + id, { method: 'DELETE' });
    loadDocuments();
  } catch (e) {
    alert('Could not remove: ' + e.message);
  }
}
```

- [ ] **Step 7: Restart the running server**

```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; ends with `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 8: Curl-based verification**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/profile
```
Expected: `200`.

Using a cookie jar, sign up, save a profile, add a link, upload a small text file, list documents, and confirm the download endpoint serves it:

```bash
curl -s -c /tmp/ck.txt -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/signup \
  -H 'Content-Type: application/json' -d '{"email":"testuser@example.com","password":"hunter222"}'
curl -s -c /tmp/ck.txt -b /tmp/ck.txt -X POST http://localhost:8000/api/profile/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"Test User","bio_text":"bio","project_description":"proj","confirmed_paper_ids":[],"research_interests":[]}'
curl -s -c /tmp/ck.txt -b /tmp/ck.txt -X POST http://localhost:8000/api/profile/links \
  -H 'Content-Type: application/json' -d '{"label":"My Site","url":"https://example.com"}'
echo "hello from a test upload" > /tmp/testdoc.txt
curl -s -c /tmp/ck.txt -b /tmp/ck.txt -X POST http://localhost:8000/api/profile/documents \
  -F "file=@/tmp/testdoc.txt" -F "label=Test Doc"
curl -s -c /tmp/ck.txt -b /tmp/ck.txt http://localhost:8000/api/profile/documents
```
Expected: the final `GET` shows both the link and the file entries.

- [ ] **Step 9: Manual verification in the browser**

1. Log in, go through the wizard (or already have a profile), confirm the full profile view appears showing bio, interests, publications, and project — not the old terse "Welcome back" card.
2. Upload a real PDF with a label, confirm it appears in the list, click it, confirm it downloads/opens.
3. Add a link with a label, confirm it appears and opens in a new tab.
4. Remove a document and a link, confirm both disappear from the list.
5. Click "Continue to Advisor", confirm it navigates to `/advisor` as before.
6. Click "Update my profile", confirm the wizard reappears and saving again still lands back on the profile view (not a duplicate profile — same as before this change).

- [ ] **Step 10: Commit**

```bash
git add templates/profile.html
git commit -m "$(cat <<'EOF'
Add full profile view with document/link upload to profile.html

Replaces the terse "Welcome back" card with a full view of the saved
profile (bio, interests, publications, project) plus a documents/links
section backed by the new profile_documents endpoints. Saving no
longer auto-redirects to the advisor — the user reviews/adds sources
first, then continues via an explicit button.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend — advisor topic-block menu

**Files:**
- Modify: `templates/advisor.html` (CSS, HTML, JS)

**Interfaces:**
- Consumes: nothing new — reuses the existing profile-load flow and `sendInitialGreeting()`/`callAdvisorAPI()`.
- Produces: `startBlock(block)` function, callable by future topic blocks without further plumbing changes.

No automated test exists for this file — verification is curl-based endpoint checks (unchanged backend) plus a manual walkthrough.

- [ ] **Step 1: Add topic-block CSS**

In `templates/advisor.html`, immediately after the `.proposal-section-label{font-weight:700;color:var(--ink);margin-bottom:3px}` rule (`templates/advisor.html:47`), add:

```css
.topic-blocks{flex:1;display:flex;align-items:center;justify-content:center;padding:40px 28px}
.topic-blocks-inner{max-width:520px;width:100%}
.topic-blocks-inner h2{font-family:Georgia,serif;font-size:22px;font-weight:normal;margin-bottom:8px;color:var(--ink)}
.topic-blocks-desc{font-size:14px;color:var(--ink-2);margin-bottom:24px}
.topic-block-list{display:flex;flex-direction:column;gap:10px}
.topic-block{background:var(--surface);border:1.5px solid var(--border);padding:16px 18px;text-align:left;cursor:pointer;font-family:inherit}
.topic-block:hover{border-color:var(--teal);background:var(--teal-bg)}
.topic-block-title{font-size:15px;font-weight:700;color:var(--ink);margin-bottom:4px}
.topic-block-desc{font-size:13px;color:var(--ink-2)}
```

- [ ] **Step 2: Add the topic-blocks markup**

In `templates/advisor.html:152-154`, the current block reads:

```html
<!-- Research proposal panel (shown once a proposal exists) -->
<div id="proposal-panel" class="proposal-panel" style="display:none"></div>

<!-- Chat interface (hidden until profile is confirmed) -->
```

Replace with:

```html
<!-- Research proposal panel (shown once a proposal exists) -->
<div id="proposal-panel" class="proposal-panel" style="display:none"></div>

<!-- Topic blocks (shown after profile loads, before entering a conversation) -->
<div class="topic-blocks" id="topic-blocks" style="display:none">
  <div class="topic-blocks-inner">
    <h2>What would you like to talk about?</h2>
    <p class="topic-blocks-desc">Pick a topic to start a focused conversation with the advisor.</p>
    <div class="topic-block-list">
      <button class="topic-block" onclick="startBlock('research')">
        <div class="topic-block-title">Tell me about my new research</div>
        <div class="topic-block-desc">Build out a research proposal and find AI/data-science collaborators.</div>
      </button>
    </div>
  </div>
</div>

<!-- Chat interface (hidden until profile is confirmed) -->
```

- [ ] **Step 3: Show the topic-blocks menu instead of jumping straight into chat**

In `templates/advisor.html:214-228`, the current tail of `init()` reads:

```js
  // Show profile bar
  const bar  = document.getElementById('profile-bar');
  const pname = document.getElementById('pb-name');
  const pmeta = document.getElementById('pb-meta');
  pname.textContent = _profile.name;
  const projPreview = (_profile.project_description || '').slice(0, 120);
  pmeta.textContent = projPreview ? 'Project: ' + projPreview + (projPreview.length >= 120 ? '…' : '') : 'No project description yet.';
  bar.style.display = 'flex';

  // Show chat
  document.getElementById('chat-wrap').style.display = 'flex';

  // Kick off the personalized greeting automatically
  sendInitialGreeting();
})();
```

Replace with:

```js
  // Show profile bar
  const bar  = document.getElementById('profile-bar');
  const pname = document.getElementById('pb-name');
  const pmeta = document.getElementById('pb-meta');
  pname.textContent = _profile.name;
  const projPreview = (_profile.project_description || '').slice(0, 120);
  pmeta.textContent = projPreview ? 'Project: ' + projPreview + (projPreview.length >= 120 ? '…' : '') : 'No project description yet.';
  bar.style.display = 'flex';

  // Show the topic-blocks menu instead of jumping straight into a conversation
  document.getElementById('topic-blocks').style.display = 'flex';
})();

// ── Topic blocks ──────────────────────────────────────────────────────────────
function startBlock(block) {
  document.getElementById('topic-blocks').style.display = 'none';
  document.getElementById('chat-wrap').style.display = 'flex';
  sendInitialGreeting();
}
```

`block` is unused for now (only one block exists) but keeps `startBlock` ready to branch on it once more blocks are designed, without changing its call sites.

- [ ] **Step 4: Restart the running server**

```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; ends with `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 5: Curl-based verification**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/advisor
```
Expected: `200`.

```bash
grep -c "topic-blocks\|startBlock" templates/advisor.html
```
Expected: a positive count confirming the new markup/JS landed.

- [ ] **Step 6: Manual verification in the browser**

1. Log in with an account that has a saved profile, visit `/advisor` — confirm the "What would you like to talk about?" menu appears instead of the chat starting immediately.
2. Click "Tell me about my new research" — confirm the menu disappears, the chat interface appears, and the personalized greeting fires exactly as it did before this change.
3. Confirm the rest of the conversation (suggestions, faculty search, proposal panel) behaves unchanged — this task doesn't touch any of that logic.

- [ ] **Step 7: Commit**

```bash
git add templates/advisor.html
git commit -m "$(cat <<'EOF'
Add topic-block menu to advisor page before starting a conversation

Shows "What would you like to talk about?" with one working block
("Tell me about my new research") that reveals the chat and starts
today's existing conversation unchanged. startBlock(block) is ready
for more blocks to be added later without further plumbing changes.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review notes

- **Spec coverage:** `profile_documents` table + all five endpoints with ownership checks (Task 1); full profile view (bio/interests/papers/project) + document/link upload/list/delete UI (Task 2); topic-block menu gating the chat (Task 3) — all three pieces of the user's request are covered.
- **No placeholders:** every step has complete, runnable code, exact SQL, or exact commands with expected output.
- **Type/name consistency checked:** the six document/link fields (`id`, `kind`, `label`, `url`, `filename`, `has_text`) are consistent between Task 1's `GET /api/profile/documents` response and Task 2's `renderDocuments()`; `stored_filename` is only ever exposed server-side (used for file I/O, never returned to the client) — confirmed the JSON responses in Task 1 never include it. `showProfileView(profile)` is defined once in Task 2 and called from both `init()` and `saveProfile()`'s success path with the same shape (`GET /api/profile/me`'s response). `startBlock(block)` in Task 3 doesn't depend on anything from Tasks 1-2.
