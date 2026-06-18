# DePaul Faculty Matcher

A semantic search tool that helps people find DePaul faculty by **what they research**,
not just by name or department. Ask a plain-English question like
*"who studies fish evolution?"* and it returns the faculty whose research is the
closest match by meaning.

## How it works

The project is a pipeline that builds a searchable index of faculty research:

1. **Roster** — the official list of faculty (name, title, department, college, email,
   bio link), cleaned from the university directory.
2. **Research text** — each faculty member's research interests and publications,
   collected from their public DePaul bio page.
3. **Publications & topics** — additional research output pulled from OpenAlex,
   a free and open database of scholarly work.
4. **Semantic search** — every research summary is turned into an embedding so
   questions can be matched to faculty by meaning.

## Scripts

| File | What it does |
|------|--------------|
| `extract_depaul_faculty.py` | Pulls DePaul researchers + publications from OpenAlex. |
| `enrich_bios2.py` | Reads each faculty bio page and extracts research interests + publications. |
| `inspect_bios.py` | Diagnostic: reports which section headings the bio pages use. |
| `search_faculty.py` | The matching engine — ask a question, get the best-matching faculty. |

## Running the search

```bash
pip3 install --user sentence-transformers numpy
python3 search_faculty.py
```

Then type questions at the `Ask:` prompt. Type `quit` to exit.

## Status

Working prototype. Next steps: serve it as a web app with a chat interface, add a
knowledge-graph layer for relationship queries (co-authors, shared grants), and let
faculty log in to claim and update their own profiles.

## Note on data

Faculty information is collected from public university pages for the purpose of this
project. Data files are kept in this private repository and are not for redistribution.
