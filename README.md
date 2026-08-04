# DePaul Faculty Matcher

A research-proposal advisor for DePaul faculty, built for the AI Institute.

A researcher describes what they're working on. The advisor interviews them until
the problem is specific, checks against live literature whether it's actually new,
builds it into a full academic proposal, and matches them with DePaul colleagues
whose recent work complements theirs.

Underneath is a semantic search index over **1,389 DePaul faculty** and **18,665
publications**, so "who works on fairness auditing in clinical models?" returns
people by what they research, not by keyword overlap with their job title.

---

## What it does

**1. Profile.** You find yourself in the faculty database; we pre-fill a bio,
research interests, and publication list from your DePaul page and OpenAlex. All
of it is editable, because scraped data is wrong for somebody. You can upload a CV
or link a Google Scholar profile to bring in more.

**2. The advisor** (`/advisor`) works through four stages, in order. Which stage
you're in is determined by what's saved in the proposal, not by conversational
memory, so it survives a reload or a week away:

| Stage | What happens | Gate to the next stage |
|-------|--------------|------------------------|
| 1 — Specify the problem | Asks focused questions until there's a specific population/setting, a specific mechanism, a stated objective, and 2–4 evidence-answerable research questions. Deliberately does **not** suggest topics. | Research questions saved |
| 2 — Test novelty | Searches live literature (OpenAlex), reports what exists, and gives a plain verdict. If the ground is covered, offers concrete novelty moves; if it stays covered after two rounds, offers an honest way forward rather than looping. | A saved "Nobody has yet ___, and this project will" claim |
| 3 — Problem statement | Drafts the anchor paragraph in the chat and revises until you agree. | Problem statement saved |
| 4 — Build the proposal | Background, objectives, research questions, literature review, methodology, **the role of AI**, ethical considerations, expected outcomes, and an abstract written last. | — |

**3. Matching.** Collaborators surface per project, with an explanation grounded
in the specific papers that make each person relevant.

**4. Export.** The finished proposal downloads as a `.docx` with a reference list
accumulated from the literature searches.

---

## Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-..."                  # or any LiteLLM-supported provider
export CHATBOT_MODEL="anthropic/claude-sonnet-5"
.venv/bin/python3 -m uvicorn web_app:app --port 8000
```

Then open http://localhost:8000. The database (`faculty.db`) and embedding indexes
(`*.pkl`) are generated, not committed — see **Rebuilding the data** below.

Every LLM call goes through LiteLLM, so switching providers is one environment
variable. `CHATBOT_MODEL="ollama/llama3.2:3b"` runs it against a free local model.

---

## Models and services

| Component | What it is | Why |
|-----------|-----------|-----|
| **SPECTER2** (`allenai/specter2_base`) | 768-dim embeddings for faculty research text and papers | Trained on 75M academic citation pairs, so it knows "bilingual acquisition" belongs near linguistics. General-purpose embeddings don't. |
| **SPECTER2 + proximity adapter** | Better retrieval accuracy than the base model | Used automatically **if the `adapters` package is installed**. It currently isn't, so the app runs on `specter2_base` — `pip install adapters` is the upgrade. |
| **Fine-tuned SPECTER2** (`models/specter2_depaul_*`) | SPECTER2 further trained on synthetic (query → faculty bio) pairs | Optional, and takes priority over both above. Set `FINETUNED_MODEL` to the directory to use it. See pipeline steps 9–10. |
| **ms-marco-MiniLM-L-12-v2** cross-encoder | Reranks the top ~25 candidates | ~120MB, ~80ms for 25 pairs, no GPU. Catches cases where embedding similarity is fooled by abstract-sounding text. |
| **LLM via LiteLLM** (Claude Sonnet by default) | Query expansion, match explanation, and the advisor conversation | Provider-agnostic — no reason to lock the project to one vendor. |
| **OpenAlex** | Publications, research topics, live literature search | Free, open, no API key. |
| **Semantic Scholar + CrossRef** | Publication fallback | Catches people OpenAlex misses. |
| **FastAPI + SQLite** | Web app and storage | 1,389 rows is a small dataset. A single file that moves with the repo beats a server. |

**Search runs in three stages:** SPECTER2 cosine similarity + keyword overlap to
get ~25 candidates → cross-encoder rerank to ~7 → LLM reranker for the final
ordering and the explanation. A diversity filter caps results per department so
"machine learning" doesn't return five people from Computing.

---

## Rebuilding the data

The pipeline is a numbered sequence — each step reads what the previous one wrote.

```bash
cd pipeline
python3 1_extract_faculty.py       # scrape the DePaul faculty directory
python3 2_enrich_bios.py           # research summaries + courses from bio pages
python3 3_enrich_openalex.py       # OpenAlex research topics for thin bios
python3 4_db_setup.py              # JSON → faculty.db
python3 5_fix_data.py              # clean encoding and boilerplate artifacts
python3 6_fetch_papers.py          # publications via OpenAlex
python3 7_fetch_papers_s2.py       # publications via Semantic Scholar + CrossRef
python3 8_clean_papers.py          # drop misattributed papers
```

Steps 6–7 are resumable (Ctrl-C is safe; progress saves per faculty member) and
take roughly an hour against rate-limited public APIs.

Optional, for the fine-tuned model:

```bash
python3 9_generate_training_data.py   # LLM writes 5 plausible search queries per person
python3 10_finetune_specter2.py       # trains 3 configs, evaluates, keeps the best
```

`11_merge_scholar_csv.py` and `12_recover_missed_faculty.py` are repair tools for
specific data problems, not part of a normal build.

Embedding indexes rebuild automatically when the underlying text changes — the
cache is keyed on a fingerprint of the input.

---

## Repository map

```
web_app.py          FastAPI app: routes, advisor prompt + tools, proposal storage
search.py           Matching engine: embeddings, ranking stages, faculty loading
chatbot.py          LiteLLM wrapper
auth.py             Password hashing and session handling
doc_extract.py      PDF/DOCX text extraction for uploaded documents
templates/          Server-rendered pages (_shell.html holds shared CSS + nav)
pipeline/           Numbered data-collection scripts, run in order
tests/              pytest suite
docs/superpowers/   Design specs and implementation plans, by date
```

Generated and not committed: `faculty.db`, `faculty_index.pkl`, `paper_index.pkl`,
`models/` (~420MB), `uploads/`, `start_server.sh` (holds an API key).

## Testing

```bash
.venv/bin/python3 -m pytest tests -q
```

## A note on data

Faculty information is collected from public university pages for this project.
Faculty self-edits are stored separately from scraped data (`faculty_overrides`),
so re-running the pipeline can never overwrite something a person corrected
about themselves.

> **⚠️ This repository is public, and `data/` is committed to it.**
> `data/depaul_faculty_enriched.json` holds 2,270 faculty records including
> 2,240 email addresses. Every one of those is already published on DePaul's
> own directory, so this is aggregation rather than disclosure — but an
> aggregated, machine-readable copy of every faculty email is exactly what
> scrapers want, and it is more exposure than any individual page gives.
>
> An earlier version of this note claimed the repository was private. It is
> not. Decide deliberately whether it should be: making it private, or
> gitignoring `data/`, are both one-step changes.

The generated database (`faculty.db`) is **not** committed, and must not be —
it additionally holds user accounts, password hashes, and session tokens. Use
`pipeline/make_seed_db.py` to produce a public-data-only copy for deployment.
