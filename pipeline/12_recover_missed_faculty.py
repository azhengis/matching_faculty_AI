#!/usr/bin/env python3
"""
12_recover_missed_faculty.py
----------------------------
Promote already-staged Google Scholar publications into the `papers` table for
faculty whose Scholar profile the exact-name merge in 11_merge_scholar_csv.py
MISSED — real faculty listed under a fuller name ("Leonard A. Jason" in Scholar
vs "Leonard Jason" in the roster), whose deep publication record was therefore
absent from search.

Reads the `scholar_papers` staging table directly, so no CSV is needed — the
Scholar export was already staged there by script 11.

WHY A STRICTER RULE THAN SCRIPT 11:
Script 11 matches on surname + first-name agreement, where "agreement" allows a
bare initial ("j" ↔ "james"). That is unsafe for a bulk auto-merge: an initial
matches many first names, so "James H Murphy" wrongly maps to "J. Patrick
Murphy". Here every merge is unattended, so we require the FULL first name to be
identical (middle names/initials ignored) and the surname to match, and the
profile to map to EXACTLY ONE faculty member. Anything else is skipped.

STUDENTS ARE EXCLUDED FOR FREE: a student, postdoc, or alumnus is not in the
faculty roster, so their profile matches no faculty record and contributes
nothing. The "faculty only" requirement is a consequence of requiring a match,
not a separate heuristic.

USAGE:
    python3 pipeline/12_recover_missed_faculty.py           # dry run — report only
    python3 pipeline/12_recover_missed_faculty.py --apply   # write to papers

After --apply, delete paper_index.pkl so the embeddings rebuild on next start.
"""

import sys, os, re, sqlite3, unicodedata, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB   = os.path.join(ROOT, "faculty.db")


def norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = re.sub(r"\b(dr|prof|professor|phd|jr|sr|ii|iii|m\.?d|mfa|mba)\b\.?", "", name.lower())
    name = re.sub(r"[^a-z\s]", " ", name)
    return " ".join(name.split())


def faculty_by_surname(con):
    """Map surname -> [(faculty_id, display_name, first_name)]."""
    by_last = collections.defaultdict(list)
    for fid, name in con.execute("SELECT id, name FROM faculty"):
        parts = norm(name).split()
        if len(parts) >= 2:
            by_last[parts[-1]].append((fid, name, parts[0]))
    return by_last


def strict_match(profile: str, by_last: dict):
    """The one faculty member this Scholar profile safely belongs to, or None.

    Requires: same surname, IDENTICAL full first name (both longer than an
    initial), and exactly one such faculty member. Middle names are ignored.
    """
    parts = norm(profile).split()
    if len(parts) < 2:
        return None
    first, last = parts[0], parts[-1]
    if len(first) < 2:
        return None  # profile itself is initial-only — too little to match on
    hits = [(fid, name) for fid, name, dbfirst in by_last.get(last, [])
            if len(dbfirst) > 1 and dbfirst == first]
    return hits[0] if len(hits) == 1 else None


def main():
    apply_changes = "--apply" in sys.argv
    con = sqlite3.connect(DB)

    # Profiles already claimed by the exact-name merge — skip them, their papers
    # are in `papers` already.
    exact_norm = {norm(name) for (name,) in con.execute("SELECT name FROM faculty")}

    by_last = faculty_by_surname(con)

    # Titles already on file per faculty, so re-running is harmless and we don't
    # duplicate a paper a faculty member already has from OpenAlex/S2.
    seen = collections.defaultdict(set)
    for fid, title in con.execute("SELECT faculty_id, LOWER(TRIM(title)) FROM papers"):
        seen[fid].add(title)
    had_papers = {r[0] for r in con.execute("SELECT DISTINCT faculty_id FROM papers")}

    # Group staged Scholar rows by profile.
    by_profile = collections.defaultdict(list)
    for sid, pname, title, year, cites in con.execute(
        "SELECT scholar_id, professor_name, title, year, citations FROM scholar_papers"
    ):
        by_profile[pname].append((title, year, cites))

    to_insert, recovered = [], {}
    for profile, pubs in by_profile.items():
        if norm(profile) in exact_norm:
            continue  # already merged by exact name
        hit = strict_match(profile, by_last)
        if not hit:
            continue  # ambiguous, or not a faculty member (student/alumnus)
        fid, fac_name = hit
        recovered[profile] = fac_name
        for title, year, cites in pubs:
            title = (title or "").strip()
            key = title.lower()
            if not title or key in seen[fid]:
                continue
            seen[fid].add(key)
            to_insert.append((fid, title, year, cites or 0))

    touched = {row[0] for row in to_insert}
    print(f"Faculty recovered      : {len(recovered)} Scholar profiles the exact merge missed")
    print(f"New papers to add      : {len(to_insert)}")
    print(f"Faculty touched        : {len(touched)}  (first-ever papers for {len(touched - had_papers)})")

    per_fac = collections.Counter(row[0] for row in to_insert)
    fid_to_name = {fid: name for fid, name in con.execute("SELECT id, name FROM faculty")}
    print("\nTop recoveries (faculty, #papers added):")
    for fid, cnt in per_fac.most_common(10):
        print(f"  {cnt:4}  {fid_to_name.get(fid, '?')}")

    if not apply_changes:
        print("\nDry run — nothing written. Re-run with --apply.")
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
    print("Delete paper_index.pkl and faculty_index.pkl so the embeddings rebuild on next start.")


if __name__ == "__main__":
    main()
