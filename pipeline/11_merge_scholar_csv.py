#!/usr/bin/env python3
"""
8_merge_scholar_csv.py
----------------------
Merge a Google Scholar publication export into the `papers` table.

Additive only. Existing rows are never deleted or overwritten, because the
OpenAlex/S2 rows carry abstracts that Scholar does not provide, and roughly 170
faculty have papers from those sources that this export doesn't cover.

What Scholar buys us is depth: 6_fetch_papers.py caps each faculty member at 20
papers, while a Scholar profile routinely lists hundreds.

A Scholar export is NOT a faculty roster — it sweeps up students, postdocs, and
alumni, plus the occasional person whose surname merely happens to be "DePaul".
So a row is only imported when its profile maps to someone already in `faculty`:
surname equal, and first name equal / an initial / a nickname prefix. Anything
ambiguous is skipped rather than guessed.

USAGE:
    python3 pipeline/8_merge_scholar_csv.py <publications.csv> [--apply]

Without --apply it reports what it would do and writes the review file only.
"""

import csv, sys, os, re, sqlite3, unicodedata, collections

csv.field_size_limit(10 ** 7)

ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB     = os.path.join(ROOT, "faculty.db")
REVIEW = os.path.join(ROOT, "data", "scholar_unmatched_review.csv")

# A profile is worth a human look if it reads like an established researcher
# rather than a current student: a real citation record, a publication history
# that predates a typical PhD, and more than a handful of papers.
LIKELY_FACULTY = dict(min_citations=300, max_first_year=2017, min_papers=10)


def norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = re.sub(r"\b(dr|prof|professor|phd|jr|sr|ii|iii|m\.?d|mfa|mba)\b\.?", "", name.lower())
    name = re.sub(r"[^a-z\s]", " ", name)
    return " ".join(name.split())


def first_name_agrees(a: str, b: str) -> bool:
    """Equal, an initial of, or a nickname prefix of ('dan' vs 'daniel')."""
    return a == b or a.startswith(b) or b.startswith(a)


def load_faculty(con):
    by_last = collections.defaultdict(list)
    for fid, name in con.execute("SELECT id, name FROM faculty"):
        parts = norm(name).split()
        if len(parts) >= 2:
            by_last[parts[-1]].append((fid, name, parts[0]))
    return by_last


def match_profile(profile: str, by_last: dict):
    """The one faculty member this profile belongs to, or None if unsure."""
    parts = norm(profile).split()
    if len(parts) < 2:
        return None
    hits = [(fid, name) for fid, name, dbfirst in by_last.get(parts[-1], [])
            if first_name_agrees(parts[0], dbfirst)]
    return hits[0] if len(hits) == 1 else None


def as_int(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_changes = "--apply" in sys.argv
    if not args:
        sys.exit(__doc__)
    path = os.path.expanduser(args[0])

    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    by_profile = collections.defaultdict(list)
    for r in rows:
        by_profile[r["professor_name"]].append(r)
    print(f"Read {len(rows)} publications across {len(by_profile)} Scholar profiles.")

    con     = sqlite3.connect(DB)
    by_last = load_faculty(con)

    # Titles already on file, so re-running this is harmless and the three
    # people with two Scholar profiles don't get their work stored twice.
    seen = collections.defaultdict(set)
    for fid, title in con.execute("SELECT faculty_id, LOWER(TRIM(title)) FROM papers"):
        seen[fid].add(title)
    had_papers = {r[0] for r in con.execute("SELECT DISTINCT faculty_id FROM papers")}

    to_insert, matched_profiles, unmatched = [], {}, []
    for profile, pubs in by_profile.items():
        hit = match_profile(profile, by_last)
        if not hit:
            unmatched.append(profile)
            continue
        fid, fac_name = hit
        matched_profiles[profile] = fac_name
        for r in pubs:
            title = (r["title"] or "").strip()
            key   = title.lower()
            if not title or key in seen[fid]:
                continue
            seen[fid].add(key)
            to_insert.append((fid, title, as_int(r["year"]), as_int(r["citations"]) or 0))

    touched  = {row[0] for row in to_insert}
    print(f"\nMatched {len(matched_profiles)} profiles to faculty; {len(unmatched)} unmatched.")
    print(f"New papers to add : {len(to_insert)}")
    print(f"Faculty touched   : {len(touched)}  (first-ever papers for {len(touched - had_papers)})")

    # ── Review file: unmatched profiles that look like real faculty ──────────
    scored = []
    for profile in unmatched:
        pubs  = by_profile[profile]
        cites = sum(as_int(r["citations"]) or 0 for r in pubs)
        years = [y for y in (as_int(r["year"]) for r in pubs) if y and 1900 < y < 2030]
        first = min(years) if years else None
        likely = (cites >= LIKELY_FACULTY["min_citations"]
                  and first is not None and first <= LIKELY_FACULTY["max_first_year"]
                  and len(pubs) >= LIKELY_FACULTY["min_papers"])
        venues = collections.Counter(r["venue"][:60] for r in pubs if r["venue"].strip())
        scored.append({
            "profile_name": profile,
            "likely_faculty": "yes" if likely else "no",
            "total_citations": cites,
            "papers": len(pubs),
            "first_year": first or "",
            "top_venue": venues.most_common(1)[0][0] if venues else "",
            "scholar_profile": pubs[0]["source_profile_url"],
        })
    scored.sort(key=lambda d: (d["likely_faculty"] == "no", -d["total_citations"]))

    os.makedirs(os.path.dirname(REVIEW), exist_ok=True)
    with open(REVIEW, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(scored[0].keys()))
        w.writeheader()
        w.writerows(scored)
    likely_n = sum(1 for d in scored if d["likely_faculty"] == "yes")
    print(f"\nWrote {REVIEW}")
    print(f"  {likely_n} unmatched profiles look like established researchers "
          f"(>= {LIKELY_FACULTY['min_citations']} citations, publishing since "
          f"{LIKELY_FACULTY['max_first_year']} or earlier, >= {LIKELY_FACULTY['min_papers']} papers)")
    print(f"  {len(scored) - likely_n} look like students, postdocs, or unrelated people")

    if not apply_changes:
        print("\nDry run — nothing written to the database. Re-run with --apply.")
        con.close()
        return

    con.executemany(
        "INSERT INTO papers (faculty_id, title, abstract, year, cited_by_count) VALUES (?, ?, NULL, ?, ?)",
        to_insert
    )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    withp = con.execute("SELECT COUNT(DISTINCT faculty_id) FROM papers").fetchone()[0]
    con.close()
    print(f"\nApplied. papers={total}, faculty with papers={withp}")
    print("Delete paper_index.pkl so the embeddings rebuild on next start.")


if __name__ == "__main__":
    main()
