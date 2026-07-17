# Proposal Conversation Depth + Live Panel + Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the advisor's research-proposal conversation genuinely Socratic (one question at a time, section by section, pushing for the multi-item depth of a real proposal), show the proposal building up live in a side panel next to the chat, let the user download it as a Word document, and always keep it viewable on the profile page.

**Architecture:** `_advisor_system_prompt`'s CONVERSATION FLOW is rewritten to ask one focused question per turn and push for richer, bulleted content in the multi-item sections (research questions, related work, methodology, expected outcomes). A new deterministic helper, `_build_proposal_docx`, converts a saved proposal into a `.docx` (via the already-available `python-docx` library) and is served through a new `GET /api/profile/me/proposal/download` endpoint. `templates/advisor.html` gets a two-column layout — chat on the left, a persistent proposal panel on the right that re-renders after every turn and includes the download link; clicking download hides the panel (chat continues). `templates/profile.html` gets a new "Research Proposal" section in the full profile view, always showing the latest saved state with the same download link.

**Tech Stack:** FastAPI + `sqlite3` + `python-docx` (already a runtime dependency, used by `doc_extract.py` for reading `.docx` uploads — this plan is the first to *write* one, but installs nothing new) — no new dependencies.

## Global Constraints

- No new third-party dependencies — `python-docx` is already present in both the `.venv` test environment and the app's runtime environment (confirmed by the pre-existing `/api/profile/extract-file` endpoint and the profile-documents feature's own tests, which already round-trip real `.docx` files).
- The download format is `.docx` (Word), not PDF or plain text.
- After a successful download click, the proposal side panel on `/advisor` hides; the chat conversation continues normally (not a reset to the topic-block menu). The proposal itself is never deleted — it stays in the `proposals` table and remains visible on `/profile` regardless of whether it was ever downloaded.
- `_build_proposal_docx` is a pure function (dict in, bytes out) with no FastAPI/request coupling, so it can be unit-tested deterministically without a live LLM — mirroring the pattern already used by `auth.py` and `doc_extract.py` in this codebase.
- System-prompt wording changes (Task 1) have no automated test — consistent with every prior prompt change in this codebase, which are all verified manually against a live model, since there's no way to unit-test LLM conversational behavior.
- The live app process runs via `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000` with `reload=False` — must be restarted after backend/template changes to take effect.

---

### Task 1: Backend — Socratic conversation flow + bullet-formatting hints

**Files:**
- Modify: `web_app.py:673-729` (`_advisor_system_prompt`), `web_app.py:752-774` (`_ADVISOR_TOOLS`'s `save_proposal` field descriptions)

**Interfaces:**
- Consumes: nothing new.
- Produces: no interface change — same function signature, same tool schema shape (only description strings and the CONVERSATION FLOW prose change). Later tasks don't depend on anything new from this task.

No automated test — manual verification only, per Global Constraints.

- [ ] **Step 1: Rewrite the CONVERSATION FLOW section of `_advisor_system_prompt`**

In `web_app.py`, the current block (`web_app.py:704-726`) reads:

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

Replace with:

```python
━━━ CONVERSATION FLOW ━━━
• First message: Greet {name} by name. Reference their research area from their bio — show you read it. If they described a project, acknowledge it specifically. If not, ask: "What research problem are you currently working on?"

• Build the research proposal through genuine back-and-forth — ask ONE focused question at a time, wait for {name}'s answer, then ask the next. Never dump a checklist of questions in one message. Work through these sections in order, but let {name} jump ahead, revisit, or add detail at any point:

  1. Background — the problem and its context. Ask what's motivating this work and why it matters right now. Use their bio and project description above as your starting material — don't re-ask for things you already know.
  2. Objectives — what they're trying to find out, build, or change. Ask them to state this in a sentence or two.
  3. Research questions — the specific questions or hypotheses being tested. Don't settle for one question: help {name} articulate 2-4, grouped by theme if there's more than one angle (e.g. "Consent and X", "Bias and Y" — mirroring how a strong proposal breaks questions into thematic clusters). Ask "what else would you want to know?" to draw out more than the first answer.
  4. Related work — ask if they know of specific scholars, papers, or prior approaches connected to this work. If they don't, or want help, suggest 2-4 specific works or researchers you're aware of that seem relevant, each with a one-sentence note on why it's relevant — always framed as "you should verify these" since you don't have live literature search. Ask if any resonate or if they'd add their own.
  5. Methodology — don't just take the first idea. Suggest 2-3 concrete methodological approaches (e.g. historical/archival analysis, case studies, interviews, dataset/bias analysis, legal-ethical review) and ask {name} to react — which fit, which don't, what would they add or combine. Converge on a multi-part methodology if the project calls for one, not a single generic method.
  6. Expected outcomes — ask what this research should produce or demonstrate when it's done. Push for 3-4 concrete outcomes, not one vague sentence.

  For research_questions, related_work, methodology, and expected_outcomes — once you have more than one item, format the saved text as a bulleted list (lines starting with "- ") so it reads cleanly as a real document. Background and objectives stay as short prose.

• If {name}'s answers stay vague or uncertain ("not sure", "I don't know", short non-answers) across a couple of exchanges, do NOT keep pressing for a full proposal. Instead, pivot: offer a short menu of 3-4 broad, generally-applicable AI/data-science possibilities for their field (e.g. "text/document analysis," "predictive modeling from existing records," "survey or interview data analysis," "automating a manual review process") so they have something concrete to react to. Ask which sounds closest, then proceed straight to the AI integration suggestions below — skip the full proposal and do not call save_proposal.

• Call save_proposal once background, objectives, research questions, and methodology are reasonably developed (related_work and expected_outcomes are a bonus — include them if you have them, but don't hold up saving for them). Call it again any time the proposal evolves — a new research question, a refined methodology, added literature — so what's saved always reflects the latest state of the conversation.

• Once the proposal is developed (whether the full version or the fallback menu), give 3-4 CONCRETE AI integration suggestions. Name actual methods — topic modeling, computer vision, NLP, predictive modeling, network analysis, etc. — and explain why each fits this specific research.

• Then call search_faculty. Base the query on the SAVED PROPOSAL's methodology and research questions (not just the surface-level conversation) — craft it around the AI/DATA SKILLS needed, not the subject domain.
  Example: for a researcher studying political polarization via surveys who needs ML help, search:
  "machine learning natural language processing survey analysis text classification sentiment"
  NOT "political polarization sociology."

• Return up to 10 results. For each person, explain specifically what they bring to this collaboration — what method or expertise they have that matches the researcher's need.
```

- [ ] **Step 2: Update `save_proposal`'s field descriptions to hint at bullet formatting**

In `web_app.py`, the `save_proposal` tool's `properties` block (`web_app.py:761-770`) currently reads:

```python
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
```

Replace with:

```python
        "parameters": {
            "type": "object",
            "properties": {
                "background": {"type": "string", "description": "The research problem/context, 2-4 sentences."},
                "objectives": {"type": "string", "description": "The primary research objectives."},
                "research_questions": {"type": "string", "description": "Research questions and/or hypotheses being tested. If there is more than one, format as a bulleted list (lines starting with '- ')."},
                "related_work": {"type": "string", "description": "Relevant related work/literature discussed. If there is more than one item, format as a bulleted list (lines starting with '- ')."},
                "methodology": {"type": "string", "description": "The chosen or discussed methodological approach(es). If there is more than one component, format as a bulleted list (lines starting with '- ')."},
                "expected_outcomes": {"type": "string", "description": "What the research is expected to produce or show. If there is more than one outcome, format as a bulleted list (lines starting with '- ')."}
            },
            "required": ["background", "objectives", "research_questions", "methodology"]
        }
```

- [ ] **Step 3: Run the full test suite to confirm no regression**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 65 passed (this task only changes prompt/description strings, no code paths any existing test exercises).

- [ ] **Step 4: Commit**

```bash
git add web_app.py
git commit -m "$(cat <<'EOF'
Rewrite advisor conversation flow to be genuinely Socratic

Replaces the single-pass "cover these sections conversationally"
instruction with an explicit one-question-at-a-time flow through six
sections, pushing for multiple grouped research questions, suggested
literature with relevance notes, a multi-part methodology, and
concrete expected outcomes — matching the depth of a real research
proposal rather than a quick intake. Multi-item sections are now
requested as bulleted text so they render and export cleanly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend — `.docx` generation + download endpoint

**Files:**
- Modify: `web_app.py:43-44` (imports), `web_app.py:891-892` (insert after `_save_proposal`)
- Test: `tests/test_proposal_download.py`

**Interfaces:**
- Consumes: `proposals` table (existing), `_current_user(req)` (existing).
- Produces: `_build_proposal_docx(researcher_name: str, proposal: dict) -> bytes` — pure function, no request/DB access. New route `api_profile_proposal_download(req) -> Response` at `GET /api/profile/me/proposal/download`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proposal_download.py`:

```python
import asyncio
import io
import json
import sqlite3

from docx import Document

import web_app
from web_app import _build_proposal_docx, api_profile_proposal_download


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


def _paragraph_texts(docx_bytes):
    doc = Document(io.BytesIO(docx_bytes))
    return [p.text for p in doc.paragraphs]


def test_build_proposal_docx_includes_all_populated_sections():
    proposal = {
        "background": "AI ethics in imagery is under-examined.",
        "objectives": "Understand consent and bias issues.",
        "research_questions": "- How does AI challenge consent?\n- How does bias manifest in outputs?",
        "related_work": "- Manovich, Artificial Aesthetics\n- Paglen, image ethics",
        "methodology": "- Historical analysis\n- Case studies",
        "expected_outcomes": "- A framework for ethical guidelines\n- A set of recommendations",
    }
    docx_bytes = _build_proposal_docx("Jane Doe", proposal)
    texts = _paragraph_texts(docx_bytes)

    assert any("Jane Doe" in t for t in texts)
    assert any("Introduction" in t or "Background" in t for t in texts)
    assert "AI ethics in imagery is under-examined." in texts
    assert "How does AI challenge consent?" in texts
    assert "Manovich, Artificial Aesthetics" in texts
    assert "Historical analysis" in texts
    assert "A framework for ethical guidelines" in texts


def test_build_proposal_docx_skips_empty_sections():
    proposal = {
        "background": "Some background.",
        "objectives": "Some objectives.",
        "research_questions": "A single research question.",
        "related_work": "",
        "methodology": "Some methodology.",
        "expected_outcomes": "",
    }
    docx_bytes = _build_proposal_docx("Jane Doe", proposal)
    texts = _paragraph_texts(docx_bytes)

    assert not any("Relevant Literature" in t for t in texts)
    assert not any("Expected Outcomes" in t for t in texts)


def test_build_proposal_docx_renders_bullet_lines_as_list_items():
    proposal = {
        "background": "bg", "objectives": "obj",
        "research_questions": "- First question\n- Second question",
        "related_work": "", "methodology": "single method line", "expected_outcomes": "",
    }
    docx_bytes = _build_proposal_docx("Jane Doe", proposal)
    doc = Document(io.BytesIO(docx_bytes))

    bullet_paragraphs = [p for p in doc.paragraphs if p.text in ("First question", "Second question")]
    assert len(bullet_paragraphs) == 2
    for p in bullet_paragraphs:
        assert p.style.name == "List Bullet"

    plain_paragraphs = [p for p in doc.paragraphs if p.text == "single method line"]
    assert len(plain_paragraphs) == 1
    assert plain_paragraphs[0].style.name != "List Bullet"


def _init_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    return db_path


def _signup_session(email="jane@depaul.edu"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": "hunter222"})))
    return list(web_app._auth_sessions.keys())[-1]


def test_download_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    response = _run(api_profile_proposal_download(_FakeRequest(cookies={})))
    assert response.status_code == 401


def test_download_returns_404_when_no_profile(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup_session()
    response = _run(api_profile_proposal_download(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 404


def test_download_returns_404_when_no_proposal(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    token = _signup_session()
    user_id = web_app._auth_sessions[token]
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (name, user_id) VALUES ('Jane Doe', ?)", (user_id,))
    con.commit()
    con.close()

    response = _run(api_profile_proposal_download(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 404


def test_download_returns_docx_when_proposal_exists(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    token = _signup_session()
    user_id = web_app._auth_sessions[token]
    con = sqlite3.connect(db_path)
    cur = con.execute("INSERT INTO profiles (name, user_id) VALUES ('Jane Doe', ?)", (user_id,))
    profile_id = cur.lastrowid
    con.execute(
        "INSERT INTO proposals (profile_id, background, objectives, research_questions, methodology) "
        "VALUES (?, 'bg text', 'obj text', 'rq text', 'method text')", (profile_id,)
    )
    con.commit()
    con.close()

    response = _run(api_profile_proposal_download(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert response.media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "attachment" in response.headers["content-disposition"]
    assert "Jane_Doe" in response.headers["content-disposition"]

    texts = _paragraph_texts(response.body)
    assert "bg text" in texts
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_proposal_download.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_proposal_docx' from 'web_app'` (neither the function nor the route exist yet).

- [ ] **Step 3: Update imports for `Response`**

In `web_app.py`, the import line (`web_app.py:44`) currently reads:

```python
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
```

Replace with:

```python
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
```

- [ ] **Step 4: Add `_build_proposal_docx` and the download route**

In `web_app.py`, immediately after `_save_proposal` (which ends at `web_app.py:891` with `return {"status": "saved"}`), before `@app.post("/api/advisor/chat")`, add:

```python
def _build_proposal_docx(researcher_name: str, proposal: dict) -> bytes:
    """Render a saved proposal dict into a .docx file's raw bytes.

    Pure function — no DB/request access — so it's independently testable.
    Sections with empty text are skipped entirely. Within a section, lines
    starting with "- " or "• " become bulleted list items; other lines
    become plain paragraphs.
    """
    import io
    from docx import Document

    doc = Document()
    title = f"Research Proposal: {researcher_name}" if researcher_name else "Research Proposal"
    doc.add_heading(title, level=1)

    sections = [
        ("Introduction / Background", proposal.get("background", "")),
        ("Research Objectives", proposal.get("objectives", "")),
        ("Possible Research Questions", proposal.get("research_questions", "")),
        ("Relevant Literature", proposal.get("related_work", "")),
        ("Methodology", proposal.get("methodology", "")),
        ("Expected Outcomes", proposal.get("expected_outcomes", "")),
    ]

    for heading, text in sections:
        text = (text or "").strip()
        if not text:
            continue
        doc.add_heading(heading, level=2)
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("- ") or line.startswith("• "):
                doc.add_paragraph(line[2:].strip(), style="List Bullet")
            else:
                doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@app.get("/api/profile/me/proposal/download")
async def api_profile_proposal_download(req: Request):
    """Download the logged-in user's saved research proposal as a .docx file."""
    user = _current_user(req)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)

    con = sqlite3.connect(DB_PATH)
    profile_row = con.execute("SELECT id, name FROM profiles WHERE user_id = ?", (user["id"],)).fetchone()
    if not profile_row:
        con.close()
        return JSONResponse({"error": "No profile yet"}, status_code=404)
    profile_id, name = profile_row
    row = con.execute(
        "SELECT background, objectives, research_questions, related_work, methodology, expected_outcomes "
        "FROM proposals WHERE profile_id = ?", (profile_id,)
    ).fetchone()
    con.close()
    if not row or not (row[0] or "").strip():
        return JSONResponse({"error": "No proposal to download yet."}, status_code=404)

    proposal = dict(zip(
        ["background", "objectives", "research_questions", "related_work", "methodology", "expected_outcomes"], row
    ))
    docx_bytes = _build_proposal_docx(name, proposal)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "proposal").strip("_") or "proposal"
    filename = f"Research_Proposal_{safe_name}.docx"
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 5: Update the module docstring**

In `web_app.py`, change:

```python
  GET  /api/profile/me/proposal — the logged-in user's saved research proposal, if any (requires login)
```

to:

```python
  GET  /api/profile/me/proposal — the logged-in user's saved research proposal, if any (requires login)
  GET  /api/profile/me/proposal/download — download the saved proposal as a .docx file (requires login)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/test_proposal_download.py -v`
Expected: 7 passed.

- [ ] **Step 7: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && ./.venv/bin/python -m pytest tests/ -v`
Expected: 72 passed (65 pre-existing + 7 new).

- [ ] **Step 8: Commit**

```bash
git add web_app.py tests/test_proposal_download.py
git commit -m "$(cat <<'EOF'
Add .docx generation and GET /api/profile/me/proposal/download

_build_proposal_docx is a pure, deterministically-testable function
(dict in, .docx bytes out) reused by the new download endpoint, which
requires login and 404s if no profile/proposal exists yet. Bulleted
lines in a section's saved text become real list items in the
document; sections with no content are omitted entirely.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend — advisor.html two-column layout with live proposal panel + download

**Files:**
- Modify: `templates/advisor.html` (CSS, HTML, JS)

**Interfaces:**
- Consumes: `GET /api/profile/me/proposal` (existing), `GET /api/profile/me/proposal/download` (Task 2).
- Produces: no new interfaces — outermost layer.

No automated test exists for this file — verification is curl-based endpoint checks plus a manual walkthrough.

- [ ] **Step 1: Replace the profile-bar/proposal-panel CSS with two-column layout CSS**

In `templates/advisor.html`, the current block (`templates/advisor.html:37-60`) reads:

```css
/* ── Profile bar (shown when profile is loaded) ── */
.profile-bar{background:var(--teal-bg);border-bottom:1.5px solid #B3D9DB;padding:10px 24px;flex-shrink:0;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.profile-bar-name{font-size:13.5px;font-weight:700;color:var(--teal)}
.profile-bar-meta{font-size:12.5px;color:var(--ink-2);flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.profile-bar-actions{display:flex;gap:10px;flex-shrink:0}
.btn-bar-link{background:none;border:none;font-size:12.5px;color:var(--teal);cursor:pointer;text-decoration:underline;font-family:inherit;padding:0;white-space:nowrap}
.btn-bar-link:hover{color:var(--navy)}
.proposal-panel{background:var(--surface);border-bottom:1.5px solid var(--border);padding:16px 24px;font-size:13.5px;color:var(--ink-2);line-height:1.6}
.proposal-section{margin-bottom:12px}
.proposal-section:last-child{margin-bottom:0}
.proposal-section-label{font-weight:700;color:var(--ink);margin-bottom:3px}

.topic-blocks{flex:1;display:flex;align-items:center;justify-content:center;padding:40px 28px}
.topic-blocks-inner{max-width:520px;width:100%}
.topic-blocks-inner h2{font-family:Georgia,serif;font-size:22px;font-weight:normal;margin-bottom:8px;color:var(--ink)}
.topic-blocks-desc{font-size:14px;color:var(--ink-2);margin-bottom:24px}
.topic-block-list{display:flex;flex-direction:column;gap:10px}
.topic-block{background:var(--surface);border:1.5px solid var(--border);padding:16px 18px;text-align:left;cursor:pointer;font-family:inherit}
.topic-block:hover{border-color:var(--teal);background:var(--teal-bg)}
.topic-block-title{font-size:15px;font-weight:700;color:var(--ink);margin-bottom:4px}
.topic-block-desc{font-size:13px;color:var(--ink-2)}

/* ── Chat layout ── */
.chat-wrap{flex:1;display:flex;flex-direction:column;max-width:820px;width:100%;margin:0 auto;padding:0 20px;min-height:0}
```

Replace with:

```css
/* ── Profile bar (shown when profile is loaded) ── */
.profile-bar{background:var(--teal-bg);border-bottom:1.5px solid #B3D9DB;padding:10px 24px;flex-shrink:0;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.profile-bar-name{font-size:13.5px;font-weight:700;color:var(--teal)}
.profile-bar-meta{font-size:12.5px;color:var(--ink-2);flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.profile-bar-actions{display:flex;gap:10px;flex-shrink:0}
.btn-bar-link{background:none;border:none;font-size:12.5px;color:var(--teal);cursor:pointer;text-decoration:underline;font-family:inherit;padding:0;white-space:nowrap}
.btn-bar-link:hover{color:var(--navy)}

.topic-blocks{flex:1;display:flex;align-items:center;justify-content:center;padding:40px 28px}
.topic-blocks-inner{max-width:520px;width:100%}
.topic-blocks-inner h2{font-family:Georgia,serif;font-size:22px;font-weight:normal;margin-bottom:8px;color:var(--ink)}
.topic-blocks-desc{font-size:14px;color:var(--ink-2);margin-bottom:24px}
.topic-block-list{display:flex;flex-direction:column;gap:10px}
.topic-block{background:var(--surface);border:1.5px solid var(--border);padding:16px 18px;text-align:left;cursor:pointer;font-family:inherit}
.topic-block:hover{border-color:var(--teal);background:var(--teal-bg)}
.topic-block-title{font-size:15px;font-weight:700;color:var(--ink);margin-bottom:4px}
.topic-block-desc{font-size:13px;color:var(--ink-2)}

/* ── Two-column layout: chat + live proposal panel ── */
.advisor-layout{flex:1;display:flex;min-height:0}
.chat-wrap{flex:1;display:flex;flex-direction:column;max-width:820px;width:100%;margin:0 auto;padding:0 20px;min-height:0}

.proposal-sidepanel{width:380px;flex-shrink:0;border-left:1.5px solid var(--border);background:var(--surface);overflow-y:auto;padding:22px;display:none}
.proposal-sidepanel.visible{display:block}
.proposal-sidepanel h3{font-family:Georgia,serif;font-size:17px;font-weight:normal;color:var(--ink);margin-bottom:4px}
.proposal-sidepanel-sub{font-size:12px;color:var(--ink-3);margin-bottom:18px}
.proposal-section{margin-bottom:18px}
.proposal-section:last-child{margin-bottom:0}
.proposal-section-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--teal);margin-bottom:6px}
.proposal-section-body{font-size:13.5px;color:var(--ink-2);line-height:1.6}
.proposal-section-body p{margin-bottom:6px}
.proposal-section-body p:last-child{margin-bottom:0}
.proposal-section-body ul{padding-left:18px}
.proposal-section-body li{margin-bottom:3px}
.proposal-empty{font-size:13px;color:var(--ink-3);font-style:italic}
.btn-download{display:block;margin-top:20px;padding:11px 16px;background:var(--teal);color:#fff;border:none;font-size:13.5px;font-weight:600;cursor:pointer;text-decoration:none;text-align:center}
.btn-download:hover{background:#0A4A50}
.btn-download.disabled{opacity:.4;pointer-events:none}

@media(max-width:900px){.advisor-layout{flex-direction:column}.proposal-sidepanel{width:100%;border-left:none;border-top:1.5px solid var(--border);max-height:40vh}}
```

- [ ] **Step 2: Replace the profile-bar/proposal-panel markup and wrap chat in the new layout div**

In `templates/advisor.html:151-196`, the current block reads:

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
<div class="chat-wrap" id="chat-wrap" style="display:none">
  <div class="messages" id="messages">
    <!-- Typing indicator -->
    <div class="msg bot typing" id="typing">
      <div class="avatar">AI</div>
      <div class="bubble">
        <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      </div>
    </div>
  </div>

  <div class="input-bar">
    <textarea class="chat-input" id="chat-input" rows="1"
              placeholder="Ask anything about your research, AI integration, or potential collaborators..."></textarea>
    <button class="btn-send" id="btn-send" onclick="sendMessage()">Send</button>
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
  </div>
</div>

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

<!-- Chat + live proposal panel (hidden until a topic block is chosen) -->
<div class="advisor-layout" id="advisor-layout">
  <div class="chat-wrap" id="chat-wrap" style="display:none">
    <div class="messages" id="messages">
      <!-- Typing indicator -->
      <div class="msg bot typing" id="typing">
        <div class="avatar">AI</div>
        <div class="bubble">
          <span class="dot"></span><span class="dot"></span><span class="dot"></span>
        </div>
      </div>
    </div>

    <div class="input-bar">
      <textarea class="chat-input" id="chat-input" rows="1"
                placeholder="Ask anything about your research, AI integration, or potential collaborators..."></textarea>
      <button class="btn-send" id="btn-send" onclick="sendMessage()">Send</button>
    </div>
  </div>

  <div class="proposal-sidepanel" id="proposal-sidepanel">
    <h3>Your Research Proposal</h3>
    <div class="proposal-sidepanel-sub">Updates live as you talk with the advisor.</div>
    <div id="proposal-sections"></div>
    <a class="btn-download disabled" id="btn-download-proposal" href="/api/profile/me/proposal/download" onclick="return onDownloadProposal(event)">Download as Word doc</a>
  </div>
</div>
```

- [ ] **Step 3: Show the panel (not just the chat) when the research block starts**

In `templates/advisor.html:252-256`, the current function reads:

```js
function startBlock(block) {
  document.getElementById('topic-blocks').style.display = 'none';
  document.getElementById('chat-wrap').style.display = 'flex';
  sendInitialGreeting();
}
```

Replace with:

```js
function startBlock(block) {
  document.getElementById('topic-blocks').style.display = 'none';
  document.getElementById('chat-wrap').style.display = 'flex';
  if (block === 'research') {
    document.getElementById('proposal-sidepanel').classList.add('visible');
  }
  sendInitialGreeting();
}
```

- [ ] **Step 4: Replace `checkProposal()`/`renderProposalPanel()`/`toggleProposalPanel()` with live-panel rendering + download handling**

In `templates/advisor.html:500-539`, the current block reads:

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
  try {
    const res  = await fetch('/api/profile/me/proposal');
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

Replace with:

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
  try {
    const res  = await fetch('/api/profile/me/proposal');
    const data = await res.json();
    _proposal = data;
    renderProposalPanel();
  } catch (e) {
    // Network hiccup — the panel just won't update this turn.
  }
}

function proposalTextToHtml(text) {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const isBulletLine = l => l.startsWith('- ') || l.startsWith('• ');
  if (lines.some(isBulletLine)) {
    const items = lines.filter(isBulletLine).map(l => `<li>${esc(l.replace(/^[-•]\s+/, ''))}</li>`).join('');
    return `<ul>${items}</ul>`;
  }
  return lines.map(l => `<p>${esc(l)}</p>`).join('');
}

function renderProposalPanel() {
  const container = document.getElementById('proposal-sections');
  const sections = Object.keys(PROPOSAL_LABELS)
    .map(k => {
      const val = (_proposal && _proposal[k]) ? _proposal[k].trim() : '';
      if (!val) return '';
      return `<div class="proposal-section"><div class="proposal-section-label">${esc(PROPOSAL_LABELS[k])}</div><div class="proposal-section-body">${proposalTextToHtml(val)}</div></div>`;
    })
    .join('');
  container.innerHTML = sections || '<div class="proposal-empty">Nothing yet — it will appear here as we talk.</div>';

  const hasContent = Object.keys(PROPOSAL_LABELS).some(k => (_proposal && _proposal[k] || '').trim());
  document.getElementById('btn-download-proposal').classList.toggle('disabled', !hasContent);
}

function onDownloadProposal(event) {
  const hasContent = Object.keys(PROPOSAL_LABELS).some(k => (_proposal && _proposal[k] || '').trim());
  if (!hasContent) {
    event.preventDefault();
    return false;
  }
  // Let the browser's normal navigation to the download URL proceed
  // (the server sends Content-Disposition: attachment, so this downloads
  // the file rather than navigating away). Hide the panel shortly after
  // so the chat gets the full width back — the proposal itself is never
  // deleted, it's always still viewable on the profile page.
  setTimeout(() => {
    document.getElementById('proposal-sidepanel').classList.remove('visible');
  }, 300);
  return true;
}
```

- [ ] **Step 5: Remove the now-unused `_proposalPanelOpen` state variable**

In `templates/advisor.html:211-216`, the current state block reads:

```js
// ── State ─────────────────────────────────────────────────────────────────────
let _sessionId  = null;
let _busy       = false;
let _profile    = null;
let _proposal   = null;
let _proposalPanelOpen = false;
```

Replace with:

```js
// ── State ─────────────────────────────────────────────────────────────────────
let _sessionId  = null;
let _busy       = false;
let _profile    = null;
let _proposal   = null;
```

- [ ] **Step 6: Restart the running server**

```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; ends with `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 7: Curl-based verification**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/advisor
```
Expected: `200`.

Using a cookie jar, sign up, save a profile with `_save_proposal`-equivalent data isn't directly possible via curl (that's LLM-driven), but confirm the download endpoint's error paths and the panel markup landed:

```bash
curl -s -c /tmp/ck2.txt -b /tmp/ck2.txt -X POST http://localhost:8000/api/auth/signup \
  -H 'Content-Type: application/json' -d '{"email":"downloadtest@example.com","password":"hunter222"}'
curl -s -c /tmp/ck2.txt -b /tmp/ck2.txt http://localhost:8000/api/profile/me/proposal/download
```
Expected: the last call returns `{"error":"No profile yet"}` with a 404 (no profile saved yet for this fresh account).

```bash
grep -c "proposal-sidepanel\|onDownloadProposal\|advisor-layout" templates/advisor.html
```
Expected: a positive count confirming the new markup/JS landed.

- [ ] **Step 8: Manual verification in the browser (and, if `CHATBOT_MODEL` is configured, a live conversation)**

1. Log in with an account that has a saved profile, visit `/advisor`, click "Tell me about my new research" — confirm the layout splits into chat (left) and an empty "Your Research Proposal" panel (right) showing "Nothing yet — it will appear here as we talk."
2. If `CHATBOT_MODEL` is configured: have a conversation, confirm the advisor asks one question at a time rather than a list; confirm the panel's sections fill in as `save_proposal` gets called (after each assistant turn, matching the existing `checkProposal()` call site); confirm multi-item sections render as bullet lists in the panel, not as one run-on paragraph.
3. Click "Download as Word doc" once there's content — confirm a `.docx` file downloads, confirm the panel then hides and the chat expands to full width, confirm the conversation is still usable afterward (not reset).
4. Confirm clicking the download link while the panel is still empty does nothing (the link has the `disabled` class and its `onclick` handler prevents navigation).
5. If `CHATBOT_MODEL` is not configured, note in the report that step 2's live-conversation check was skipped and why.

- [ ] **Step 9: Commit**

```bash
git add templates/advisor.html
git commit -m "$(cat <<'EOF'
Add live two-column proposal panel + download to the advisor page

Replaces the old toggle-button proposal panel with a persistent
side panel next to the chat, visible once the research topic block
starts. It re-renders after every turn (bullet-aware), and a
"Download as Word doc" link (disabled until there's content) triggers
GET /api/profile/me/proposal/download, then hides the panel — the
chat continues, and the proposal remains permanently viewable on the
profile page.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Frontend — profile.html "Research Proposal" section

**Files:**
- Modify: `templates/profile.html` (CSS, HTML, JS)

**Interfaces:**
- Consumes: `GET /api/profile/me/proposal` (existing), `GET /api/profile/me/proposal/download` (Task 2).
- Produces: no new interfaces — outermost layer.

No automated test exists for this file — verification is curl-based endpoint checks plus a manual walkthrough.

- [ ] **Step 1: Add CSS for the proposal section (reusing the panel-section look from advisor.html for visual consistency)**

In `templates/profile.html`, immediately after the `.doc-add-form{flex:1;min-width:240px;padding:14px;border:1px dashed var(--border-2)}` rule (`templates/profile.html:128`), add:

```css
.proposal-view-section{background:var(--surface);border:1.5px solid var(--border);padding:24px;margin-bottom:24px}
.proposal-view-empty{font-size:13.5px;color:var(--ink-3);font-style:italic}
.proposal-view-item{margin-bottom:16px}
.proposal-view-item:last-child{margin-bottom:0}
.proposal-view-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--teal);margin-bottom:6px}
.proposal-view-body{font-size:14px;color:var(--ink-2);line-height:1.6}
.proposal-view-body p{margin-bottom:6px}
.proposal-view-body p:last-child{margin-bottom:0}
.proposal-view-body ul{padding-left:18px}
.proposal-view-body li{margin-bottom:3px}
.btn-download{display:inline-block;margin-top:14px;padding:10px 18px;background:var(--teal);color:#fff;border:none;font-size:13.5px;font-weight:600;cursor:pointer;text-decoration:none}
.btn-download:hover{background:#0A4A50}
```

- [ ] **Step 2: Add the "Research Proposal" section to the full profile view**

In `templates/profile.html:172-174`, the current markup reads:

```html
    </div>

    <div class="documents-section">
```

Replace with:

```html
    </div>

    <div class="proposal-view-section" id="proposal-view-section">
      <div class="section-tag">Research Proposal</div>
      <h2>Your proposal</h2>
      <p class="section-desc">Built from your conversations with the advisor — always up to date here, whether or not you've downloaded it.</p>
      <div id="proposal-view-body"></div>
    </div>

    <div class="documents-section">
```

- [ ] **Step 3: Call `loadProposalSection()` from `showProfileView()`**

In `templates/profile.html:387-405`, the current function reads:

```js
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

Replace with:

```js
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
  loadProposalSection();
}
```

- [ ] **Step 4: Add the proposal-loading/rendering JS**

In `templates/profile.html`, immediately after the `logout()` function (which ends around `templates/profile.html:761` with `}`), before the `// ── Documents & links ──` comment, add:

```js
// ── Research proposal ─────────────────────────────────────────────────────────
const PROPOSAL_LABELS = {
  background: 'Background',
  objectives: 'Objectives',
  research_questions: 'Research Questions',
  related_work: 'Related Work',
  methodology: 'Methodology',
  expected_outcomes: 'Expected Outcomes',
};

function proposalTextToHtml(text) {
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const isBulletLine = l => l.startsWith('- ') || l.startsWith('• ');
  let html = '';
  let bulletBuffer = [];
  const flushBullets = () => {
    if (bulletBuffer.length) {
      html += '<ul>' + bulletBuffer.map(l => `<li>${esc(l.replace(/^[-•]\s+/, ''))}</li>`).join('') + '</ul>';
      bulletBuffer = [];
    }
  };
  for (const line of lines) {
    if (isBulletLine(line)) {
      bulletBuffer.push(line);
    } else {
      flushBullets();
      html += `<p>${esc(line)}</p>`;
    }
  }
  flushBullets();
  return html;
}

async function loadProposalSection() {
  const body = document.getElementById('proposal-view-body');
  try {
    const res  = await fetch('/api/profile/me/proposal');
    const data = await res.json();
    const hasContent = Object.keys(PROPOSAL_LABELS).some(k => (data[k] || '').trim());

    if (!hasContent) {
      body.innerHTML = '<div class="proposal-view-empty">Not started yet — build one by talking with the Faculty Advisor.</div>';
      return;
    }

    const items = Object.keys(PROPOSAL_LABELS)
      .map(k => {
        const val = (data[k] || '').trim();
        if (!val) return '';
        return `<div class="proposal-view-item"><div class="proposal-view-label">${esc(PROPOSAL_LABELS[k])}</div><div class="proposal-view-body">${proposalTextToHtml(val)}</div></div>`;
      })
      .join('');
    body.innerHTML = items + '<a class="btn-download" href="/api/profile/me/proposal/download">Download as Word doc</a>';
  } catch (e) {
    body.innerHTML = '<div style="color:#9B2020;font-size:13px">Could not load your proposal: ' + esc(e.message) + '</div>';
  }
}
```

- [ ] **Step 5: Restart the running server**

```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; ends with `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 6: Curl-based verification**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/profile
```
Expected: `200`.

Using a cookie jar, sign up, save a profile, insert a proposal row directly via `sqlite3` (simulating what the LLM would have saved), then confirm the section reflects it:

```bash
curl -s -c /tmp/ck3.txt -b /tmp/ck3.txt -X POST http://localhost:8000/api/auth/signup \
  -H 'Content-Type: application/json' -d '{"email":"proptest@example.com","password":"hunter222"}'
curl -s -c /tmp/ck3.txt -b /tmp/ck3.txt -X POST http://localhost:8000/api/profile/save \
  -H 'Content-Type: application/json' \
  -d '{"name":"Prop Test","bio_text":"bio","project_description":"proj","confirmed_paper_ids":[],"research_interests":[]}'
sqlite3 faculty.db "INSERT INTO proposals (profile_id, background, objectives, research_questions, methodology) SELECT id, 'Test background.', 'Test objectives.', '- Q1\n- Q2', 'Test methodology.' FROM profiles WHERE name='Prop Test';"
curl -s -c /tmp/ck3.txt -b /tmp/ck3.txt http://localhost:8000/api/profile/me/proposal
```
Expected: the last call's JSON shows the inserted `background`/`objectives`/`research_questions`/`methodology` values.

```bash
grep -c "proposal-view-section\|loadProposalSection" templates/profile.html
```
Expected: a positive count confirming the new markup/JS landed.

- [ ] **Step 7: Manual verification in the browser**

1. Log in as an account with no proposal yet, visit `/profile` — confirm the "Research Proposal" section shows "Not started yet — build one by talking with the Faculty Advisor."
2. Build a proposal via `/advisor` (or, if `CHATBOT_MODEL` isn't configured, insert one directly via `sqlite3` as in Step 6), return to `/profile` — confirm the section shows the saved sections formatted with bullet lists where applicable, plus a "Download as Word doc" link.
3. Click the download link on the profile page — confirm it downloads the same `.docx` file the advisor page's panel would produce (same endpoint).

- [ ] **Step 8: Commit**

```bash
git add templates/profile.html
git commit -m "$(cat <<'EOF'
Add Research Proposal section to the full profile view

Always shows the latest saved proposal (or a "not started yet" note),
formatted with bullet lists where applicable, plus a Download link —
so the proposal remains permanently accessible here regardless of
whether it was ever downloaded from the advisor page.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review notes

- **Spec coverage:** Socratic, one-question-at-a-time conversation flow with grouped research questions, suggested literature, multi-part methodology, concrete outcomes (Task 1); `.docx` generation matching the example proposal's section structure, download endpoint (Task 2); live two-column panel on `/advisor` that updates after every turn, download-then-hide behavior with the proposal never deleted (Task 3); permanent "always stored... for example profile" requirement via the new profile-page section with its own download link (Task 4) — all pieces of the user's request are covered.
- **No placeholders:** every step has complete, runnable code, exact SQL/commands, or exact expected output.
- **Type/name consistency checked:** `PROPOSAL_LABELS` (six identical keys/labels) is duplicated consistently between Task 3's `advisor.html` and Task 4's `profile.html`, matching this codebase's established pattern of small, deliberate duplication across templates (e.g. `logout()`, `esc()`) rather than a shared JS file. `proposalTextToHtml` is duplicated identically in both files for the same reason. `_build_proposal_docx`'s parameter/return shape (`researcher_name: str, proposal: dict -> bytes`) is used identically by its test file and by the download route in Task 2. The six proposal field names are identical across the DB schema (pre-existing), the `save_proposal` tool schema (Task 1), the download endpoint's `SELECT`/`dict(zip(...))` (Task 2), and both frontend `PROPOSAL_LABELS` objects (Tasks 3-4).
