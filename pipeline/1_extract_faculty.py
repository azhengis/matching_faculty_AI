import csv
import json
import time
import sys
import requests

YOUR_EMAIL = "you@depaul.edu"          # <-- put a real email here (polite pool)
INSTITUTION_SEARCH = "DePaul University"
FETCH_PUBLICATIONS = True               # set False to skip publications (much faster)
MAX_PUBS_PER_AUTHOR = 25                # how many recent papers to pull per person
OUTPUT_JSON = "depaul_faculty.json"
OUTPUT_CSV = "depaul_faculty.csv"

BASE = "https://api.openalex.org"
SESSION = requests.Session()


def get(url, params):
    """GET with the polite-pool email, basic retry, and 429 back-off."""
    params = dict(params or {})
    params["mailto"] = YOUR_EMAIL
    for attempt in range(6):
        r = SESSION.get(url, params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:                # rate limited -> wait and retry
            wait = 2 ** attempt
            print(f"  (rate limited, waiting {wait}s...)")
            time.sleep(wait)
            continue
        r.raise_for_status()
    raise RuntimeError(f"Gave up after retries on {url}")


def find_institution_id():
    """Look up DePaul's OpenAlex institution ID automatically."""
    print(f'Looking up "{INSTITUTION_SEARCH}" in OpenAlex...')
    data = get(f"{BASE}/institutions", {
        "search": INSTITUTION_SEARCH,
        "select": "id,display_name,ror,country_code,works_count",
    })
    results = data.get("results", [])
    if not results:
        sys.exit("Could not find the institution. Check the spelling above.")
    # Prefer the US result with the most works (the real DePaul, not a namesake).
    results.sort(key=lambda x: (x.get("country_code") == "US", x.get("works_count", 0)),
                 reverse=True)
    inst = results[0]
    short_id = inst["id"].rsplit("/", 1)[-1]    # e.g. "I123456789"
    print(f'  Found: {inst["display_name"]}  ({short_id}, {inst.get("works_count")} works)')
    return short_id


def fetch_authors(institution_id):
    """Page through every author whose last-known institution is DePaul."""
    print("Fetching researchers (this is the slow part)...")
    authors = []
    cursor = "*"
    while cursor:
        data = get(f"{BASE}/authors", {
            "filter": f"last_known_institutions.id:{institution_id}",
            "per-page": 200,
            "cursor": cursor,
            "select": "id,display_name,orcid,works_count,cited_by_count,"
                      "last_known_institutions,topics,summary_stats",
        })
        batch = data.get("results", [])
        authors.extend(batch)
        cursor = data.get("meta", {}).get("next_cursor")
        print(f"  {len(authors)} researchers so far...")
        time.sleep(0.2)
    print(f"Done: {len(authors)} researchers total.")
    return authors


def fetch_publications(author_id):
    """Grab recent publication titles + years for one author."""
    short = author_id.rsplit("/", 1)[-1]
    data = get(f"{BASE}/works", {
        "filter": f"author.id:{short}",
        "per-page": MAX_PUBS_PER_AUTHOR,
        "sort": "publication_year:desc",
        "select": "id,title,publication_year,doi,cited_by_count",
    })
    pubs = []
    for w in data.get("results", []):
        pubs.append({
            "title": w.get("title"),
            "year": w.get("publication_year"),
            "doi": w.get("doi"),
            "cited_by_count": w.get("cited_by_count"),
        })
    return pubs


def build_records(authors):
    """Flatten OpenAlex objects into clean records for our platform."""
    records = []
    for i, a in enumerate(authors, 1):
        topics = [t.get("display_name") for t in (a.get("topics") or [])][:10]
        last_inst = (a.get("last_known_institutions") or [{}])[0]
        rec = {
            "name": a.get("display_name"),
            "openalex_id": a.get("id"),
            "orcid": a.get("orcid"),
            "institution": last_inst.get("display_name"),
            "works_count": a.get("works_count"),
            "cited_by_count": a.get("cited_by_count"),
            "h_index": (a.get("summary_stats") or {}).get("h_index"),
            "research_topics": topics,          # <- the key field for matching
            "publications": [],
        }
        if FETCH_PUBLICATIONS:
            try:
                rec["publications"] = fetch_publications(a["id"])
            except Exception as e:
                print(f"  (couldn't get publications for {rec['name']}: {e})")
            if i % 25 == 0:
                print(f"  enriched {i}/{len(authors)} with publications...")
            time.sleep(0.15)
        records.append(rec)
    return records


def save(records):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "institution", "research_topics", "works_count",
                    "cited_by_count", "h_index", "orcid", "openalex_id"])
        for r in records:
            w.writerow([
                r["name"], r["institution"], "; ".join(r["research_topics"]),
                r["works_count"], r["cited_by_count"], r["h_index"],
                r["orcid"], r["openalex_id"],
            ])
    print(f"\nSaved {len(records)} researchers to:")
    print(f"  {OUTPUT_JSON}  (for building the agent)")
    print(f"  {OUTPUT_CSV}   (open in Excel to eyeball it)")


def main():
    if YOUR_EMAIL == "you@depaul.edu":
        print("WARNING: set YOUR_EMAIL near the top to your real email first.\n")
    institution_id = find_institution_id()
    authors = fetch_authors(institution_id)
    records = build_records(authors)
    save(records)


if __name__ == "__main__":
    main()
