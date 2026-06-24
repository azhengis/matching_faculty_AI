#!/usr/bin/env python3
"""
enrich_bios2.py  (improved parser)
----------------------------------
Same as before, but now it catches ALL the heading names DePaul actually uses
for research and publications -- not just "Research Interests". Re-reads the pages
already saved in ./bio_cache, so it runs in seconds and downloads nothing new.

Run:  python3 enrich_bios2.py
Output (overwrites the thin versions):
    depaul_faculty_enriched.json
    depaul_faculty_enriched.csv
"""
import json, csv, os, re, time, sys
import requests
from bs4 import BeautifulSoup

ROSTER_IN  = "depaul_roster_clean.json"
JSON_OUT   = "depaul_faculty_enriched.json"
CSV_OUT    = "depaul_faculty_enriched.csv"
CACHE_DIR  = "bio_cache"
PAUSE_SECS = 1.0
USER_AGENT = "DePaul-Faculty-Matching-Project (academic; contact: you@depaul.edu)"

USE_OLLAMA   = False
OLLAMA_MODEL = "llama3.1"
OLLAMA_URL   = "http://localhost:11434/api/generate"

# All the research-type headings we discovered, in priority order.
RESEARCH_HEADINGS = ["Research Interests", "Specific Research Area", "Research Area",
                     "Major Areas of Interest", "Research Focus",
                     "Areas of Expertise", "Areas of Interest", "Interests"]
PUB_HEADINGS    = ["Selected Publications", "Select Publications", "Publications"]
BIO_HEADINGS    = ["Biography", "About"]
COURSE_HEADINGS = ["Classes Taught", "Courses Taught", "Courses Recently Taught",
                   "Courses Frequently Taught"]
# Headings we don't output but use as boundaries so a section stops at the next one.
BOUNDARY_ONLY   = ["Academic Degrees", "Education", "Professional Associations",
                   "Professional Affiliations", "Professional Society Memberships",
                   "Professional Certifications", "Professional Activities",
                   "Awards and Honors", "Books", "Media", "Courses & Syllabi",
                   # footer markers -- stop here so we don't grab page furniture:
                   "Information for", "Academic Resources", "Campus Resources",
                   "University Resources"]

ALL_HEADINGS = (RESEARCH_HEADINGS + PUB_HEADINGS + BIO_HEADINGS +
                COURSE_HEADINGS + BOUNDARY_ONLY)

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(url):
    slug = url.rstrip("/").rsplit("/", 1)[-1] or "index"
    return os.path.join(CACHE_DIR, slug + ".html")


def fetch(url):
    cp = cache_path(url)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return f.read()
    r = session.get(url, timeout=45)
    r.raise_for_status()
    with open(cp, "w", encoding="utf-8") as f:
        f.write(r.text)
    time.sleep(PAUSE_SECS)
    return r.text


def meta(soup, name):
    tag = soup.find("meta", attrs={"name": f"meta-{name}"})
    return (tag.get("content") or "").strip() if tag else ""


def parse_sections(text):
    """Split page text into {heading: body}, handling overlapping heading names."""
    low = text.lower()
    hits = []
    for h in ALL_HEADINGS:
        i = low.find(h.lower())
        if i != -1:
            hits.append((i, i + len(h), h))
    # Drop any hit fully contained inside another (e.g. "Research Area" inside
    # "Specific Research Area", or "Publications" inside "Selected Publications").
    hits.sort(key=lambda x: (x[1] - x[0]), reverse=True)   # longest first
    kept = []
    for s, e, h in hits:
        if any(ks <= s and e <= ke for ks, ke, _ in kept):
            continue
        kept.append((s, e, h))
    kept.sort()                                            # back to page order
    out = {}
    for idx, (s, e, h) in enumerate(kept):
        end = kept[idx + 1][0] if idx + 1 < len(kept) else len(text)
        body = text[e:end].strip(" :\n\t")
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if body:
            out[h] = body
    return out


def first_of(sections, headings):
    for h in headings:
        if sections.get(h):
            return sections[h]
    return ""


def ollama_topics(text):
    prompt = ("Extract 5-8 short research topic tags (1-3 words each) from this "
              "faculty research description. Reply ONLY with a comma-separated list.\n\n"
              + text[:2000])
    try:
        r = session.post(OLLAMA_URL, json={"model": OLLAMA_MODEL,
                                           "prompt": prompt, "stream": False}, timeout=120)
        raw = r.json().get("response", "")
        return [t.strip() for t in raw.split(",") if t.strip()][:8]
    except Exception as e:
        print(f"    (ollama skipped: {e})")
        return []


def main():
    if not os.path.exists(ROSTER_IN):
        sys.exit(f"Put {ROSTER_IN} next to this script first.")
    with open(ROSTER_IN, encoding="utf-8") as f:
        people = json.load(f)

    todo = [p for p in people if p.get("bio_url")]
    print(f"{len(people)} people, {len(todo)} bio pages (reading from cache).\n")

    found = 0
    for p in people:
        url = p.get("bio_url")
        if not url:
            continue
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  skip {p['name']}: {e}")
            continue

        soup = BeautifulSoup(html, "html.parser")
        sections = parse_sections(soup.get_text("\n"))

        p["college"]    = meta(soup, "College") or p.get("college", "")
        p["department"] = meta(soup, "Department") or p.get("department", "")
        p["title"]      = meta(soup, "PersonnelTitle") or p.get("title", "")

        # Combine every research-type section that exists on the page.
        research_bits = [sections[h] for h in RESEARCH_HEADINGS if sections.get(h)]
        research = "\n\n".join(research_bits)
        if not research:                       # fall back to biography prose
            research = first_of(sections, BIO_HEADINGS)

        p["research_summary"]  = research
        p["publications_text"] = first_of(sections, PUB_HEADINGS)
        p["classes_taught"]    = first_of(sections, COURSE_HEADINGS)

        if research:
            found += 1
        if USE_OLLAMA and research:
            p["research_topics"] = ollama_topics(research)

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(people, f, indent=2, ensure_ascii=False)

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "department", "college", "email",
                    "research_summary", "research_topics", "bio_url"])
        for p in people:
            w.writerow([p["name"], p.get("department",""), p.get("college",""),
                        p.get("email",""), p.get("research_summary","")[:600],
                        "; ".join(p.get("research_topics", [])), p.get("bio_url","")])

    print(f"Done. {found} people now have a research summary "
          f"(was ~140 before).")
    print(f"Saved {JSON_OUT} and {CSV_OUT}.")


if __name__ == "__main__":
    main()
