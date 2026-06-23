#!/usr/bin/env python3
"""
enrich_openalex.py
------------------
For every full-time faculty member with no research summary, queries the
OpenAlex API by name and synthesises a summary from their paper titles and
research topics. Updates depaul_faculty_enriched.json and rebuilds faculty.db.

Run once (takes ~5-10 min depending on how many faculty are missing):
    python3 enrich_openalex.py

Then re-run db_setup.py and delete faculty_index.pkl so search.py rebuilds
the embeddings with the new data.
"""
import json, time, re, sqlite3, os
import requests

YOUR_EMAIL   = "aruzhanzhengis19@gmail.com"
JSON_FILE    = "depaul_faculty_enriched.json"
DB_FILE      = "faculty.db"
OPENALEX_BASE = "https://api.openalex.org"
PAUSE        = 0.2   # seconds between requests (polite pool)
MAX_PAPERS   = 15    # paper titles to use per person

session = requests.Session()


def get(url, params):
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


def find_author(name):
    """Search OpenAlex for a DePaul author by name. Returns best match or None."""
    data = get(f"{OPENALEX_BASE}/authors", {
        "search": name,
        "filter": "last_known_institutions.ror:04g2y3253",  # DePaul ROR ID
        "per-page": 5,
        "select": "id,display_name,topics,works_count",
    })
    if not data:
        return None
    results = data.get("results", [])
    if not results:
        # Retry without institution filter — some faculty aren't indexed under DePaul
        data = get(f"{OPENALEX_BASE}/authors", {
            "search": name,
            "per-page": 3,
            "select": "id,display_name,topics,works_count,last_known_institutions",
        })
        if not data:
            return None
        results = data.get("results", [])
        # Keep only results that mention DePaul in any institution
        results = [
            r for r in results
            if any("depaul" in (i.get("display_name") or "").lower()
                   for i in (r.get("last_known_institutions") or []))
        ]
    if not results:
        return None
    # Pick the one whose name is closest to our query
    query_parts = set(name.lower().split())
    def name_overlap(r):
        parts = set(r.get("display_name","").lower().split())
        return len(query_parts & parts)
    return max(results, key=name_overlap)


def fetch_paper_titles(author_id):
    short = author_id.rsplit("/", 1)[-1]
    data = get(f"{OPENALEX_BASE}/works", {
        "filter": f"author.id:{short}",
        "per-page": MAX_PAPERS,
        "sort": "cited_by_count:desc",
        "select": "title,publication_year",
    })
    if not data:
        return []
    return [w["title"] for w in data.get("results", []) if w.get("title")]


def build_summary(author):
    """Synthesise a research summary from OpenAlex topics + paper titles."""
    lines = []

    topics = [t.get("display_name","") for t in (author.get("topics") or [])][:8]
    if topics:
        lines.append("Research areas include " + ", ".join(topics) + ".")

    titles = fetch_paper_titles(author["id"])
    time.sleep(PAUSE)
    if titles:
        lines.append("Selected publications: " + " | ".join(titles[:10]) + ".")

    return " ".join(lines)


def main():
    with open(JSON_FILE, encoding="utf-8") as f:
        people = json.load(f)

    missing = [
        p for p in people
        if p.get("employment_status") == "Full Time"
        and not (p.get("research_summary") or "").strip()
    ]
    print(f"Full-time faculty with no research summary: {len(missing)}")
    print("(Already-enriched faculty are skipped automatically on re-run)\n")
    print("Querying OpenAlex...\n")

    enriched = 0
    for idx, p in enumerate(missing, 1):
        name = p.get("name", "")
        print(f"[{idx}/{len(missing)}] {name}", end=" ... ", flush=True)

        author = find_author(name)
        time.sleep(PAUSE)

        if not author:
            print("not found in OpenAlex")
            continue

        summary = build_summary(author)
        if not summary.strip():
            print("found but no usable data")
            continue

        p["research_summary"] = summary
        if not p.get("research_topics"):
            p["research_topics"] = [
                t.get("display_name","")
                for t in (author.get("topics") or [])[:6]
            ]
        enriched += 1
        print(f"OK ({author.get('works_count',0)} works)")

    print(f"\nEnriched {enriched} / {len(missing)} previously-missing faculty.")

    # Save back to JSON
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(people, f, indent=2, ensure_ascii=False)
    print(f"Updated {JSON_FILE}")

    # Rebuild SQLite DB
    print("Rebuilding faculty.db ...")
    os.system("python3 db_setup.py")

    # Prompt to rebuild embedding index
    print("\nDone. Now delete faculty_index.pkl and re-run search.py to rebuild the index:")
    print("  rm faculty_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
