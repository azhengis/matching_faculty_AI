#!/usr/bin/env python3
"""
14_add_faculty.py
-----------------
Add ONE faculty member to faculty.db from their DePaul bio page.

For the person who is real, has a live directory page, and is simply not in
our snapshot — a hire made after the roster was scraped, or a page the crawl
missed. Rebuilding the whole roster to recover one person costs an hour of
crawling plus a full re-enrichment; this is the ten-second version.

Does the work of stages 1, 2, 4, and 6 for a single record: scrapes the bio
page, pulls the research sections out of it, inserts the faculty row, and
fetches publications from OpenAlex.

Accepts a bio URL or an email address. An email is resolved against the
sitemap, so you can paste exactly what the person gave you.

USAGE:
    python3 pipeline/14_add_faculty.py mbachma3@depaul.edu
    python3 pipeline/14_add_faculty.py https://www.depaul.edu/faculty/matthew-bachman --apply
    python3 pipeline/14_add_faculty.py <target> --apply --no-papers

Dry run by default; --apply writes. AFTER APPLYING, the search index must be
rebuilt or the new person is in the database but unfindable:

    rm -f faculty_index.pkl paper_index.pkl && python3 search.py

And for the deployed site, regenerate the seed the image is built from:

    python3 pipeline/make_seed_db.py --gzip-to data/seed_faculty.db.gz
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from text_clean import strip_site_chrome, is_junk_summary, page_text   # noqa: E402

DB       = os.path.join(ROOT, "faculty.db")
ENRICHED = os.path.join(ROOT, "data", "depaul_faculty_enriched.json")
SITEMAP = "https://www.depaul.edu/sitemap.xml"
BIO_PREFIX = "https://www.depaul.edu/faculty/"


def _load_stage(filename):
    """Import a numbered pipeline stage.

    They start with a digit, so `import 2_enrich_bios` is a syntax error. This
    is worth the awkwardness: the heading list and section parser in stage 2
    are the product of a lot of tuning against real pages, and a second copy
    here would drift from it silently.
    """
    path = os.path.join(HERE, filename)
    spec = importlib.util.spec_from_file_location(filename[:-3].lstrip("0123456789_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_target(target):
    """Turn a bio URL or an email address into a bio URL."""
    target = target.strip()
    if target.startswith("http"):
        return target
    if "@" not in target:
        sys.exit(f"Give a bio URL or an email address, not {target!r}")

    print(f"Looking up {target} in the sitemap...")
    xml = requests.get(SITEMAP, timeout=45)
    xml.raise_for_status()
    urls = sorted({u for u in re.findall(r"<loc>([^<]+)</loc>", xml.text)
                   if u.startswith(BIO_PREFIX)})

    # The local part usually encodes the name (mbachma3 -> Bachman), so try the
    # most likely pages first rather than crawling 2,300 of them.
    local = target.split("@")[0].lower()
    stem = re.sub(r"\d+$", "", local)
    likely = [u for u in urls if stem and stem[1:] in u.rsplit("/", 1)[-1].replace("-", "")]
    ordered = likely + [u for u in urls if u not in likely]
    print(f"  {len(urls)} bio pages; checking {len(likely)} likely match(es) first")

    for i, url in enumerate(ordered, 1):
        try:
            html = requests.get(url, timeout=45).text
        except Exception:
            continue
        if re.search(re.escape(target), html, re.IGNORECASE):
            print(f"  found: {url}")
            return url
        if i == len(likely) and likely:
            print("  not among the likely pages; scanning the rest (this is slow)")
        if i > 400:
            sys.exit("Gave up after 400 pages. Pass the bio URL directly.")
    sys.exit(f"No bio page on depaul.edu carries {target}.")


def scrape(url, stage2):
    html = requests.get(url, timeout=45).text
    soup = BeautifulSoup(html, "html.parser")
    sections = stage2.parse_sections(strip_site_chrome(page_text(soup)))

    def m(name):
        return stage2.meta(soup, name)

    name = " ".join(x for x in (m("FirstName"), m("LastName")) if x).strip()
    if not name:
        t = soup.find("title")
        name = t.get_text(strip=True).split("|")[0].strip() if t else ""
    if not name:
        sys.exit(f"{url} does not look like a bio page (no name).")

    research_bits = [sections[h] for h in stage2.RESEARCH_HEADINGS if sections.get(h)]
    research = "\n\n".join(research_bits)
    if not research:
        research = stage2.first_of(sections, stage2.BIO_HEADINGS)

    return {
        "name": name,
        "title": m("PersonnelTitle"),
        "department": ", ".join(p.strip() for p in m("Department").split(";") if p.strip()),
        "college": m("College"),
        "employment_status": m("EmploymentStatus"),
        "personnel_type": m("Personnel"),
        "email": m("Email"),
        "bio_url": url,
        "research_summary": "" if is_junk_summary(research) else strip_site_chrome(research),
        "publications_text": stage2.first_of(sections, stage2.PUB_HEADINGS),
        "classes_taught": stage2.first_of(sections, stage2.COURSE_HEADINGS),
    }


def insert(con, record):
    """Insert, or update in place when the person is already present."""
    fields = list(record.keys())
    existing = None
    if record["email"]:
        existing = con.execute("SELECT id FROM faculty WHERE LOWER(email) = ?",
                               (record["email"].lower(),)).fetchone()
    if not existing:
        existing = con.execute("SELECT id FROM faculty WHERE name = ?", (record["name"],)).fetchone()

    if existing:
        con.execute(f"UPDATE faculty SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                    [record[f] for f in fields] + [existing[0]])
        return existing[0], False
    cur = con.execute(
        f"INSERT INTO faculty ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        [record[f] for f in fields])
    return cur.lastrowid, True


def update_enriched(record):
    """Add the person to data/depaul_faculty_enriched.json as well.

    Two reasons this is not optional. That file is stage 4's input, so a
    from-scratch rebuild would otherwise drop anyone added only to the
    database. And check_seed_pii treats it as the record of what personal
    information this repository already publishes, so a seed containing an
    email the file does not list fails the gate — correctly, because the gate
    cannot tell a legitimate new hire from a leak.
    """
    if not os.path.exists(ENRICHED):
        print(f"  (no {ENRICHED}; skipping the public record)")
        return False
    with open(ENRICHED, encoding="utf-8") as f:
        people = json.load(f)

    entry = dict(record)
    entry.setdefault("research_topics", [])
    entry.setdefault("publications", [])

    email = (record.get("email") or "").lower()
    for i, existing in enumerate(people):
        same_email = email and (existing.get("email") or "").lower() == email
        if same_email or existing.get("name") == record["name"]:
            people[i] = {**existing, **entry}
            action = "updated in"
            break
    else:
        people.append(entry)
        action = "added to"

    with open(ENRICHED, "w", encoding="utf-8") as f:
        json.dump(people, f, indent=2, ensure_ascii=False)
    print(f"  {action} data/depaul_faculty_enriched.json ({len(people)} records)")
    return True


def suggest_authors(name):
    """List OpenAlex candidates so a human can pick one.

    find_author_id requires the author to show a DePaul affiliation, which is
    right for a bulk run over 1,389 people but wrong for a new hire: their
    published work still carries the institution they came from. Rather than
    relax the rule, show the candidates and let the operator confirm.
    """
    try:
        data = stage6_api(f"{'https://api.openalex.org'}/authors", {
            "search": name, "per-page": 8,
            "select": "id,display_name,works_count,last_known_institutions",
        })
    except Exception as e:
        print(f"  (could not list candidates: {e})")
        return
    results = (data or {}).get("results", [])
    if not results:
        print("  OpenAlex has no author by that name.")
        return
    print("\n  OpenAlex candidates — if one is them, re-run with --openalex-id:")
    for r in results:
        insts = ", ".join(i.get("display_name", "") for i in (r.get("last_known_institutions") or []))
        print(f"    {r['id'].rsplit('/', 1)[-1]:14} {r.get('display_name', ''):26} "
              f"works={r.get('works_count', 0):<5} {insts[:48]}")


stage6_api = None   # bound to the stage-6 api_get once that module is loaded


def fetch_papers(con, faculty_id, record, stage6, pinned_id=None):
    con.execute(stage6.PAPERS_TABLE_SQL)
    if pinned_id:
        author_id = (pinned_id if pinned_id.startswith("http")
                     else f"https://openalex.org/{pinned_id}")
        print(f"  OpenAlex: using the author you pinned ({author_id.rsplit('/', 1)[-1]})")
    else:
        author_id = stage6.find_author_id(record["name"], record.get("research_summary", ""))
    if not author_id:
        print("  OpenAlex: no author with a DePaul affiliation matched this name.")
        print("  That is expected for a recent hire, whose published work still")
        print("  carries their previous institution.")
        suggest_authors(record["name"])
        return 0
    papers = stage6.fetch_papers_for_author(author_id)
    added = 0
    for p in papers:
        con.execute(
            "INSERT INTO papers (faculty_id, title, abstract, year, cited_by_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (faculty_id, p.get("title"), p.get("abstract"), p.get("year"), p.get("cited_by_count", 0)))
        added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="bio URL or @depaul.edu email address")
    ap.add_argument("--apply", action="store_true", help="write to faculty.db")
    ap.add_argument("--no-papers", action="store_true", help="skip the OpenAlex lookup")
    ap.add_argument("--openalex-id", default=None,
                    help="pin the OpenAlex author (e.g. A5012345678) when the "
                         "automatic match cannot confirm one")
    args = ap.parse_args()

    stage2 = _load_stage("2_enrich_bios.py")
    url = resolve_target(args.target)
    record = scrape(url, stage2)

    print("\nScraped:")
    for k, v in record.items():
        shown = (v[:110] + "...") if isinstance(v, str) and len(v) > 110 else v
        print(f"  {k:18} {shown!r}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write this to faculty.db.")
        return

    con = sqlite3.connect(DB)
    faculty_id, created = insert(con, record)
    con.commit()
    print(f"\n{'Inserted' if created else 'Updated'} faculty id {faculty_id}")
    update_enriched(record)

    if not args.no_papers:
        stage6 = _load_stage("6_fetch_papers.py")
        globals()["stage6_api"] = stage6.api_get
        n = fetch_papers(con, faculty_id, record, stage6, args.openalex_id)
        con.commit()
        print(f"  publications added: {n}")

    con.close()
    print("\nNow rebuild the index, or they are in the database but unfindable:")
    print("  rm -f faculty_index.pkl paper_index.pkl && python3 search.py")
    print("\nAnd for the deployed site, regenerate the seed:")
    print("  python3 pipeline/make_seed_db.py --gzip-to data/seed_faculty.db.gz")


if __name__ == "__main__":
    main()
