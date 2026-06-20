#!/usr/bin/env python3
"""
db_setup.py
-----------
Loads depaul_faculty_enriched.json into a SQLite database,
keeping only full-time faculty.

Run once:  python3 db_setup.py
Output:    faculty.db
"""
import json, sqlite3

JSON_IN = "depaul_faculty_enriched.json"
DB_OUT  = "faculty.db"

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

def main():
    with open(JSON_IN, encoding="utf-8") as f:
        people = json.load(f)

    full_time = [p for p in people if p.get("employment_status") == "Full Time"]
    print(f"Total records: {len(people)}  |  Full-time: {len(full_time)}")

    con = sqlite3.connect(DB_OUT)
    cur = con.cursor()
    cur.execute(CREATE_SQL)
    cur.execute("DELETE FROM faculty")

    cur.executemany(
        """INSERT INTO faculty
               (name, title, department, college, employment_status, personnel_type,
                email, bio_url, research_summary, publications_text,
                classes_taught, research_topics)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(
            p.get("name", ""),
            p.get("title", ""),
            p.get("department", ""),
            p.get("college", ""),
            p.get("employment_status", ""),
            p.get("personnel_type", ""),
            p.get("email", ""),
            p.get("bio_url", ""),
            p.get("research_summary", ""),
            p.get("publications_text", ""),
            p.get("classes_taught", ""),
            json.dumps(p.get("research_topics", [])),
        ) for p in full_time]
    )
    con.commit()

    total      = cur.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    searchable = cur.execute(
        "SELECT COUNT(*) FROM faculty WHERE TRIM(research_summary) != ''"
    ).fetchone()[0]

    print(f"Saved {total} full-time faculty to {DB_OUT}")
    print(f"  {searchable} have research summaries (will be searchable)")
    con.close()


if __name__ == "__main__":
    main()
