#!/usr/bin/env python3
"""
db_setup.py
-----------
Loads depaul_faculty_enriched.json into a SQLite database,
keeping only full-time faculty.

Run once:  python3 db_setup.py
Output:    faculty.db
"""
import json, sqlite3, os, re, collections

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


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _identifying_email(value):
    """The address, lowercased, or "" when the field does not hold one.

    The email column is not reliably an email. Column shifts in the original
    scrape left "Full Time", "Faculty", and "School of Music" sitting in it,
    and matching people on those merges everyone who shares an employment
    status into one record.
    """
    email = (value or "").strip().lower()
    return email if _EMAIL_RE.match(email) else ""


def shared_emails(records):
    """Addresses that belong to more than one person, so cannot identify one.

    Two kinds turn up. Departmental inboxes listed on everybody's record:
    theatreschoolpr@depaul.edu sits on ten Theatre School people, lawcomm@ on
    three. And whatever the scrape put in the email column when it shifted:
    "Full Time" appears on eleven records, "Faculty" on nine.

    Both are catastrophic as identities. Keying on them told _dedupe that ten
    different people were one person, so nine of them were dropped from the
    database entirely before anything else ran.
    """
    # Count DISTINCT people per address, not records: a joint appointment
    # legitimately lists the same person twice.
    by_email = collections.defaultdict(set)
    for p in records:
        email = _identifying_email(p.get("email"))
        if email:
            by_email[email].add(" ".join((p.get("name") or "").lower().split()))
    return {em for em, names in by_email.items() if len(names) > 1}


def _identity(p, ambiguous=frozenset()):
    """Stable key for a roster record: email when it identifies one person,
    otherwise the name. Used to recognise somebody across re-runs so their
    `faculty.id` survives and their publications stay attached."""
    email = _identifying_email(p.get("email"))
    if email and email not in ambiguous:
        return ("email", email)
    return ("name", " ".join((p.get("name") or "").lower().split()))


def _dedupe(records, ambiguous=frozenset()):
    """Collapse roster rows that describe the same person.

    Someone with a joint appointment appears once per unit; keeping both would
    create two faculty rows competing for the same publications.
    """
    out = {}
    for p in records:
        key = _identity(p, ambiguous)
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

    ambiguous = shared_emails(people)
    if ambiguous:
        print(f"  {len(ambiguous)} address(es) belong to more than one person and are "
              f"ignored for identity, e.g. {sorted(ambiguous)[0]}")
    full_time = _dedupe([p for p in people if _is_full_time(p)], ambiguous)
    print(f"Total records: {len(people)}  |  Full-time after dedupe: {len(full_time)}")

    con = sqlite3.connect(DB_OUT)
    cur = con.cursor()
    cur.execute(CREATE_SQL)

    # Upsert rather than DELETE + reinsert. The old version wiped the table and
    # let AUTOINCREMENT hand out fresh ids, which silently orphaned every row
    # keyed on faculty.id — papers, saved profiles, and project matches all
    # point at it. Matching on a stable identity keeps those references intact
    # and lets this be re-run safely whenever the roster changes.
    # Index BOTH keys for every row and look the incoming record up under
    # either. Keying each row one way only meant an identity that CHANGED
    # between runs inserted a duplicate instead of updating: the June scrape
    # mis-parsed six people's email column, so when a correct address finally
    # arrived the email lookup missed and a second row appeared for somebody
    # already in the table.
    #
    # Not every email identifies a person, though, and treating one that does
    # not is how a third "Bella Itkin" got created:
    #
    #   theatreschoolpr@depaul.edu is a shared Theatre School inbox on ten
    #   different people's records, and lawcomm@ is on three.
    #
    #   "Full Time", "Faculty", "Part Time" and "School of Music" appear in the
    #   email column outright — the old scrape shifted columns, and those are
    #   employment statuses, not addresses.
    #
    # Both kinds fall back to matching on name.
    by_email, by_name = {}, {}
    for fid, name, email in cur.execute("SELECT id, name, email FROM faculty"):
        em = _identifying_email(email)
        nm = " ".join((name or "").lower().split())
        if em and em not in ambiguous:
            by_email.setdefault(em, fid)
        if nm:
            by_name.setdefault(nm, fid)

    def find(person):
        em = _identifying_email(person.get("email"))
        nm = " ".join((person.get("name") or "").lower().split())
        if em and em not in ambiguous:
            hit = by_email.get(em)
            if hit:
                return hit
        return by_name.get(nm)

    fields = ("name", "title", "department", "college", "employment_status",
              "personnel_type", "email", "bio_url", "research_summary",
              "publications_text", "classes_taught")

    added = updated = 0
    for p in full_time:
        values = [p.get(f, "") for f in fields] + [json.dumps(p.get("research_topics", []))]
        fid    = find(p)
        if fid is None:
            cur.execute(
                f"INSERT INTO faculty ({', '.join(fields)}, research_topics) "
                f"VALUES ({', '.join('?' * (len(fields) + 1))})", values)
            # Register the new row under both keys so a later duplicate in the
            # same run updates it rather than inserting again.
            new_id = cur.lastrowid
            em = _identifying_email(p.get("email"))
            nm = " ".join((p.get("name") or "").lower().split())
            if em and em not in ambiguous:
                by_email.setdefault(em, new_id)
            if nm:
                by_name.setdefault(nm, new_id)
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
