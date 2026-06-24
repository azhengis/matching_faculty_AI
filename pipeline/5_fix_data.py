#!/usr/bin/env python3
"""
fix_data.py
-----------
Cleans known data quality issues in faculty.db in-place:

  1. Non-breaking spaces (\xa0) → regular space in all text fields
  2. Zero-width spaces (​) → removed from department / college names
  3. Address/phone boilerplate stripped from classes_taught (DePaul University footer)
  4. Very short summaries (< 25 chars, useless) → cleared so faculty show as no-summary
  5. Multi-college strings → first college kept for filter consistency

Run once after db_setup.py:
    python3 fix_data.py
"""
import sqlite3, re, os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB    = os.path.join(_ROOT, "faculty.db")


def clean_whitespace(text):
    if not text:
        return text
    text = text.replace("\xa0", " ")   # non-breaking space → space
    text = text.replace("​", "")  # zero-width space → gone
    text = text.replace("‎", "")  # left-to-right mark → gone
    text = re.sub(r" {2,}", " ", text) # collapse multiple spaces
    return text.strip()


def clean_courses(text):
    if not text:
        return text
    text = re.sub(r"DePaul University.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\b1\s*E\.?\s*Jackson.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\(?\d{3}\)?\s*\d{3}[-.\s]\d{4}", "", text)  # phone numbers
    text = re.sub(r"Chicago,?\s*IL\b.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def first_college(text):
    """For faculty listed in multiple colleges, keep the first one."""
    if not text:
        return text
    return text.split(",")[0].strip()


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    rows = cur.execute("SELECT id, name, research_summary, classes_taught, department, college FROM faculty").fetchall()
    print(f"Checking {len(rows)} faculty records...")

    updated = 0
    for row in rows:
        fid, name, summary, courses, dept, college = row
        orig = (summary, courses, dept, college)

        # 1 & 2: whitespace cleanup on all text fields
        summary = clean_whitespace(summary)
        courses  = clean_whitespace(courses)
        dept     = clean_whitespace(dept)
        college  = clean_whitespace(college)

        # 3: strip address boilerplate from courses
        courses = clean_courses(courses)

        # 4: summaries that are too short to be useful → clear them
        if summary and len(summary.strip()) < 25:
            print(f"  Clearing useless summary for {name}: {repr(summary)}")
            summary = ""

        # 5: multi-college → take first
        if college and "," in college:
            college = first_college(college)

        new = (summary, courses, dept, college)
        if new != orig:
            cur.execute(
                "UPDATE faculty SET research_summary=?, classes_taught=?, department=?, college=? WHERE id=?",
                (summary, courses, dept, college, fid),
            )
            updated += 1

    con.commit()
    con.close()
    print(f"Updated {updated} records.")
    print("Done. Delete faculty_index.pkl and re-run search.py to pick up changes.")
    print("  rm -f faculty_index.pkl paper_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
