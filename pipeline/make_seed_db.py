#!/usr/bin/env python3
"""
make_seed_db.py
---------------
Build a deployable copy of faculty.db containing ONLY public faculty data.

The working database accumulates real user data as you test: accounts with
password hashes, login sessions, profiles, projects, proposals, uploaded
documents. None of that belongs on a shared host — it is yours, it would let
anyone with server access read your session tokens, and testers should start
from a clean slate anyway.

This copies the database, drops every user-generated table's contents, and
VACUUMs so the deleted rows are actually gone from the file rather than just
unlinked (a plain DELETE leaves the data recoverable in free pages).

Usage:
    python3 pipeline/make_seed_db.py                  # -> seed_faculty.db
    python3 pipeline/make_seed_db.py --out /tmp/x.db
"""
import argparse
import os
import shutil
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Everything a user creates. Order matters only for readability — foreign keys
# are not enforced by default in SQLite, and we are emptying all of them anyway.
USER_TABLES = [
    "auth_sessions",
    "profile_documents",
    "project_matches",
    "proposals",
    "projects",
    "profiles",
    "faculty_overrides",
    "users",
]

# Public data that must survive.
KEEP_TABLES = ["faculty", "papers", "scholar_papers"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.path.join(ROOT, "faculty.db"))
    ap.add_argument("--out", default=os.path.join(ROOT, "seed_faculty.db"))
    args = ap.parse_args()

    if not os.path.exists(args.source):
        sys.exit(f"No database at {args.source} — run the pipeline first.")
    if os.path.abspath(args.source) == os.path.abspath(args.out):
        sys.exit("Refusing to overwrite the source database.")

    # .backup rather than a file copy: it takes a consistent snapshot even if
    # the app is running and mid-write.
    src = sqlite3.connect(args.source)
    if os.path.exists(args.out):
        os.remove(args.out)
    dst = sqlite3.connect(args.out)
    src.backup(dst)
    src.close()

    existing = {r[0] for r in dst.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    print(f"Source: {args.source}")
    for t in KEEP_TABLES:
        if t in existing:
            n = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  keeping {t:20s} {n:>7,} rows")

    print("Stripping user data:")
    for t in USER_TABLES:
        if t not in existing:
            continue
        n = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        dst.execute(f"DELETE FROM {t}")
        print(f"  cleared {t:20s} {n:>7,} rows")

    # Reset AUTOINCREMENT counters so a fresh deployment starts ids at 1 rather
    # than continuing from your local testing.
    if "sqlite_sequence" in existing:
        marks = ",".join("?" * len(USER_TABLES))
        dst.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({marks})", USER_TABLES)

    dst.commit()
    dst.execute("VACUUM")          # actually reclaim the pages
    dst.close()

    # Verify, rather than trust the deletes above.
    check = sqlite3.connect(args.out)
    leftovers = []
    for t in USER_TABLES:
        if t in existing:
            n = check.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if n:
                leftovers.append(f"{t} ({n} rows)")
    faculty = check.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
    check.close()

    if leftovers:
        sys.exit(f"FAILED — user data survived in: {', '.join(leftovers)}")
    if not faculty:
        sys.exit("FAILED — no faculty rows in the seed; it would match nothing.")

    size = os.path.getsize(args.out) / 1e6
    print(f"\nWrote {args.out}  ({size:.1f} MB, {faculty:,} faculty, no user data)")


if __name__ == "__main__":
    main()
