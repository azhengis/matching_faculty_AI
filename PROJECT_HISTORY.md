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

## Current State

| Component | Status |
|-----------|--------|
| Faculty database | 699 full-time faculty, 443 searchable |
| Publications | 4,501 papers across ~400 faculty |
| Search accuracy | ~97% precision@5 across 12 disciplines tested |
| Chatbot | Working, provider-agnostic |
| Coverage gaps | ~250 faculty (Theatre, Law, some Science/Health) have no bio and no papers yet |

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
│   └── 8_clean_papers.py        remove misattributed papers
│
├── data/              ← raw source files (input to pipeline)
│   ├── depaul_faculty_enriched.json
│   ├── depaul_faculty_enriched.csv
│   ├── depaul_roster_clean.json
│   └── depaul_roster_clean.csv
│
└── faculty.db         ← generated (not in git; rebuild with pipeline/)
    faculty_index.pkl  ← generated (SPECTER2 embeddings; rebuild with search.py)
    paper_index.pkl    ← generated (paper embeddings; rebuild with search.py)
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
