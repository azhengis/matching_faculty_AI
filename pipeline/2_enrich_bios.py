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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from text_clean import strip_site_chrome   # noqa: E402

# Anchored on the repo root like every other stage. These were bare relative
# names, so the script only worked from whatever directory happened to hold the
# data — and since the files live in data/, that was no directory at all: it
# raised FileNotFoundError whether you ran it from the root or from pipeline/,
# exactly as the README tells you to.
_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROSTER_IN  = os.path.join(_ROOT, "data", "depaul_roster_clean.json")
JSON_OUT   = os.path.join(_ROOT, "data", "depaul_faculty_enriched.json")
CSV_OUT    = os.path.join(_ROOT, "data", "depaul_faculty_enriched.csv")
CACHE_DIR  = os.path.join(_ROOT, "bio_cache")
PAUSE_SECS = 1.0
USER_AGENT = "DePaul-Faculty-Matching-Project (academic; contact: you@depaul.edu)"

USE_OLLAMA   = False
OLLAMA_MODEL = "llama3.1"
OLLAMA_URL   = "http://localhost:11434/api/generate"

# All the research-type headings we discovered, in priority order.
RESEARCH_HEADINGS = ["Research Interests", "Specific Research Area", "Research Area",
                     "Major Areas of Interest", "Research Focus",
                     "Areas of Expertise", "Areas of Interest", "Interests"]
PUB_HEADINGS    = ["Selected Publications:", "Selected Publications", "Select Publications", "Publications"]
# "BIO" is the heading DePaul actually uses on the faculty template. Omitting it
# left every profile whose page has no explicit research section — most of the
# arts, music, and law faculty — with an empty summary and invisible to search.
BIO_HEADINGS    = ["Biography", "BIO", "About"]
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
    """Read a bio page's metadata, tolerating both template generations.

    DePaul rebuilt the bio template after the June scrape. The tags carry the
    same names and the same values, but the "meta-" prefix is gone:
    <meta name="meta-College"> became <meta name="College">. Looking only for
    the prefixed form returns "" for every field on a current page, so a
    re-scrape today would produce records with no college, department, or
    title and nothing would report an error.
    """
    for attr in (f"meta-{name}", name):
        tag = soup.find("meta", attrs={"name": attr})
        if tag and (tag.get("content") or "").strip():
            return tag["content"].strip()
    return ""


def parse_sections(text):
    """Split page text into {heading: body}, handling overlapping heading names."""
    low = text.lower()
    hits = []
    for h in ALL_HEADINGS:
        # A heading OWNS ITS LINE. The page text comes from get_text("\n"), so a
        # real heading always starts one; a phrase that merely contains the same
        # words does not.
        #
        # Without the line anchor, "Information for" — a footer marker — fired
        # inside "increase access to digital information for people with
        # disabilities", cutting that biography mid-sentence and filing its tail
        # under a footer heading. 282 of 400 sampled pages had at least one
        # heading matching mid-line. The word-boundary guard is still needed on
        # top: "BIO" would otherwise fire inside a line beginning "Biomedical".
        m = re.search(r"^[ \t\xa0]*" + re.escape(h.lower()) + r"(?!\w)",
                      low, re.MULTILINE)
        if m:
            hits.append((m.start(), m.end(), h))
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


def _identity(person):
    """Match a person across files: email when there is one, else folded name."""
    email = (person.get("email") or "").strip().lower()
    return ("email", email) if email else ("name", " ".join((person.get("name") or "").lower().split()))


def load_people():
    """The roster, merged over whatever enrichment previous runs produced.

    This used to read the enriched file INSTEAD of the roster whenever it
    existed, to avoid clobbering research summaries that later stages filled
    in. That protected the summaries and quietly defeated the entire point of
    re-running stage 1: a fresh roster with new hires in it was never read, so
    nobody new could ever enter the pipeline.

    Merging keeps both. Enrichment is carried over per person, the directory
    fields are refreshed from the new roster, and anyone the roster has and the
    enriched file does not is added. People in the enriched file but no longer
    in the roster are KEPT — a page can vanish because someone left, but also
    because the crawl hiccuped, and this file is not the place to delete
    somebody from.
    """
    if not os.path.exists(ROSTER_IN):
        sys.exit(f"No roster at {ROSTER_IN}. Run 1_extract_faculty.py first.")
    with open(ROSTER_IN, encoding="utf-8") as f:
        roster = json.load(f)

    enriched = []
    if os.path.exists(JSON_OUT):
        with open(JSON_OUT, encoding="utf-8") as f:
            enriched = json.load(f)
    by_id = {_identity(p): p for p in enriched}

    people, added = [], 0
    seen = set()
    for fresh in roster:
        key = _identity(fresh)
        seen.add(key)
        prior = by_id.get(key)
        if prior is None:
            people.append(dict(fresh))
            added += 1
        else:
            # Directory fields come from the roster; everything a later stage
            # produced stays unless the roster has something non-empty.
            merged = dict(prior)
            for field, value in fresh.items():
                if value not in (None, "", [], {}):
                    merged[field] = value
            people.append(merged)

    departed = [p for p in enriched if _identity(p) not in seen]
    people.extend(departed)

    print(f"Roster {len(roster)}, previously enriched {len(enriched)}: "
          f"{added} new, {len(departed)} no longer listed (kept).")
    return people


def main():
    people = load_people()
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
        # soup.get_text() takes the whole page, footer included, and the section
        # parser has no way to know the address block below the last heading is
        # not part of it. Cutting the furniture here keeps it out of every
        # section at once, and out of the database in the first place.
        sections = parse_sections(strip_site_chrome(soup.get_text("\n")))

        p["college"]    = meta(soup, "College") or p.get("college", "")
        # Joint appointments arrive semicolon-separated ("Neuroscience;Psychology").
        # Stage 1 normalises them to commas, matching how colleges are listed;
        # re-reading the raw tag here used to undo that, and the semicolons then
        # showed on the profile page exactly as the page emitted them.
        dept = meta(soup, "Department")
        if dept:
            p["department"] = ", ".join(x.strip() for x in dept.split(";") if x.strip())
        p["title"]      = meta(soup, "PersonnelTitle") or p.get("title", "")

        # A page carries two different things and they belong in two fields.
        #
        # "Research Area" sections hold SHORT TOPIC LABELS ("Human Computer
        # Interaction"). The BIO section holds PROSE. They used to compete for
        # one field, with the labels winning and the prose only used when no
        # research section existed at all. Oliver Alonzo's page has both, so his
        # three-paragraph biography was discarded in favour of two words, and
        # the labels then sat in the bio slot where they read as a broken bio.
        #
        # Now the labels become research_topics, which feed the interests chips
        # and the matching text, and the prose becomes the summary.
        topics = []
        for heading in RESEARCH_HEADINGS:
            body = sections.get(heading)
            if not body:
                continue
            for line in body.split("\n"):
                line = line.strip()
                if line and line not in topics:
                    topics.append(line)

        bio_prose = first_of(sections, BIO_HEADINGS)
        # The summary is the prose when there is any. Where a page offers only
        # labels, they still have to serve as the summary or the person becomes
        # unsearchable.
        research = bio_prose or "\n\n".join(t for t in topics)

        if topics:
            p["research_topics"] = topics

        # Fall back to whatever was already there if this scrape found
        # nothing new -- an empty page section shouldn't erase good data.
        p["research_summary"]  = research or p.get("research_summary", "")
        p["publications_text"] = first_of(sections, PUB_HEADINGS) or p.get("publications_text", "")
        p["classes_taught"]    = first_of(sections, COURSE_HEADINGS) or p.get("classes_taught", "")

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
