# DePaul Faculty Matcher — Project History

A summary of what was built, why each decision was made, and the current state.
Written to answer technical questions from a manager or collaborator.

---

## What the system does

A tool that takes a natural-language research description (e.g., "I'm working on AI for predicting hospital readmissions") and returns the 5 most relevant DePaul University faculty members, ranked by research fit. It also explains *why* each person matches and shows a relevant publication if one exists.

The chatbot interface lets users have a back-and-forth conversation — they describe their research, get recommendations, ask follow-up questions, and refine results.

---

## Phase 1 — Data Collection

**Goal:** Get a structured list of all DePaul full-time faculty with their research areas.

**What we did:**
- Scraped DePaul's faculty directory (`1_extract_faculty.py`) to get name, title, department, college, email, and bio URL for ~1,400 people
- Filtered to full-time faculty only (~700 records)
- Fetched research summaries and course lists from individual faculty bio pages (`2_enrich_bios.py`)
- Used OpenAlex (free academic database) to pull additional research topic tags for faculty who had bare-bones bios (`3_enrich_openalex.py`)

**Result:** `data/depaul_faculty_enriched.json` — 699 full-time faculty with structured data.

**Problem we hit:** ~250 faculty (Theatre School, Law, parts of Science) had no research summary and no courses — just a name and title. These remain unsearchable unless publications are found.

---

## Phase 2 — Database

**Goal:** Move the JSON data into SQLite for fast querying and filtering.

**What we did:**
- `4_db_setup.py` loads the JSON into `faculty.db` with one row per faculty member
- `5_fix_data.py` cleaned data quality issues found during auditing:
  - Non-breaking spaces (`\xa0`) in 101 summaries
  - Zero-width spaces in 19 department names
  - Address/phone boilerplate at the end of 45 "courses taught" fields
  - 7 summaries that were too short to be useful (e.g., "Design", "and", "is mathematics")
  - 6 faculty listed under multiple colleges (kept first one for consistency)

**Result:** Clean `faculty.db` with 699 faculty, 443 of which are searchable (have a summary or course list).

---

## Phase 3 — Search Engine (SPECTER2 + Hybrid Scoring)

**Goal:** Find faculty by semantic meaning, not just keyword matching.

**Why SPECTER2:** It's an AI model trained specifically on academic papers, so it understands that "machine learning" and "neural networks" are related, or that "bilingual acquisition" belongs under linguistics. Regular keyword search would miss these connections. SPECTER2 produces a 768-dimensional embedding vector per text.

**How the search works:**
1. Encode every faculty member's research summary into a SPECTER2 vector (768 numbers representing their research)
2. Encode the user's query the same way
3. Compute cosine similarity between query and every faculty member
4. Combine with keyword overlap score: `final = α × SPECTER2_similarity + (1−α) × keyword_score`
5. Apply diversity filter: maximum 2 results per department (prevents one discipline dominating)
6. Return top 5

The alpha weight shifts based on query length: short queries (1–2 words) weight SPECTER2 more; detailed queries weight keywords more.

**Result:** Saved in `faculty_index.pkl` (pre-computed faculty embeddings so search is instant).

---

## Phase 4 — Publication Enrichment

**Goal:** Give each faculty member a set of their actual published papers, so the system can show a directly relevant publication alongside each result.

**Challenge:** DePaul's website doesn't list publications for most faculty. We had to fetch them from external databases.

**What we built:**
- `6_fetch_papers.py` — queries OpenAlex (free academic database), finds each faculty member's author profile, downloads their top 20 cited papers
- `7_fetch_papers_s2.py` — same but using Semantic Scholar + CrossRef as backup sources, for faculty OpenAlex didn't cover

**Bug we found and fixed:** The fetch scripts were matching faculty by name, and some faculty share names with famous researchers at other universities. This caused papers like *The Human Genome Project* and a *COVID-19 vaccine trial* to be attributed to DePaul professors who happen to share the same last name.

**Fix applied in `8_clean_papers.py`:**
1. Delete any paper with >2,000 citations (landmark papers are almost never from DePaul faculty)
2. Verify topic coherence: use SPECTER2 to check that each paper's embedding is close to the faculty member's own research embedding; delete if too far off

This removed 1,112 misattributed papers (from 5,613 → 4,501).

**Prevention (added to fetch scripts):** Now the fetch code requires two conditions before accepting an author match:
- Last name must match exactly (not just any word)
- At least one research topic keyword from the faculty's bio must overlap with the OpenAlex author's topic tags

**Result:** `paper_index.pkl` — per-paper SPECTER2 embeddings for 4,501 papers across ~400 faculty.

---

## Phase 5 — Result Quality Improvements

Several rounds of fixing what showed up wrong in actual results:

**Problem 1 — ML professor bias**
Querying "network security intrusion detection" returned an education professor (no security background) ranked #1 because SPECTER2 found abstract semantic overlap. She had 0 keyword hits but still scored high.

*Fix:* Added `zero_kw_penalty()` — when a query has 3+ keywords and a result has 0 keyword hits, multiply its score by 0.50. When 4+ keywords and fewer than 26% keyword overlap, multiply by 0.75. This eliminated false positives without affecting genuinely relevant results.

**Problem 2 — "Why they match" showing useless explanations**
The match explanation was picking sentences like "Peter Bernstein is an instructor of economics" (a biographical opener) or "Journal of Banking and Finance" (just a publication venue name).

*Fix:* Rewrote `explain_match()`:
- Detects and skips biographical openers (sentences starting with the person's name or "Dr. X")
- Penalizes sentences that look like journal name lists
- Prefers sentences containing research action verbs ("investigates", "develops", "examines")
- Expands short fragments: "Corporate Finance" → "Their work focuses on corporate finance"

**Problem 3 — Wrong publications shown**
Before the citation cap was added, the results showed a DePaul music professor with a euphonium recital paper and a healthcare professor with an Adam Steuer COVID vaccine paper (because the actual "Adam Steuer" at a major research hospital shares a name with the DePaul professor). Fixed by `8_clean_papers.py`.

---

## Phase 6 — Conversational Chatbot

**Goal:** Replace the command-line search with a natural back-and-forth conversation.

**What we built (`chatbot.py`):**
- Uses an LLM (configurable: OpenAI, Claude, Ollama, Gemini, etc.) as the "brain"
- The LLM receives the conversation history and decides: should I search for faculty, ask a clarifying question, or just answer?
- Faculty search (SPECTER2) is exposed as a tool the LLM can call
- The LLM extracts a clean research topic from the user's conversational message, calls the search, and formats results as natural prose — not a raw list
- Conversation history is maintained across turns so the user can say "someone more applied" or "anyone in the law school?" and get refined results

**Provider-agnostic design:** Uses LiteLLM, a unified interface supporting 100+ LLM providers. Users set two environment variables — no code changes needed to switch providers.

```
# OpenAI
export OPENAI_API_KEY=sk-...
export CHATBOT_MODEL=gpt-4o-mini

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export CHATBOT_MODEL=claude-haiku-4-5-20251001

# Local (Ollama)
export CHATBOT_MODEL=ollama/llama3
```

---

## Phase 7 — Everyone Stays Searchable

**Problem found during auditing:** the faculty loader required a research summary, courses, or publications before it would index someone. That silently dropped ~44 people — overwhelmingly performing-arts faculty who don't publish papers and whose bio pages had nothing scrapeable — out of the index entirely. Searching their own name returned nothing.

**Fix:** every faculty member gets an index entry. Those with research text get a rich one; those without get a minimal directory entry (name, title, department) so at least name and unit searches find them.

The same conflation appeared in the publication-fetch stages, which only looked up faculty who already had a scraped bio. Bio presence and publication record are independent — a new hire with an unpopulated profile page can have a decade of papers. Both stages now consider everyone.

---

## Phase 8 — Accounts, Profiles, and Self-Editing

**Goal:** let faculty claim their own record and correct what we scraped about them.

- Email/password accounts (`auth.py`), with sessions in SQLite rather than a module-level dict — the previous version signed everyone out on restart, so a deploy logged users out mid-task.
- A profile links an account to a faculty record: bio, research interests, and a confirmed publication list.
- **Self-edits live in a separate `faculty_overrides` table**, keyed by email rather than by `faculty_id`. This matters: the pipeline rebuilds `faculty` from scratch on each run and AUTOINCREMENT reassigns ids, so anything keyed on `faculty_id` would be silently reattached to the wrong person. Keying on email means a re-import can never overwrite or misattribute a self-edit.
- Only the account whose email matches the faculty record can edit that record — otherwise anyone could rewrite anyone's listing.
- Faculty can upload CVs, papers, or grant documents (`doc_extract.py` handles PDF/DOCX), or link a Google Scholar profile to import publications the pipeline missed.

Editing had to be genuinely usable, not merely possible: the edit form opens pre-filled with everything we currently believe, so correcting a misattributed publication is unticking a box rather than redoing setup.

---

## Phase 9 — Projects and Structured Proposals

**Goal:** move from "find me collaborators" to "help me write the proposal."

A **project** is one piece of research, with its own proposal, its own conversation history, and its own matches. Researchers have several; the original design allowed exactly one per person for life, which the schema enforced with `UNIQUE(profile_id)` and which had to be migrated out.

Each project has a **proposal** — a structured record with one column per section, built up over the conversation and rendered live in a panel beside the chat. Sections the researcher hand-edits are recorded in `edited_sections` and skipped by the advisor, so it can't silently overwrite someone's own wording; handing a section back is explicit.

Proposals export to `.docx` with a reference list accumulated from the literature searches.

---

## Phase 10 — The Staged Advisor

**Goal:** stop the advisor from writing a polished proposal for a vague or already-answered question.

Earlier versions started drafting sections immediately. A proposal built on an unspecific problem reads well and is useless, and one built on a solved problem costs the researcher months. So the conversation now moves through four gated stages, where **position is determined by what's saved in the proposal, not by conversational memory** — a reload, a week away, or a truncated history can't lose the place.

1. **Specify the problem.** Asks clarifying questions only. It explicitly does *not* suggest topics — the researcher already has an idea, and the job is drawing specificity out of them. Won't advance without a specific population/setting, a specific mechanism, a stated objective, and 2–4 evidence-answerable research questions.
2. **Test novelty.** Searches OpenAlex live, reports what actually exists, and gives a plain verdict — including "already well covered" when that's true. If the ground is crowded, it offers concrete novelty moves; after two unsuccessful rounds it stops looping and offers honest ways forward (replicate, pivot, or proceed knowing the contribution is incremental) rather than pretending a fresh angle is one more question away.
3. **Write the problem statement.** Drafted in the chat, revised until the researcher agrees, then saved as the anchor everything else must serve.
4. **Build the proposal.** Section by section, one question at a time.

A "research landscape" panel visualises each literature search as ranked closeness bars, so the novelty verdict and the evidence for it are visible side by side.

**Guidance from the AI Institute review (July 2026)** shaped several parts of this:
- The literature review targets **3–5 recent studies bearing directly on the gap**, not a broad sweep. Padding the list makes the gap harder to see, not easier.
- The research gap is established **after** the research questions are specific, not by jumping straight to a literature review.
- A dedicated **"role of AI and data science"** section asks where AI actually enters the project — as method, as subject, or honestly neither. "AI is peripheral here, and here is why" is a savable answer; bolting a method onto research that doesn't need it produces a weaker proposal.
- **Ethical considerations** and an **abstract** (written last) complete the section set.

---

## Phase 11 — Fine-Tuning SPECTER2 (Optional)

**Goal:** test whether a DePaul-specific model beats off-the-shelf SPECTER2.

- `9_generate_training_data.py` has an LLM write 5 plausible search queries per faculty member — the phrasings a grad student or external researcher would actually type, ranging from lay terms to technical ones. Each becomes a positive (query, bio) pair.
- `10_finetune_specter2.py` trains three configurations with MultipleNegativesRankingLoss (every other bio in the batch acts as a negative, so no explicit negatives are needed), evaluates each on a held-out test set, and keeps the best.

Set `FINETUNED_MODEL` to the output directory to use it; `search.py` prefers it over the adapter and base models. Weights live in `models/` and are not committed — 420MB, and retrainable from the pipeline.

---

## Phase 12 — Describing People by Current Work

**Problem:** for faculty with no scraped bio, the searchable research summary was synthesised from their **most-cited** titles. Citations accumulate with age, so that ranking structurally favours old work. One professor with 83 papers spanning 1981–2025, currently publishing on political marketing, was being embedded — and therefore matched — on a 1991 consumption-values paper with 9,527 citations.

**Fix:** the summary now leads with the 9 most recent titles, followed by up to 3 most-cited of any age. Recency identifies what someone works on now; the seminal tail keeps them reachable for the work they're known for. The per-paper embedding index already covers every paper regardless of era, so broader expertise is preserved for matching.

---

## Current State

*As of July 2026.*

| Component | Status |
|-----------|--------|
| Faculty database | 1,389 faculty indexed; 1,339 have a research summary or course list |
| Publications | 18,665 papers across 478 faculty |
| Embedding model | `specter2_base` in the running app — the proximity adapter is preferred but `adapters` is not currently installed |
| Advisor | Four gated stages, live literature search, structured proposal with `.docx` export |
| Accounts | Email/password, SQLite-backed sessions, faculty self-editing |
| Coverage gaps | 50 faculty have neither a bio nor courses; 44 of those also have no publications found yet. Mostly performing arts, where there is often nothing to scrape and nothing indexed to find. They still appear in name and department searches. |
| Deployment | Not yet deployed — runs locally. See README. |

---

## File Structure

```
depaul-faculty-matcher/
├── search.py          ← CLI search tool (also imported by chatbot)
├── chatbot.py         ← conversational interface (run this for the chatbot)
│
├── pipeline/          ← run once, in order, to build the database from scratch
│   ├── 1_extract_faculty.py     scrape DePaul faculty directory
│   ├── 2_enrich_bios.py         fetch individual bio pages
│   ├── 3_enrich_openalex.py     add OpenAlex research topics
│   ├── 4_db_setup.py            load JSON → faculty.db
│   ├── 5_fix_data.py            clean data quality issues
│   ├── 6_fetch_papers.py        fetch publications via OpenAlex
│   ├── 7_fetch_papers_s2.py     fetch publications via Semantic Scholar + CrossRef
│   ├── 8_clean_papers.py        remove misattributed papers
│   ├── 9_generate_training_data.py   synthetic query/bio pairs for fine-tuning
│   ├── 10_finetune_specter2.py       train + evaluate 3 configs, keep the best
│   ├── 11_merge_scholar_csv.py       repair tool: merge a Scholar export
│   └── 12_recover_missed_faculty.py  repair tool: re-scrape people step 1 missed
│
├── web_app.py         ← FastAPI app: routes, advisor prompt + tools, proposals
├── search.py          ← matching engine: embeddings, ranking stages
├── auth.py            ← password hashing, sessions
├── doc_extract.py     ← PDF/DOCX text extraction for uploads
├── templates/         ← server-rendered pages (_shell.html = shared CSS + nav)
├── tests/             ← pytest suite
├── docs/superpowers/  ← design specs and implementation plans, by date
│
├── data/              ← raw source files (input to pipeline)
│   ├── depaul_faculty_enriched.json
│   ├── depaul_faculty_enriched.csv
│   ├── depaul_roster_clean.json
│   └── depaul_roster_clean.csv
│
└── generated, not in git:
    faculty.db          rebuild with pipeline/
    faculty_index.pkl   SPECTER2 embeddings; rebuilds automatically when text changes
    paper_index.pkl     per-paper embeddings; same
    models/             fine-tuned weights (~420MB); retrain with pipeline steps 9-10
    uploads/            faculty-uploaded CVs and documents
    start_server.sh     local launch script; holds an API key
```

---

## Key Technical Decisions & Why

| Decision | Reason |
|----------|--------|
| SPECTER2 over general embeddings (e.g. text-embedding-ada) | Trained on 75M academic paper citations; understands academic domain language and cross-field relationships |
| Hybrid score (SPECTER2 + keywords) | Pure SPECTER2 had false positives where abstract similarity fooled the model; keywords add a relevance sanity check |
| SQLite over Postgres/flat JSON | Single-file, no server, easy to move. Dataset is small (700 rows). |
| LiteLLM for chatbot provider routing | Users have different API access; no reason to lock into one provider |
| Citation cap (>2000) to detect misattribution | More reliable than topic coherence alone — SPECTER2 clusters all biomedical text broadly, so a nursing paper from a random researcher could still score high against a DePaul nurse professor |
| Diversity filter (max 2 per department) | Without it, queries like "machine learning" return 5 Computing professors; diversity makes results more useful for finding collaborators across disciplines |
| Advisor stage determined by saved proposal, not chat memory | A reload, a week away, or a truncated history would otherwise lose the researcher's place and restart questions they already answered |
| Self-edits keyed by email in a separate table | The pipeline rebuilds `faculty` and AUTOINCREMENT reassigns ids, so anything keyed on `faculty_id` would silently reattach a person's corrections to someone else |
| Recency-led research summaries | Citations accumulate with age, so ranking by them describes people by their oldest successful work rather than their current direction |
| Literature review capped at 3-5 directly relevant studies | Per AI Institute guidance: a padded review obscures the gap it exists to establish |
| One `proposals` row per project, not per person | The original `UNIQUE(profile_id)` capped a researcher at one proposal for life |
