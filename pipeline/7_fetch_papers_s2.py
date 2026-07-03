#!/usr/bin/env python3
"""
fetch_papers_s2.py
------------------
Second-pass publication enrichment using Semantic Scholar and CrossRef.
Runs AFTER fetch_papers.py (OpenAlex) and fills in faculty still missing papers.

  Semantic Scholar  — strong abstracts, great for CS / health / sciences
  CrossRef          — broadest net (all DOI-registered papers), titles only

Run:
    python3 fetch_papers_s2.py

Safe to interrupt and re-run — skips faculty already in the papers table.
After finishing:
    rm -f faculty_index.pkl paper_index.pkl && python3 search.py
"""
import time, sqlite3, re, os
import requests

YOUR_EMAIL    = "aruzhanzhengis19@gmail.com"
_ROOT         = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE       = os.path.join(_ROOT, "faculty.db")
S2_BASE       = "https://api.semanticscholar.org/graph/v1"
CROSSREF_BASE = "https://api.crossref.org/works"
S2_PAUSE      = 1.5    # ~1 req/sec without API key
CR_PAUSE      = 0.5
MAX_PAPERS    = 20

session = requests.Session()
session.headers.update({"User-Agent": f"DePaulFacultyMatcher/1.0 (mailto:{YOUR_EMAIL})"})

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
# HTTP helper
# ---------------------------------------------------------------------------

def api_get(url, params=None, headers=None, pause=S2_PAUSE):
    for attempt in range(5):
        try:
            r = session.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 10 * (2 ** attempt)
                print(f"    rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code in (404, 400):
                return None
            # Other errors: brief wait and retry
            time.sleep(2 ** attempt)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            wait = 2 ** attempt
            print(f"    network error, retry in {wait}s...")
            time.sleep(wait)
    return None


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

_S2_STOPWORDS = {"research", "studies", "university", "professor", "department",
                 "science", "study", "analysis", "data", "using", "based",
                 "approach", "methods", "results", "with", "from", "have",
                 "been", "were", "their", "which", "these", "those", "that"}

def _summary_keywords(text):
    """Meaningful words (4+ chars) from a faculty research summary."""
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())
            if w not in _S2_STOPWORDS}


def s2_find_author(name, faculty_summary=""):
    """Return best S2 authorId for a DePaul faculty member, or None."""
    data = api_get(f"{S2_BASE}/author/search", params={
        "query": name,
        "fields": "authorId,name,affiliations,paperCount,externalIds",
        "limit": 5,
    }, pause=S2_PAUSE)
    if not data:
        return None

    results = data.get("data", [])
    if not results:
        return None

    # Prefer results that mention DePaul in affiliations
    depaul_results = [
        r for r in results
        if any("depaul" in (a or "").lower() for a in (r.get("affiliations") or []))
    ]

    candidates = depaul_results if depaul_results else results

    # Among candidates, pick the one whose name overlaps most with our query
    query_words = set(name.lower().split())
    def name_score(r):
        rwords = set((r.get("name") or "").lower().split())
        overlap = len(query_words & rwords)
        has_depaul = any("depaul" in (a or "").lower() for a in (r.get("affiliations") or []))
        return (overlap, has_depaul, r.get("paperCount") or 0)

    best = max(candidates, key=name_score)

    # Require at least a last-name match to avoid false positives
    best_words = set((best.get("name") or "").lower().split())
    last_name = name.lower().split()[-1]
    if last_name not in best_words:
        return None

    # If multiple DePaul candidates exist, require topic keyword overlap with
    # the faculty summary to avoid selecting a namesake in a different field
    summary_kws = _summary_keywords(faculty_summary)
    if summary_kws and len(depaul_results) > 1:
        affil_text = " ".join(
            a for a in (best.get("affiliations") or []) if a
        ).lower()
        if not (summary_kws & set(re.findall(r"[a-z]{4,}", affil_text))):
            # No keyword overlap — try the next best DePaul candidate
            for candidate in sorted(depaul_results, key=name_score, reverse=True):
                cand_affil = " ".join(
                    a for a in (candidate.get("affiliations") or []) if a
                ).lower()
                if summary_kws & set(re.findall(r"[a-z]{4,}", cand_affil)):
                    return candidate.get("authorId")

    return best.get("authorId")


def s2_fetch_papers(author_id):
    """Fetch top papers for a Semantic Scholar author."""
    data = api_get(
        f"{S2_BASE}/author/{author_id}/papers",
        params={
            "fields": "title,abstract,year,citationCount",
            "limit": MAX_PAPERS,
        },
        pause=S2_PAUSE,
    )
    if not data:
        return []

    papers = []
    for w in sorted(data.get("data", []), key=lambda x: x.get("citationCount") or 0, reverse=True):
        title = (w.get("title") or "").strip()
        if not title:
            continue
        papers.append({
            "title":          title,
            "abstract":       (w.get("abstract") or "").strip(),
            "year":           w.get("year"),
            "cited_by_count": w.get("citationCount") or 0,
        })
    return papers


# ---------------------------------------------------------------------------
# CrossRef (fallback — titles only, rarely has abstracts)
# ---------------------------------------------------------------------------

def crossref_fetch_papers(name):
    """Search CrossRef for papers by a DePaul faculty member."""
    # First try with affiliation filter
    for query_affil in ["DePaul University", None]:
        params = {
            "query.author":      name,
            "rows":              MAX_PAPERS,
            "select":            "title,abstract,published,is-referenced-by-count",
            "mailto":            YOUR_EMAIL,
            "sort":              "is-referenced-by-count",
            "order":             "desc",
        }
        if query_affil:
            params["query.affiliation"] = query_affil

        data = api_get(CROSSREF_BASE, params=params, pause=CR_PAUSE)
        if not data:
            continue

        items = (data.get("message") or {}).get("items") or []
        if not items and query_affil:
            continue  # try without affiliation

        papers = []
        for item in items:
            titles = item.get("title") or []
            title  = titles[0].strip() if titles else ""
            if not title:
                continue
            year  = None
            pub   = item.get("published") or {}
            parts = (pub.get("date-parts") or [[]])[0]
            if parts:
                year = parts[0]
            papers.append({
                "title":          title,
                "abstract":       (item.get("abstract") or "").strip(),
                "year":           year,
                "cited_by_count": item.get("is-referenced-by-count") or 0,
            })
        if papers:
            return papers

    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    cur.execute(PAPERS_TABLE_SQL)
    con.commit()

    # All searchable faculty
    all_faculty = cur.execute("""
        SELECT id, name FROM faculty
        WHERE TRIM(COALESCE(research_summary,'')) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
        ORDER BY name
    """).fetchall()

    # Skip anyone already in the papers table
    done_ids = {r[0] for r in cur.execute("SELECT DISTINCT faculty_id FROM papers")}
    todo = [(fid, name) for fid, name in all_faculty if fid not in done_ids]

    total_already = len(done_ids)
    print(f"Total searchable faculty : {len(all_faculty)}")
    print(f"Already have papers (OpenAlex pass): {total_already}")
    print(f"To look up now           : {len(todo)}")
    print("Trying Semantic Scholar first, CrossRef as fallback...")
    print("(Safe to Ctrl-C and resume — progress is saved per faculty)\n")

    s2_found = cr_found = 0

    for idx, (fac_id, name) in enumerate(todo, 1):
        print(f"[{idx:3d}/{len(todo)}] {name}", end=" ... ", flush=True)

        # --- Semantic Scholar ---
        author_id = s2_find_author(name)
        time.sleep(S2_PAUSE)

        papers = []
        source = None

        if author_id:
            papers = s2_fetch_papers(author_id)
            time.sleep(S2_PAUSE)
            if papers:
                source = "S2"
                s2_found += 1

        # --- CrossRef fallback ---
        if not papers:
            papers = crossref_fetch_papers(name)
            time.sleep(CR_PAUSE)
            if papers:
                source = "CrossRef"
                cr_found += 1

        if not papers:
            print("not found")
            continue

        cur.executemany(
            """INSERT INTO papers (faculty_id, title, abstract, year, cited_by_count)
               VALUES (?,?,?,?,?)""",
            [(fac_id, p["title"], p["abstract"], p["year"], p["cited_by_count"])
             for p in papers],
        )
        con.commit()

        top = papers[0]
        has_abstract = "+" if top["abstract"] else "-"
        print(f"{source} | {len(papers)} papers [{has_abstract}abstract]  (top cited: {top['cited_by_count']}×)")

    # Summary
    total_papers  = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    fac_with_pubs = cur.execute("SELECT COUNT(DISTINCT faculty_id) FROM papers").fetchone()[0]
    con.close()

    print(f"\n{'='*55}")
    print(f"This run  — Semantic Scholar: {s2_found}  CrossRef: {cr_found}")
    print(f"Total faculty with papers in DB : {fac_with_pubs}")
    print(f"Total papers stored             : {total_papers}")
    print(f"\nNext step — rebuild the search index:")
    print(f"  rm -f faculty_index.pkl paper_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
