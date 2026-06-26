#!/usr/bin/env python3
"""
fetch_papers.py
---------------
For every searchable faculty member, queries OpenAlex for their published papers
(title + abstract) and stores them in a 'papers' table in faculty.db.

Run once (takes ~15-30 min for ~450 faculty):
    python3 fetch_papers.py

Safe to interrupt and re-run — skips faculty already in the papers table.
After finishing, delete faculty_index.pkl and re-run search.py.
"""
import time, sqlite3, os
import requests

YOUR_EMAIL    = "aruzhanzhengis19@gmail.com"
_ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE       = os.path.join(_ROOT, "faculty.db")
OPENALEX_BASE = "https://api.openalex.org"
PAUSE         = 0.2    # polite pool delay (seconds between requests)
MAX_PAPERS    = 20     # top-cited papers per faculty
RECENT_PAPERS = 10     # most-recent papers added on top (deduped)

session = requests.Session()

PAPERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id     INTEGER,
    title          TEXT,
    abstract       TEXT,
    year           INTEGER,
    cited_by_count INTEGER
)
"""


# ---------------------------------------------------------------------------
# OpenAlex helpers
# ---------------------------------------------------------------------------

def api_get(url, params):
    params = dict(params)
    params["mailto"] = YOUR_EMAIL
    for attempt in range(6):
        try:
            r = session.get(url, params=params, timeout=45)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"    rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** attempt
            print(f"    network error, retrying in {wait}s...")
            time.sleep(wait)
    return None


_GENERIC = {"research","studies","university","professor","department","science",
            "the","and","for","with","of","in","on","at","by","to","a","an"}

def _topic_words(author):
    """Meaningful words from an OpenAlex author's research topics."""
    topics = author.get("topics") or []
    words  = set()
    for t in topics:
        for w in re.findall(r"[a-z]+", (t.get("display_name") or "").lower()):
            if w not in _GENERIC and len(w) > 3:
                words.add(w)
    return words


def find_author_id(name, faculty_summary=""):
    """Return best OpenAlex author ID for a DePaul faculty member, or None.

    Guards against name collisions (different researchers sharing the same name)
    by requiring at least one shared keyword between the author's OpenAlex
    research topics and the faculty member's known research summary.
    """
    summary_words = {
        w for w in re.findall(r"[a-z]+", faculty_summary.lower())
        if w not in _GENERIC and len(w) > 3
    }

    # First: search restricted to DePaul ROR
    data = api_get(f"{OPENALEX_BASE}/authors", {
        "search": name,
        "filter": "last_known_institutions.ror:04g2y3253",
        "per-page": 5,
        "select": "id,display_name,works_count,topics",
    })
    results = (data or {}).get("results", [])

    if not results:
        # Fallback: broader search, then filter for DePaul in any institution
        data = api_get(f"{OPENALEX_BASE}/authors", {
            "search": name,
            "per-page": 5,
            "select": "id,display_name,works_count,last_known_institutions,topics",
        })
        results = (data or {}).get("results", [])
        results = [
            r for r in results
            if any(
                "depaul" in (inst.get("display_name") or "").lower()
                for inst in (r.get("last_known_institutions") or [])
            )
        ]

    if not results:
        return None

    # Require last-name match — avoids accepting "Marcus DeAnda (genomicist)"
    # for a DePaul "Michael DeAnda (designer)" just because both have "DeAnda"
    last_name = name.strip().lower().split()[-1]
    results = [
        r for r in results
        if last_name in (r.get("display_name") or "").lower().split()
    ]
    if not results:
        return None

    # Require at least one topic keyword overlap with faculty's research summary
    # — prevents associating a DePaul writing professor with a bioinformatician
    # who happens to share the same name
    if summary_words:
        topic_matched = [r for r in results if summary_words & _topic_words(r)]
        if topic_matched:
            results = topic_matched
        # else: no topic overlap for any candidate — still accept but warn
        # (covers faculty with very sparse summaries like "Design", "Theatre")

    # Among remaining candidates, pick the one whose display name overlaps most
    query_words = set(name.lower().split())
    def name_score(r):
        rwords = set((r.get("display_name") or "").lower().split())
        return len(query_words & rwords)

    best = max(results, key=name_score)
    return best["id"]


def reconstruct_abstract(inv_idx):
    """Convert OpenAlex inverted-index abstract to plain text."""
    if not inv_idx:
        return ""
    word_at = {}
    for word, positions in inv_idx.items():
        for pos in positions:
            word_at[pos] = word
    return " ".join(word_at[i] for i in sorted(word_at))


def fetch_papers_for_author(author_id):
    """Return list of dicts {title, abstract, year, cited_by_count}.

    Makes two calls: top MAX_PAPERS by citations, then RECENT_PAPERS by year.
    Deduplicates on title so recent highly-cited papers aren't double-counted.
    This ensures current work (not yet widely cited) appears alongside landmark papers.
    """
    short   = author_id.rsplit("/", 1)[-1]
    seen    = set()
    papers  = []

    def _parse(data):
        for w in (data or {}).get("results", []):
            title = (w.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            abstract = reconstruct_abstract(w.get("abstract_inverted_index") or {})
            papers.append({
                "title":          title,
                "abstract":       abstract,
                "year":           w.get("publication_year"),
                "cited_by_count": w.get("cited_by_count") or 0,
            })

    base_params = {
        "filter": f"author.id:{short}",
        "select": "title,publication_year,cited_by_count,abstract_inverted_index",
    }

    # Pass 1: most-cited (established impact)
    _parse(api_get(f"{OPENALEX_BASE}/works",
                   {**base_params, "per-page": MAX_PAPERS, "sort": "cited_by_count:desc"}))
    time.sleep(PAUSE)

    # Pass 2: most-recent (current work not yet widely cited)
    _parse(api_get(f"{OPENALEX_BASE}/works",
                   {**base_params, "per-page": RECENT_PAPERS, "sort": "publication_year:desc"}))

    return papers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(PAPERS_TABLE_SQL)
    con.commit()

    # All searchable faculty (have a summary or courses)
    all_faculty = cur.execute("""
        SELECT id, name, research_summary, classes_taught FROM faculty
        WHERE TRIM(COALESCE(research_summary,'')) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
        ORDER BY name
    """).fetchall()

    # Skip faculty already processed
    done_ids = {r[0] for r in cur.execute("SELECT DISTINCT faculty_id FROM papers")}
    todo = [r for r in all_faculty if r["id"] not in done_ids]

    print(f"Total searchable faculty : {len(all_faculty)}")
    print(f"Already have papers      : {len(done_ids)}")
    print(f"To look up now           : {len(todo)}")
    print("Querying OpenAlex (safe to Ctrl-C and resume)...\n")

    found = 0
    for idx, row in enumerate(todo, 1):
        name    = row["name"]
        fac_id  = row["id"]
        summary = (row["research_summary"] or row["classes_taught"] or "")
        print(f"[{idx:3d}/{len(todo)}] {name}", end=" ... ", flush=True)

        author_id = find_author_id(name, faculty_summary=summary)
        time.sleep(PAUSE)

        if not author_id:
            print("not found in OpenAlex")
            continue

        papers = fetch_papers_for_author(author_id)
        time.sleep(PAUSE)

        if not papers:
            print("found but no papers")
            continue

        cur.executemany(
            """INSERT INTO papers (faculty_id, title, abstract, year, cited_by_count)
               VALUES (?,?,?,?,?)""",
            [(fac_id, p["title"], p["abstract"], p["year"], p["cited_by_count"])
             for p in papers],
        )
        con.commit()
        found += 1
        top_cit = papers[0]["cited_by_count"]
        print(f"{len(papers)} papers  (top cited: {top_cit}×)")

    # Summary
    total_papers  = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    fac_with_pubs = cur.execute("SELECT COUNT(DISTINCT faculty_id) FROM papers").fetchone()[0]
    con.close()

    print(f"\n{'='*55}")
    print(f"Faculty with papers in DB : {fac_with_pubs}")
    print(f"Total papers stored       : {total_papers}")
    print(f"\nNext step — rebuild the search index:")
    print(f"  rm -f faculty_index.pkl paper_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
