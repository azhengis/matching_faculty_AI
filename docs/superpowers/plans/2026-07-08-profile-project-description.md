# Profile Step 3 Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single free-text "describe your project" textarea in the faculty profile wizard with five guided questions, plus an alternative PDF/DOCX upload path that extracts text for review before saving.

**Architecture:** A new pure-Python extraction module (`doc_extract.py`) handles PDF/DOCX → text, called from one new stateless FastAPI endpoint (`POST /api/profile/extract-file`) in `web_app.py`. The frontend (`templates/profile.html`) gets a two-tab UI: "Type it out" (5 labeled textareas, concatenated client-side) and "Upload a document" (file input → extraction endpoint → editable review textarea). Both paths funnel into the existing `POST /api/profile/save` endpoint and the existing `project_description TEXT` column — no schema change.

**Tech Stack:** FastAPI (existing), `pypdf` (new), `python-docx` (new), `python-multipart` (new, runtime only), vanilla JS (existing pattern, no framework), pytest + `fpdf2` (new, test-only, for generating PDF fixtures).

**Design spec:** `docs/superpowers/specs/2026-07-08-profile-project-description-design.md`

## Global Constraints

- No database schema changes — `project_description` stays one `TEXT` column (spec §Goals).
- Upload accepts `.pdf` and `.docx` only, no legacy `.doc` (spec §Non-goals).
- Upload size cap: 10MB, enforced server-side, not just client-side (spec §4).
- The four guided questions (problem, data, methodology challenge, desired AI help) are required; the fifth ("anything else") is optional (spec §2).
- The live app process runs via `python -m uvicorn web_app:app --host 0.0.0.0 --port 8000` with `reload=False` — it must be restarted after backend changes for them to take effect (spec §5).
- Two separate Python environments exist in this repo: `.venv/` (created for pipeline/test tooling) and the app's actual runtime environment (`~/Library/Python/3.13/lib/python/site-packages`, used by the running `uvicorn` process). New libraries needed by the running app must be installed into the runtime environment, not just `.venv`.

---

### Task 1: `doc_extract.py` — PDF/DOCX text extraction module

**Files:**
- Create: `doc_extract.py`
- Create: `tests/test_doc_extract.py`

**Interfaces:**
- Produces: `extract_text(filename: str, content: bytes) -> str`. Returns extracted text. Raises `ValueError` with a user-facing message in three cases: unsupported extension, unparsable/corrupt file, or no readable text found (empty/whitespace-only result).

- [ ] **Step 1: Install test and library dependencies into `.venv`**

Run:
```bash
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
/Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/pip install pypdf python-docx pytest fpdf2
```
Expected: all four install successfully (no errors).

Verify:
```bash
/Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -c "import pypdf, docx, fpdf, pytest; print('deps ok')"
```
Expected output: `deps ok`

- [ ] **Step 2: Write the failing tests**

Create `tests/test_doc_extract.py`:

```python
import io

import pytest
from docx import Document
from fpdf import FPDF

from doc_extract import extract_text


def _make_docx_bytes(paragraphs):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf_bytes(text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.multi_cell(0, 10, text)
    return bytes(pdf.output())


def _make_empty_pdf_bytes():
    pdf = FPDF()
    pdf.add_page()
    return bytes(pdf.output())


def test_extract_text_from_docx():
    content = _make_docx_bytes([
        "My research focuses on natural language processing.",
        "I study low-resource languages.",
    ])
    result = extract_text("statement.docx", content)
    assert "natural language processing" in result
    assert "low-resource languages" in result


def test_extract_text_from_pdf():
    content = _make_pdf_bytes("My research focuses on computer vision for medical imaging.")
    result = extract_text("statement.pdf", content)
    assert "computer vision" in result


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text("notes.txt", b"hello")


def test_extract_text_raises_on_empty_pdf():
    content = _make_empty_pdf_bytes()
    with pytest.raises(ValueError, match="No readable text found"):
        extract_text("blank.pdf", content)


def test_extract_text_raises_on_corrupted_file():
    with pytest.raises(ValueError, match="Couldn't read that file"):
        extract_text("broken.pdf", b"not a real pdf")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && /Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -m pytest tests/test_doc_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'doc_extract'` (module doesn't exist yet).

- [ ] **Step 4: Implement `doc_extract.py`**

Create `doc_extract.py`:

```python
"""
doc_extract.py
--------------
Pure text-extraction helpers for the profile document-upload feature.
Supports .pdf and .docx only. Every failure mode raises ValueError with
a message meant to be shown directly to the faculty member uploading
the file.
"""
import io


def extract_pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def extract_docx_text(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text(filename: str, content: bytes) -> str:
    """Extract readable text from a .pdf or .docx file's raw bytes.

    Raises ValueError with a user-facing message if the extension is
    unsupported, the file can't be parsed, or no text is found.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        extractor = extract_pdf_text
    elif name.endswith(".docx"):
        extractor = extract_docx_text
    else:
        raise ValueError("Unsupported file type. Please upload a .pdf or .docx file.")

    try:
        text = extractor(content)
    except ValueError:
        raise
    except Exception:
        raise ValueError(
            "Couldn't read that file. It may be corrupted — try a different "
            "file or 'Type it out' instead."
        )

    if not text.strip():
        raise ValueError("No readable text found in that file. Try 'Type it out' instead.")

    return text
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && /Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -m pytest tests/test_doc_extract.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add doc_extract.py tests/test_doc_extract.py
git commit -m "$(cat <<'EOF'
Add doc_extract.py for PDF/DOCX text extraction

Pure-function module backing the profile upload feature; extracts text
from .pdf and .docx files and raises a user-facing ValueError for
unsupported types, corrupt files, or empty extraction results.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `POST /api/profile/extract-file` endpoint

**Files:**
- Modify: `web_app.py:30-39` (imports), `web_app.py:5-20` (docstring route list), add new route near `web_app.py:373` (next to `/api/profile/save`)
- Test: `tests/test_extract_endpoint.py`

**Interfaces:**
- Consumes: `doc_extract.extract_text(filename, content) -> str` (Task 1).
- Produces: route function `api_profile_extract_file(file: UploadFile) -> JSONResponse` registered at `POST /api/profile/extract-file`. Success: `{"text": "..."}`, status 200. Bad extension or file >10MB: `{"error": "..."}`, status 400. Extraction failure (corrupt/empty): `{"error": "..."}`, status 422.

**Note on test approach:** `web_app.py`'s FastAPI app uses a `lifespan` handler that loads the SPECTER2 embedding model and search indices on startup (`web_app.py:87-99`). Using FastAPI's `TestClient` as a context manager would trigger that heavy ML startup and require installing `torch`/`sentence-transformers`/`transformers`/`adapters` into `.venv` just to test one stateless endpoint. To avoid that, tests call the route function directly (it's a plain `async def`, importable and callable without going through HTTP or triggering `lifespan`), constructing a real `fastapi.UploadFile` by hand.

- [ ] **Step 1: Install additional test dependencies into `.venv`**

Run:
```bash
/Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/pip install fastapi numpy
```
Expected: both install successfully. (`.venv` already has `pypdf`, `python-docx`, `pytest`, `fpdf2` from Task 1 — these four are needed so `.venv` can `import web_app` at all, since `web_app.py` imports `fastapi`, `numpy`, and `search` at module level.)

Verify:
```bash
/Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -c "import fastapi, numpy; print('deps ok')"
```
Expected output: `deps ok`

- [ ] **Step 2: Install runtime dependencies into the live app's environment**

Run:
```bash
/opt/homebrew/bin/python3 -m pip install --user pypdf python-docx python-multipart
```
Expected: all three install successfully into `~/Library/Python/3.13/lib/python/site-packages` (the same location `fastapi`/`uvicorn`/`numpy` are already installed, confirmed via `pip list` during planning). `python-multipart` is required by FastAPI to parse the real multipart/form-data upload from the browser — without it, the running server will raise `RuntimeError: Form data requires "python-multipart" to be installed.` as soon as this route is registered.

Verify:
```bash
/opt/homebrew/bin/python3 -c "import pypdf, docx, multipart; print('runtime deps ok')"
```
Expected output: `runtime deps ok`

- [ ] **Step 3: Write the failing tests**

Create `tests/test_extract_endpoint.py`:

```python
import asyncio
import io
import json

from docx import Document
from fastapi import UploadFile
from fpdf import FPDF

from web_app import api_profile_extract_file


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


def test_extract_file_endpoint_returns_text_for_valid_docx():
    doc = Document()
    doc.add_paragraph("My research focuses on reinforcement learning for robotics.")
    buf = io.BytesIO()
    doc.save(buf)
    upload = UploadFile(file=io.BytesIO(buf.getvalue()), filename="statement.docx")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 200
    assert "reinforcement learning" in _body(response)["text"]


def test_extract_file_endpoint_rejects_bad_extension():
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 400
    assert "error" in _body(response)


def test_extract_file_endpoint_rejects_oversized_file():
    big_content = b"x" * (10 * 1024 * 1024 + 1)
    upload = UploadFile(file=io.BytesIO(big_content), filename="statement.pdf")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 400
    assert "error" in _body(response)


def test_extract_file_endpoint_returns_422_for_empty_pdf():
    pdf = FPDF()
    pdf.add_page()
    upload = UploadFile(file=io.BytesIO(bytes(pdf.output())), filename="blank.pdf")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 422
    assert "error" in _body(response)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && /Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -m pytest tests/test_extract_endpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'api_profile_extract_file' from 'web_app'` (route doesn't exist yet).

- [ ] **Step 5: Add the import and route to `web_app.py`**

In `web_app.py`, update the module docstring's route list (around line 18-19) — change:
```python
  POST /api/profile/save        — create/update profile
  GET  /api/profile/{id}        — load a saved profile
```
to:
```python
  POST /api/profile/save        — create/update profile
  GET  /api/profile/{id}        — load a saved profile
  POST /api/profile/extract-file — extract text from an uploaded .pdf/.docx
```

Update the import block (`web_app.py:35-39`) — change:
```python
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search as sm
```
to:
```python
import numpy as np
from fastapi import FastAPI, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import search as sm
import doc_extract
```

Add the new route immediately after the existing `/api/profile/save` handler (after `web_app.py:395`, the `return JSONResponse({"profile_id": profile_id, "name": name})` line that closes `api_profile_save`):

```python
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB


@app.post("/api/profile/extract-file")
async def api_profile_extract_file(file: UploadFile = File(...)):
    """Extract text from an uploaded .pdf or .docx for review before saving."""
    filename = file.filename or ""
    if not filename.lower().endswith((".pdf", ".docx")):
        return JSONResponse(
            {"error": "Unsupported file type. Please upload a .pdf or .docx file."},
            status_code=400,
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error": "File is too large (10MB max)."}, status_code=400)

    try:
        text = doc_extract.extract_text(filename, content)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

    return JSONResponse({"text": text})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && /Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -m pytest tests/test_extract_endpoint.py -v`
Expected: 4 passed.

- [ ] **Step 7: Run the full test suite together**

Run: `cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher && /Users/aruzhanzhengis/Documents/depaul-faculty-matcher/.venv/bin/python -m pytest tests/ -v`
Expected: 9 passed (5 from Task 1 + 4 from Task 2).

- [ ] **Step 8: Commit**

```bash
git add web_app.py tests/test_extract_endpoint.py
git commit -m "$(cat <<'EOF'
Add POST /api/profile/extract-file endpoint

Stateless endpoint that extracts text from an uploaded .pdf/.docx via
doc_extract.py, for the profile wizard's document-upload alternative
to typing a project description.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend redesign — `templates/profile.html` step 3

**Files:**
- Modify: `templates/profile.html` (CSS block, step-3 panel markup, step-3 JS)

**Interfaces:**
- Consumes: `POST /api/profile/extract-file` (Task 2), existing `POST /api/profile/save` (unchanged).
- Produces: no new interfaces consumed by other files — this is the outermost layer of the feature.

No automated test exists for this file: the codebase has no JS test runner or `package.json` anywhere (checked during planning), so this task's verification is a manual, in-browser walkthrough, matching the existing "Testing plan" section of the design spec.

- [ ] **Step 1: Add mode-tab CSS**

In `templates/profile.html`, immediately after the `.char-count` rule (around line 99, in the "── Project textarea ──" block), add:

```css
.mode-tabs{display:flex;gap:20px;margin-bottom:20px;border-bottom:1.5px solid var(--border)}
.mode-tab{padding:10px 2px;background:none;border:none;border-bottom:2px solid transparent;font-size:13.5px;font-weight:600;color:var(--ink-3);cursor:pointer;font-family:inherit}
.mode-tab.active{color:var(--navy);border-bottom-color:var(--navy)}
.mode-tab:hover{color:var(--navy)}
```

- [ ] **Step 2: Replace the step-3 panel markup**

In `templates/profile.html`, the current step-3 panel (lines 202-224) is:

```html
  <!-- ── Step 3: Project description ── -->
  <div id="panel-3" class="panel">
    <div class="selected-banner">
      <div><strong id="s3-name"></strong><br><span id="s3-meta" style="font-size:13px;color:var(--teal)"></span></div>
      <button class="btn-change" onclick="goToStep(1)">Change profile</button>
    </div>

    <div class="section-tag">Step 3 of 3</div>
    <h2>Describe your research project</h2>
    <p class="section-desc">The advisor will use this to give you personalized suggestions for AI integration and find the right collaborators.</p>

    <div class="textarea-wrap">
      <div class="field-label">Current research project</div>
      <textarea class="proj-textarea" id="project-desc"
        placeholder="What research problem are you working on right now? What are you trying to find out or build? What data do you have? What's the hardest part of the methodology?&#10;&#10;The more specific you are, the more targeted the AI's suggestions will be."></textarea>
      <div class="char-count" id="char-count">0 / 1000 characters recommended</div>
    </div>

    <button class="btn-primary" id="btn-save" onclick="saveProfile()" style="width:100%">
      Save Profile &amp; Open Advisor &rarr;
    </button>
    <div class="search-status" id="save-status" style="margin-top:12px"></div>
  </div>
```

Replace it with:

```html
  <!-- ── Step 3: Project description ── -->
  <div id="panel-3" class="panel">
    <div class="selected-banner">
      <div><strong id="s3-name"></strong><br><span id="s3-meta" style="font-size:13px;color:var(--teal)"></span></div>
      <button class="btn-change" onclick="goToStep(1)">Change profile</button>
    </div>

    <div class="section-tag">Step 3 of 3</div>
    <h2>Describe your research project</h2>
    <p class="section-desc">The advisor will use this to give you personalized suggestions for AI integration and find the right collaborators.</p>

    <div class="mode-tabs">
      <button type="button" class="mode-tab active" id="mode-tab-type" onclick="setMode('type')">Type it out</button>
      <button type="button" class="mode-tab" id="mode-tab-upload" onclick="setMode('upload')">Upload a document</button>
    </div>

    <div id="mode-type">
      <div class="textarea-wrap">
        <div class="field-label">What problem are you working on?</div>
        <textarea class="proj-textarea" id="q-problem"
          placeholder="What research problem are you working on right now? What are you trying to find out or build?"></textarea>
      </div>
      <div class="textarea-wrap">
        <div class="field-label">What data do you have or could you collect?</div>
        <textarea class="proj-textarea" id="q-data"
          placeholder="e.g. survey responses, sensor logs, text corpora, imaging data..."></textarea>
      </div>
      <div class="textarea-wrap">
        <div class="field-label">What's the hardest part of the methodology?</div>
        <textarea class="proj-textarea" id="q-challenge"
          placeholder="Where do you get stuck, or what part feels the most uncertain?"></textarea>
      </div>
      <div class="textarea-wrap">
        <div class="field-label">What kind of AI/data-science help are you looking for?</div>
        <textarea class="proj-textarea" id="q-help"
          placeholder="A technical co-investigator? A consultant on methods? Something else?"></textarea>
      </div>
      <div class="textarea-wrap">
        <div class="field-label">Anything else you'd like to add? <span style="text-transform:none;font-weight:400">(optional)</span></div>
        <textarea class="proj-textarea" id="q-extra" style="min-height:80px"
          placeholder="Anything else that would help the advisor understand your work."></textarea>
      </div>
    </div>

    <div id="mode-upload" style="display:none">
      <div class="textarea-wrap">
        <div class="field-label">Upload a project description, grant abstract, or research statement</div>
        <input type="file" id="upload-input" accept=".pdf,.docx" onchange="handleFileUpload(event)">
        <div class="search-status" id="upload-status"></div>
        <textarea class="proj-textarea" id="upload-extracted-text" style="display:none;margin-top:10px"
          placeholder="Extracted text will appear here for you to review before saving."></textarea>
      </div>
    </div>

    <button class="btn-primary" id="btn-save" onclick="saveProfile()" style="width:100%">
      Save Profile &amp; Open Advisor &rarr;
    </button>
    <div class="search-status" id="save-status" style="margin-top:12px"></div>
  </div>
```

- [ ] **Step 3: Replace the step-3 JS (mode state, tab switching, upload handler, save logic)**

In `templates/profile.html`, the current step-3 script block (lines 376-433) starts with:

```js
// ── Step 3: Project + save ───────────────────────────────────────────────────
const projTA = document.getElementById('project-desc');
projTA.addEventListener('input', () => {
  const n = projTA.value.length;
  document.getElementById('char-count').textContent = n + ' / 1000 characters recommended';
  document.getElementById('char-count').style.color = n > 1000 ? 'var(--scarlet)' : 'var(--ink-3)';
});

async function saveProfile() {
  const btn    = document.getElementById('btn-save');
  const status = document.getElementById('save-status');
  const proj   = document.getElementById('project-desc').value.trim();

  if (!proj) {
    status.textContent = 'Please describe your current research project before saving.';
    status.className   = 'search-status err';
    return;
  }

  btn.disabled      = true;
  status.textContent = '';

  const payload = {
    faculty_id:           _selected ? _selected.id : null,
    name:                 _selected ? _selected.name : 'Unknown',
    email:                _selected ? _selected.email || '' : '',
    bio_text:             _selected ? _selected.bio || '' : '',
    project_description:  proj,
    confirmed_paper_ids:  getCheckedPaperIds(),
  };

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

    localStorage.setItem('advisor_profile_id',   String(data.profile_id));
    localStorage.setItem('advisor_profile_name', data.name);

    // Redirect to advisor
    window.location.href = '/advisor';

  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
    btn.disabled       = false;
  }
}
```

Replace it with:

```js
// ── Step 3: Project + save ───────────────────────────────────────────────────
let _mode = 'type';

function setMode(mode) {
  _mode = mode;
  document.getElementById('mode-tab-type').classList.toggle('active', mode === 'type');
  document.getElementById('mode-tab-upload').classList.toggle('active', mode === 'upload');
  document.getElementById('mode-type').style.display = mode === 'type' ? 'block' : 'none';
  document.getElementById('mode-upload').style.display = mode === 'upload' ? 'block' : 'none';
}

async function handleFileUpload(event) {
  const file = event.target.files[0];
  const status = document.getElementById('upload-status');
  const extractedTA = document.getElementById('upload-extracted-text');
  extractedTA.style.display = 'none';
  extractedTA.value = '';

  if (!file) return;

  const name = file.name.toLowerCase();
  if (!name.endsWith('.pdf') && !name.endsWith('.docx')) {
    status.textContent = 'Unsupported file type. Please upload a .pdf or .docx file.';
    status.className   = 'search-status err';
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    status.textContent = 'File is too large (10MB max).';
    status.className   = 'search-status err';
    return;
  }

  status.innerHTML = '<span class="spinner"></span> Extracting text...';
  status.className = 'search-status';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res  = await fetch('/api/profile/extract-file', { method: 'POST', body: formData });
    const data = await res.json();

    if (data.error) {
      status.textContent = data.error;
      status.className   = 'search-status err';
      return;
    }

    extractedTA.value = data.text;
    extractedTA.style.display = 'block';
    status.textContent = 'Review the extracted text below, then save.';
    status.className = 'search-status';
  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
  }
}

async function saveProfile() {
  const btn    = document.getElementById('btn-save');
  const status = document.getElementById('save-status');

  let proj;
  if (_mode === 'type') {
    const problem   = document.getElementById('q-problem').value.trim();
    const data      = document.getElementById('q-data').value.trim();
    const challenge = document.getElementById('q-challenge').value.trim();
    const help      = document.getElementById('q-help').value.trim();
    const extra     = document.getElementById('q-extra').value.trim();

    if (!problem || !data || !challenge || !help) {
      status.textContent = 'Please fill in all four required questions before saving.';
      status.className   = 'search-status err';
      return;
    }

    const parts = [
      'Research problem: ' + problem,
      'Data available: ' + data,
      'Methodological challenge: ' + challenge,
      'Desired AI/data-science help: ' + help,
    ];
    if (extra) parts.push('Additional notes: ' + extra);
    proj = parts.join('\n\n');
  } else {
    proj = document.getElementById('upload-extracted-text').value.trim();
    if (!proj) {
      status.textContent = 'Please upload a document and review the extracted text before saving.';
      status.className   = 'search-status err';
      return;
    }
  }

  btn.disabled      = true;
  status.textContent = '';

  const payload = {
    faculty_id:           _selected ? _selected.id : null,
    name:                 _selected ? _selected.name : 'Unknown',
    email:                _selected ? _selected.email || '' : '',
    bio_text:             _selected ? _selected.bio || '' : '',
    project_description:  proj,
    confirmed_paper_ids:  getCheckedPaperIds(),
  };

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

    localStorage.setItem('advisor_profile_id',   String(data.profile_id));
    localStorage.setItem('advisor_profile_name', data.name);

    // Redirect to advisor
    window.location.href = '/advisor';

  } catch (e) {
    status.textContent = 'Network error: ' + e.message;
    status.className   = 'search-status err';
    btn.disabled       = false;
  }
}
```

- [ ] **Step 4: Restart the running server**

The live server is running with `reload=False`, so it won't pick up the `web_app.py` changes from Task 2 or these template changes automatically.

Find and stop the current process:
```bash
ps aux | grep "uvicorn web_app:app" | grep -v grep
kill <PID from above>
```

Restart it in the background:
```bash
cd /Users/aruzhanzhengis/Documents/depaul-faculty-matcher
/opt/homebrew/bin/python3 -m uvicorn web_app:app --host 0.0.0.0 --port 8000 > /tmp/web_app_restart.log 2>&1 &
```

Verify it came up clean:
```bash
sleep 3 && tail -20 /tmp/web_app_restart.log
```
Expected: no tracebacks; last line resembles `Ready — N faculty indexed, M with publication records`.

- [ ] **Step 5: Manual verification in the browser**

Open `http://localhost:8000/profile` and walk through:

1. Search for and select any faculty member, confirm/uncheck papers, proceed to step 3.
2. **Type it out path:** leave "What data do you have..." empty, fill the other three required boxes, click save → confirm the inline error "Please fill in all four required questions before saving." appears and the page does not navigate away.
3. Fill all four required boxes plus the optional "anything else" box, click save → confirm it redirects to `/advisor`.
4. Go back to `/profile`, start a new profile (or reuse), switch to the "Upload a document" tab, upload a real `.docx` file with some paragraph text → confirm a spinner appears, then the extracted text appears in the editable textarea below the file input.
5. Edit the extracted text, click save → confirm it redirects to `/advisor`.
6. Repeat step 4-5 with a real `.pdf` that has a text layer (e.g. export any doc to PDF from Word/Google Docs).
7. Try uploading a `.txt` file → confirm the inline error "Unsupported file type..." appears immediately (client-side check, no network call needed).
8. Try uploading a scanned/image-only PDF or a blank PDF → confirm the server round-trips and shows "No readable text found in that file. Try 'Type it out' instead."

- [ ] **Step 6: Commit**

```bash
git add templates/profile.html
git commit -m "$(cat <<'EOF'
Redesign profile step 3 into guided questions + document upload

Replaces the single free-text project-description textarea with a
mode toggle: five guided questions (four required, one optional) that
get concatenated client-side, or a PDF/DOCX upload that extracts text
via the new /api/profile/extract-file endpoint for review before
saving. No backend schema change — both paths still submit a single
project_description string to the existing save endpoint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Plan self-review notes

- **Spec coverage:** mode toggle (Task 3 Step 2), 5 guided boxes with exact labels/required-ness (Task 3 Step 2 + validation in Step 3), concatenation format (Task 3 Step 3 `saveProfile`), upload endpoint with extension/size/extraction validation and correct status codes (Task 2 Step 5), review-before-save textarea (Task 3 Step 2/3), dependency installs in both environments (Task 1 Step 1, Task 2 Steps 1-2), server restart requirement (Task 3 Step 4) — all covered.
- **No placeholders:** every step has complete, runnable code or exact commands with expected output.
- **Type/name consistency checked:** `extract_text(filename, content)` signature matches between Task 1's implementation and Task 2's call site; `api_profile_extract_file` name matches between Task 2's route definition and its test's import; frontend element IDs (`q-problem`, `q-data`, `q-challenge`, `q-help`, `q-extra`, `upload-input`, `upload-status`, `upload-extracted-text`, `mode-tab-type`, `mode-tab-upload`, `mode-type`, `mode-upload`) are consistent between the HTML (Task 3 Step 2) and JS (Task 3 Step 3).
