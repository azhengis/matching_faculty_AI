#!/usr/bin/env python3
"""What a refresh actually changed. Called by refresh.sh, harmless standalone.

Split out of the shell script because building these queries inside a heredoc
required escaping quotes inside quotes inside quotes, which is exactly the kind
of thing that breaks silently and reports nothing.
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "faculty.db")

CHECKS = [
    ("with a bio", "SELECT COUNT(*) FROM faculty "
                   "WHERE TRIM(COALESCE(research_summary,'')) != ''"),
    ("with research areas", "SELECT COUNT(*) FROM faculty "
                            "WHERE TRIM(COALESCE(research_topics,'')) NOT IN ('','[]')"),
    ("publications", "SELECT COUNT(*) FROM papers"),
]

MUST_BE_ZERO = [
    ("orphaned papers", "SELECT COUNT(*) FROM papers p "
                        "LEFT JOIN faculty f ON f.id = p.faculty_id WHERE f.id IS NULL"),
    ("records with page furniture", "SELECT COUNT(*) FROM faculty "
                                    "WHERE research_summary LIKE '%4DE PAUL%' "
                                    "OR research_summary LIKE '%1 E. Jackson%'"),
]


def main():
    before = int(os.environ.get("FACULTY_BEFORE") or 0)
    con = sqlite3.connect(DB)
    q = lambda sql: con.execute(sql).fetchone()[0]

    now = q("SELECT COUNT(*) FROM faculty")
    delta = f"({now - before:+d})" if before else ""
    print(f"  faculty              : {before} -> {now}  {delta}" if before
          else f"  faculty              : {now}")
    for label, sql in CHECKS:
        print(f"  {label:21}: {q(sql)}")

    problems = 0
    for label, sql in MUST_BE_ZERO:
        n = q(sql)
        flag = "" if n == 0 else "   <-- SHOULD BE 0"
        problems += n
        print(f"  {label:21}: {n}{flag}")
    con.close()
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
