#!/usr/bin/env python3
"""
fix_data.py
-----------
Cleans known data quality issues in faculty.db in-place:

  1. Non-breaking spaces (\xa0) → regular space in all text fields
  2. Zero-width spaces (​) → removed from department / college names
  3. DePaul site footer excised from research_summary and classes_taught
  4. Contentless summaries (invisible characters, torn fragments) → cleared
  5. Multi-college strings → first college kept for filter consistency

Run once after db_setup.py:
    python3 fix_data.py
"""
import sqlite3, re, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB    = os.path.join(_ROOT, "faculty.db")
sys.path.insert(0, _ROOT)
from text_clean import strip_site_chrome, has_site_chrome, is_junk_summary   # noqa: E402


def clean_whitespace(text):
    if not text:
        return text
    text = text.replace("\xa0", " ")   # non-breaking space → space
    text = text.replace("​", "")  # zero-width space → gone
    text = text.replace("‎", "")  # left-to-right mark → gone
    text = re.sub(r" {2,}", " ", text) # collapse multiple spaces
    return text.strip()


def clean_courses(text):
    """Course-list specific tidying, after the shared footer excision.

    These rules TRUNCATE from the match onward, which is safe here (no course
    list has real text after the footer) but is exactly why research summaries
    get strip_site_chrome instead: 45 of them carry research areas on both
    sides of the footer, and truncating drops the second half.
    """
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

    updated = chrome_fixed = emptied = 0
    for row in rows:
        fid, name, summary, courses, dept, college = row
        orig = (summary, courses, dept, college)

        # 1 & 2: whitespace cleanup on all text fields
        summary = clean_whitespace(summary)
        courses  = clean_whitespace(courses)
        dept     = clean_whitespace(dept)
        college  = clean_whitespace(college)

        # 3: excise the site footer the bio scrape swept up. Summaries get
        # excision only — see clean_courses for why courses may be truncated.
        if has_site_chrome(summary):
            chrome_fixed += 1
            summary = strip_site_chrome(summary)
            if not summary:
                emptied += 1
        courses = strip_site_chrome(courses)
        courses = clean_courses(courses)

        # 4: summaries with no research content → clear them. Length alone is
        # not the test: see is_junk_summary for why "Screenwriting" stays.
        if is_junk_summary(summary):
            print(f"  Clearing empty summary for {name}: {repr(summary)}")
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

    # The same footer reached profiles that were pre-filled from a scraped bio.
    polluted_bios = 0
    try:
        prows = cur.execute("SELECT id, bio_text FROM profiles").fetchall()
    except sqlite3.OperationalError:
        prows = []   # profiles table only exists once the web app has run
    for pid, bio in prows:
        if has_site_chrome(bio):
            cur.execute("UPDATE profiles SET bio_text = ? WHERE id = ?", (strip_site_chrome(bio), pid))
            polluted_bios += 1

    con.commit()
    con.close()
    print(f"Updated {updated} records.")
    print(f"  site footer excised from {chrome_fixed} research summaries "
          f"({emptied} of which were nothing BUT footer, so they are now empty)")
    if polluted_bios:
        print(f"  site footer excised from {polluted_bios} user profile bios")
    print("Done. Delete faculty_index.pkl and re-run search.py to pick up changes.")
    print("  rm -f faculty_index.pkl paper_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
