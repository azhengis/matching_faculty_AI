#!/usr/bin/env python3
"""
db_setup.py
-----------
Loads depaul_faculty_enriched.json into a SQLite database,
keeping only full-time faculty.

Run once:  python3 db_setup.py
Output:    faculty.db
"""
import json, sqlite3, os

_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_IN = os.path.join(_ROOT, "data", "depaul_faculty_enriched.json")
DB_OUT  = os.path.join(_ROOT, "faculty.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS faculty (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT,
    title             TEXT,
    department        TEXT,
    college           TEXT,
    employment_status TEXT,
    personnel_type    TEXT,
    email             TEXT,
    bio_url           TEXT,
    research_summary  TEXT,
    publications_text TEXT,
    classes_taught    TEXT,
    research_topics   TEXT
)
"""

def _is_full_time(p):
    """Decide full-time status, tolerating a messy/blank employment_status field.

    The roster source leaves employment_status blank (or garbled with a
    department/college name) for ~700 records that are otherwise clearly
    full-time by title. Only trust an explicit "Part Time" tag or an
    "Adjunct" title as a signal of part-time status; everything else with
    an unrecognized status falls back to being treated as full-time.
    """
    status = (p.get("employment_status") or "").strip()
    if status == "Full Time":
        return True
    if status == "Part Time":
        return False
    return "adjunct" not in (p.get("title") or "").lower()


def _identity(p):
    """Stable key for a roster record: email if there is one, else the name.

    Used to recognise someone across re-runs so their `faculty.id` survives.
    """
    email = (p.get("email") or "").strip().lower()
    if email:
        return ("email", email)
    return ("name", " ".join((p.get("name") or "").lower().split()))


def _dedupe(records):
    """Collapse roster rows that describe the same person.

    Someone with a joint appointment appears once per unit; keeping both would
    create two faculty rows competing for the same publications.
    """
    out = {}
    for p in records:
        key = _identity(p)
        if key == ("name", ""):
            continue
        prev = out.get(key)
        # Prefer the record carrying an actual research summary.
        if prev is None or len((p.get("research_summary") or "")) > len((prev.get("research_summary") or "")):
            out[key] = p
    return list(out.values())


def main():
    with open(JSON_IN, encoding="utf-8") as f:
        people = json.load(f)

    full_time = _dedupe([p for p in people if _is_full_time(p)])
    print(f"Total records: {len(people)}  |  Full-time after dedupe: {len(full_time)}")

    con = sqlite3.connect(DB_OUT)
    cur = con.cursor()
    cur.execute(CREATE_SQL)

    # Upsert rather than DELETE + reinsert. The old version wiped the table and
    # let AUTOINCREMENT hand out fresh ids, which silently orphaned every row
    # keyed on faculty.id — papers, saved profiles, and project matches all
    # point at it. Matching on a stable identity keeps those references intact
    # and lets this be re-run safely whenever the roster changes.
    existing = {}
    for fid, name, email in cur.execute("SELECT id, name, email FROM faculty"):
        em = (email or "").strip().lower()
        existing[("email", em) if em else ("name", " ".join((name or "").lower().split()))] = fid

    fields = ("name", "title", "department", "college", "employment_status",
              "personnel_type", "email", "bio_url", "research_summary",
              "publications_text", "classes_taught")

    added = updated = 0
    for p in full_time:
        values = [p.get(f, "") for f in fields] + [json.dumps(p.get("research_topics", []))]
        fid    = existing.get(_identity(p))
        if fid is None:
            cur.execute(
                f"INSERT INTO faculty ({', '.join(fields)}, research_topics) "
                f"VALUES ({', '.join('?' * (len(fields) + 1))})", values)
            added += 1
        else:
            cur.execute(
                f"UPDATE faculty SET {', '.join(f + ' = ?' for f in fields)}, research_topics = ? "
                f"WHERE id = ?", values + [fid])
            updated += 1
    con.commit()

    total      = cur.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    searchable = cur.execute(
        "SELECT COUNT(*) FROM faculty WHERE TRIM(research_summary) != ''"
    ).fetchone()[0]
    orphans = cur.execute(
        "SELECT COUNT(*) FROM papers WHERE faculty_id NOT IN (SELECT id FROM faculty)"
    ).fetchone()[0]

    print(f"Added {added} new faculty, updated {updated} existing.")
    print(f"Saved {total} full-time faculty to {DB_OUT}")
    print(f"  {searchable} have research summaries")
    print(f"  orphaned paper rows: {orphans}  (should be 0)")
    con.close()


if __name__ == "__main__":
    main()
