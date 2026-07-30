#!/usr/bin/env python3
"""
check_seed_pii.py
-----------------
Gate for data/seed_faculty.db.gz before it is committed.

That file is committed so a host with no persistent disk can build a working
image without a manual data upload. It ships in a PUBLIC repository, so two
things must hold every time it is regenerated:

  1. It contains no user data — no accounts, password hashes, session tokens,
     profiles, projects, or proposals.
  2. It exposes no email address that data/depaul_faculty_enriched.json does
     not already publish. That file is committed too, so the seed adds no new
     personal information; it repackages what is already there.

The second check is the one that matters over time. A future pipeline run
could pull in contact details for people the JSON never covered, and it would
land in a public repo with nobody noticing. This fails the build instead.

    python3 pipeline/check_seed_pii.py
"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SEED_GZ = os.path.join(ROOT, "data", "seed_faculty.db.gz")
ENRICHED = os.path.join(ROOT, "data", "depaul_faculty_enriched.json")

USER_TABLES = [
    "users", "auth_sessions", "profiles", "projects", "proposals",
    "project_matches", "profile_documents", "faculty_overrides",
]


def emails_from_json(path):
    with open(path, encoding="utf-8") as f:
        people = json.load(f)
    return {(p.get("email") or "").strip().lower() for p in people} - {""}


def main():
    if not os.path.exists(SEED_GZ):
        sys.exit(f"No seed at {SEED_GZ} — build one with:\n"
                 f"  python3 pipeline/make_seed_db.py --gzip-to data/seed_faculty.db.gz")
    if not os.path.exists(ENRICHED):
        sys.exit(f"Missing {ENRICHED}; cannot establish what is already public.")

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "seed.db")
    try:
        with gzip.open(SEED_GZ, "rb") as fin, open(db_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)

        con = sqlite3.connect(db_path)
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

        failures = []

        # 1. No user data.
        for t in USER_TABLES:
            if t in tables:
                n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                if n:
                    failures.append(f"{t} still holds {n} row(s)")

        # 2. No email the enriched JSON does not already publish.
        seed_emails = {
            (r[0] or "").strip().lower()
            for r in con.execute("SELECT email FROM faculty")
        } - {""}
        public_emails = emails_from_json(ENRICHED)
        new_emails = seed_emails - public_emails

        faculty = con.execute("SELECT COUNT(*) FROM faculty").fetchone()[0]
        papers = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        con.close()

        print(f"seed: {faculty:,} faculty, {papers:,} papers")
        print(f"emails in seed          : {len(seed_emails):,}")
        print(f"already public in data/ : {len(public_emails):,}")
        print(f"NEW emails introduced   : {len(new_emails):,}")

        if new_emails:
            sample = sorted(new_emails)[:5]
            failures.append(
                f"{len(new_emails)} email(s) not already in "
                f"data/depaul_faculty_enriched.json, e.g. {', '.join(sample)}")

        if not faculty:
            failures.append("no faculty rows — the seed would match nothing")

        if failures:
            print("\nFAILED — do not commit this seed:")
            for f in failures:
                print(f"  - {f}")
            sys.exit(1)

        print("\nOK — safe to commit: no user data, no new personal information.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
