# Profile Step 3 Redesign: Guided Questions + Document Upload

## Problem

Step 3 of the faculty onboarding wizard (`templates/profile.html`) asks faculty to
describe their research project in a single large textarea. The placeholder hints
at four things to cover (problem, data, methodology challenge, what help they
want) but faculty face one blank box and have to structure the answer themselves.
This causes blank-page hesitation and inconsistent, under-specified project
descriptions — which directly weakens the AI advisor's first message and its
`search_faculty` query quality (see `_advisor_system_prompt` in `web_app.py`).

There's also no way to reuse an existing document (grant abstract, CV research
statement) — faculty must retype everything by hand.

## Goals

- Replace the single textarea with guided, labeled boxes so faculty know exactly
  what to write in each one.
- Let faculty upload a PDF or Word document as an alternative way to provide
  their project description, with a review step before saving.
- No backend schema change: `project_description` stays a single `TEXT` column;
  this is a client-side/input-collection change, not a data-model change.

## Non-goals

- No DB migration or separate columns per guided question.
- No legacy `.doc` (binary) support — `.pdf` and `.docx` only.
- No persistence of which mode (typed vs. uploaded) was used.
- No change to the advisor.html "update project" quick-edit flow (still a single
  textarea there — out of scope for this pass).

## Design

### 1. Mode toggle

Step 3 gets two tabs above the existing step heading: **"Type it out"** (default,
active on load) and **"Upload a document."** Only the active tab's inputs are
visible and validated; whichever is active when "Save Profile & Open Advisor" is
clicked determines what's submitted.

### 2. "Type it out" mode

Five labeled textareas replace the single `#project-desc` textarea:

| Box | Label | Required |
|---|---|---|
| 1 | What problem are you working on? | yes |
| 2 | What data do you have or could you collect? | yes |
| 3 | What's the hardest part of the methodology? | yes |
| 4 | What kind of AI/data-science help are you looking for? | yes |
| 5 | Anything else you'd like to add? | no |

On save, the client concatenates the non-empty boxes into one labeled string,
e.g.:

```
Research problem: <box 1>

Data available: <box 2>

Methodological challenge: <box 3>

Desired AI/data-science help: <box 4>

Additional notes: <box 5, if filled>
```

This string is sent as `project_description` in the existing
`POST /api/profile/save` payload — the endpoint itself is unchanged.

Validation: boxes 1-4 must be non-empty before save is allowed (same
non-empty-project rule the old single textarea enforced, just spread across
four fields now).

### 3. "Upload a document" mode

A file input accepting `.pdf` and `.docx`. On file selection:

1. Client uploads the file via `POST /api/profile/extract-file`
   (`multipart/form-data`).
2. Server validates extension and size (≤ 10MB), extracts text (`pypdf` for
   `.pdf`, `python-docx` for `.docx`), and returns `{"text": "..."}`.
3. Client shows the extracted text in an editable textarea so the faculty
   member can review/clean it up before saving (PDF extraction can be messy —
   this is the same safety net typing gets).
4. If extraction fails or returns empty text, show an inline error and suggest
   switching to "Type it out" instead.

On save (while in this mode), the (possibly edited) textarea content is sent
directly as `project_description` — no concatenation, no guided-box labels.

Validation: the textarea must be non-empty before save is allowed.

### 4. Backend: new endpoint

`POST /api/profile/extract-file` in `web_app.py`:

- Accepts one `UploadFile`.
- Rejects (400) if extension isn't `.pdf`/`.docx`, or file exceeds 10MB.
- `.pdf` → extract text with `pypdf.PdfReader`, join page text with `\n\n`.
- `.docx` → extract text with `python-docx`, join non-empty paragraph text with
  `\n\n`.
- If extracted text is empty/whitespace-only (e.g. scanned PDF with no text
  layer), return `{"error": "No readable text found in that file. Try 'Type it
  out' instead."}` with status 422. Bad extension/oversized file returns the
  same `{"error": ...}` shape with status 400. The client checks for an
  `error` key in the response body either way (same convention already used by
  `/api/profile/save`), so the exact status code doesn't change client
  handling — it's just for consistency with existing 4xx usage in the file.
- Catches extraction exceptions (corrupted file) and returns the same
  `{"error": ...}` shape with status 422 rather than a 500.
- No database writes — this endpoint is stateless; it only returns extracted
  text for the client to review and later submit via the existing save
  endpoint.

### 5. Dependencies

`pypdf` and `python-docx`, installed into the same Python environment currently
running `web_app.py` (`~/Library/Python/3.13/lib/python/site-packages`, per
`ps aux` — the process was started with
`python -m uvicorn web_app:app --host 0.0.0.0 --port 8000`).

`web_app.py` runs with `reload=False`, so the running server process needs a
restart after this change for the new endpoint to be live. Template changes
(Jinja2, no caching observed) do not require a restart on their own, but since
this feature also needs the new endpoint, a restart is required regardless.

## Data flow summary

```
Type it out:  4 required boxes + 1 optional box
                    │ (concatenate client-side, labeled)
                    ▼
              POST /api/profile/save { project_description: "<blob>" }  (unchanged)

Upload:       .pdf/.docx file
                    │
                    ▼
              POST /api/profile/extract-file  →  { text }
                    │ (shown in editable textarea, user reviews/edits)
                    ▼
              POST /api/profile/save { project_description: "<edited text>" }  (unchanged)
```

## Error handling

- Empty required box(es) in "Type it out" mode → inline validation message,
  save blocked (matches existing pattern in `saveProfile()`).
- Unsupported file type / oversized file → client-side check before upload
  (based on file extension/size) plus server-side re-validation (never trust
  the client alone).
- Extraction yields no usable text → friendly error, suggest the other tab.
- Network error on either endpoint → existing `catch` pattern in
  `saveProfile()`/new upload handler, shown as inline status text.

## Testing plan

- Manual: fill all 4 required boxes + optional box, save, confirm
  `project_description` in the DB matches the labeled concatenation format.
- Manual: leave a required box empty, confirm save is blocked with a clear
  message.
- Manual: upload a real `.pdf` with a text layer, confirm extracted text
  appears and is editable, save, confirm DB content matches edited text.
- Manual: upload a real `.docx`, same check.
- Manual: upload a scanned/image-only PDF (or a corrupted file), confirm the
  friendly error appears and no partial/garbage data is saved.
- Manual: switch between tabs before saving, confirm only the active tab's
  content is submitted.
