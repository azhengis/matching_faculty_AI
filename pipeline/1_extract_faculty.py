#!/usr/bin/env python3
"""
1_extract_faculty.py
--------------------
Scrape DePaul's faculty directory into data/depaul_roster_clean.json — the
roster every later stage reads.

WHY THIS SCRIPT EXISTS. It did not, until now. The README described step 1 as
"scrape the DePaul faculty directory", but the file at this path queried
OpenAlex and wrote depaul_faculty.json, which nothing downstream ever read. The
real roster was a frozen artifact with no generator in version control, so the
faculty list could not be refreshed and every new hire stayed invisible. That
is how a full-time Assistant Professor with a live bio page returned nothing.

HOW IT ENUMERATES PEOPLE. From https://www.depaul.edu/sitemap.xml, which DePaul
publishes and robots.txt allows. Every bio page appears there as
/faculty/<first-last>, so the sitemap is a complete list maintained by the
university rather than a guess at how their listing paginates.

TEMPLATE NOTE. Bio pages carry their structured fields as <meta> tags. The
current template names them plainly (<meta name="College">); the previous one
prefixed them (<meta name="meta-College">). Both are read, because a mixed
crawl during a template rollout should not silently produce empty records.

Resumable: pages are cached under bio_cache/, so a Ctrl-C costs nothing and a
re-run only fetches what is missing.

USAGE:
    python3 pipeline/1_extract_faculty.py                 # full crawl
    python3 pipeline/1_extract_faculty.py --limit 25      # quick smoke test
    python3 pipeline/1_extract_faculty.py --out /tmp/r.json
"""
import argparse
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from text_clean import strip_site_chrome   # noqa: E402

SITEMAP    = "https://www.depaul.edu/sitemap.xml"
BIO_PREFIX = "https://www.depaul.edu/faculty/"
CACHE_DIR  = os.path.join(ROOT, "bio_cache")
OUT_JSON   = os.path.join(ROOT, "data", "depaul_roster_clean.json")

PAUSE_SECS = 0.4          # polite crawl of ~2,300 pages
TIMEOUT    = 45

session = requests.Session()
session.headers["User-Agent"] = (
    "DePaulFacultyMatcher/1.0 (DePaul AI Institute research tool; "
    "contact: ai-institute@depaul.edu)"
)

# The tags a bio page publishes, mapped to the roster field they fill.
META_FIELDS = {
    "title":             "PersonnelTitle",
    "college":           "College",
    "department":        "Department",
    "personnel_type":    "Personnel",
    "employment_status": "EmploymentStatus",
    "email":             "Email",
}


def meta(soup, name):
    """Read a meta tag, accepting both template generations."""
    for attr in (name, f"meta-{name}"):
        tag = soup.find("meta", attrs={"name": attr})
        if tag and (tag.get("content") or "").strip():
            return tag["content"].strip()
    return ""


def cache_path(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1] or "index"
    return os.path.join(CACHE_DIR, slug + ".html")


def fetch(url, use_cache=True):
    cp = cache_path(url)
    if use_cache and os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return f.read()
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        f.write(r.text)
    time.sleep(PAUSE_SECS)
    return r.text


def bio_urls():
    """Every faculty bio page DePaul lists, deduped and sorted."""
    print(f"Reading {SITEMAP} ...")
    xml = session.get(SITEMAP, timeout=TIMEOUT)
    xml.raise_for_status()
    locs = re.findall(r"<loc>([^<]+)</loc>", xml.text)
    urls = sorted({u.strip() for u in locs if u.strip().startswith(BIO_PREFIX)})
    print(f"  {len(locs)} URLs in sitemap, {len(urls)} faculty bio pages")
    return urls


def person_from_page(url, html):
    """Build one roster record. Returns None when the page is not a bio."""
    soup = BeautifulSoup(html, "html.parser")

    first, last = meta(soup, "FirstName"), meta(soup, "LastName")
    name = " ".join(x for x in (first, last) if x).strip()
    if not name:
        # Fall back to the page title, which reads "Name | DePaul University".
        title_tag = soup.find("title")
        name = (title_tag.get_text(strip=True).split("|")[0].strip()
                if title_tag else "")
    if not name:
        return None

    record = {"name": name}
    for field, tag in META_FIELDS.items():
        record[field] = meta(soup, tag)

    # Department arrives semicolon-separated when someone is jointly appointed
    # ("Neuroscience;Psychology"). Later stages split colleges on commas, so
    # match that convention rather than inventing a second one.
    record["department"] = ", ".join(
        part.strip() for part in record["department"].split(";") if part.strip())

    record["bio_url"] = url
    # Filled by later stages; present so the shape matches the existing roster.
    record["research_topics"] = []
    record["publications"] = []
    record["research_summary"] = ""
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N pages (smoke test)")
    ap.add_argument("--no-cache", action="store_true",
                    help="re-fetch pages already in bio_cache/")
    args = ap.parse_args()

    urls = bio_urls()
    if args.limit:
        urls = urls[:args.limit]

    people, failed, skipped = [], [], 0
    for i, url in enumerate(urls, 1):
        try:
            html = fetch(url, use_cache=not args.no_cache)
        except Exception as e:
            failed.append((url, str(e)))
            print(f"[{i:4d}/{len(urls)}] FAIL {url}: {e}")
            continue

        record = person_from_page(url, html)
        if not record:
            skipped += 1
            continue
        people.append(record)
        if i % 100 == 0 or i == len(urls):
            print(f"[{i:4d}/{len(urls)}] {record['name']}")

    # A crawl that silently produces empty fields is the failure this script was
    # written to prevent, so say plainly how much of each field came back.
    print(f"\nScraped {len(people)} people ({skipped} pages had no name, "
          f"{len(failed)} fetch failures)")
    for field in ("email", "title", "college", "department", "employment_status"):
        got = sum(1 for p in people if (p.get(field) or "").strip())
        pct = (100 * got / len(people)) if people else 0
        flag = "  <-- CHECK THE TEMPLATE" if people and pct < 50 else ""
        print(f"  {field:20} {got:5}/{len(people)}  ({pct:.0f}%){flag}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(people, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {args.out}")
    print("Next: python3 pipeline/2_enrich_bios.py")

    if failed:
        print(f"\n{len(failed)} pages failed; re-run to retry (cached pages are skipped):")
        for url, err in failed[:10]:
            print(f"  {url}  {err}")


if __name__ == "__main__":
    main()
