#!/usr/bin/env python3
"""
clean_papers.py
---------------
Removes misattributed papers from the papers table.

The fetch scripts find authors by name match, which can return a completely
different researcher who shares a name with the DePaul faculty member.
Telltale signs: papers with tens of thousands of citations attached to
a writing professor, a school counselor, a design professor, etc.

Two-pass cleanup:
  Pass 1 — citation ceiling: papers cited >500 are almost certainly not
            from a DePaul faculty member (those landmark papers come from
            large research consortiums / famous scientists with the same name)
  Pass 2 — topic coherence: embed each faculty member's summary and each
            of their papers; drop papers whose embedding is too far from the
            faculty's own research embedding (similarity < MIN_FIELD_SIM).

Run:
    python3 clean_papers.py
Then rebuild paper index:
    rm -f paper_index.pkl && python3 search.py
"""
import sys, os, re, sqlite3
from collections import defaultdict

_ROOT          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB             = os.path.join(_ROOT, "faculty.db")
MAX_CITATIONS    = 500   # above this almost certainly a wrong person
MIN_KW_OVERLAP   = 1     # paper must share at least this many domain-specific words
GENERIC_DOC_FREQ = 0.30  # words appearing in >30% of all bios are generic noise


def load_faculty_lookup():
    """Return {faculty_id: (name, bio_text)} for all faculty with papers."""
    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT f.id, f.name, f.research_summary, f.classes_taught
        FROM faculty f
        WHERE EXISTS (SELECT 1 FROM papers p WHERE p.faculty_id = f.id)
    """).fetchall()
    con.close()
    lookup = {}
    for fid, name, summary, courses in rows:
        text = (summary or "").strip()
        if not text and courses:
            text = (courses or "")[:500]
        lookup[fid] = (name, text)
    return lookup


def build_generic_words(fac_lookup):
    """Compute words that appear in >GENERIC_DOC_FREQ of all faculty bios.
    These are automatically identified as too generic to signal field membership.
    No hardcoded stopword list — derived entirely from the corpus itself.
    """
    from collections import Counter
    all_bios = [text for _, text in fac_lookup.values() if text]
    n_docs   = len(all_bios)
    doc_freq = Counter()
    for bio in all_bios:
        words = set(re.findall(r"[a-z]{4,}", bio.lower()))
        doc_freq.update(words)
    generic = {w for w, cnt in doc_freq.items() if cnt / n_docs > GENERIC_DOC_FREQ}
    print(f"  Corpus: {n_docs} bios  →  {len(generic)} generic words auto-detected "
          f"(appear in >{GENERIC_DOC_FREQ*100:.0f}% of bios)")
    return generic


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    total_before = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    fac_before   = cur.execute("SELECT COUNT(DISTINCT faculty_id) FROM papers").fetchone()[0]
    print(f"Papers before cleanup : {total_before}  ({fac_before} faculty)")

    # --- Pass 1: citation ceiling ---
    high_cited = cur.execute(
        "SELECT id, title, cited_by_count FROM papers WHERE cited_by_count > ?",
        (MAX_CITATIONS,)
    ).fetchall()
    print(f"\nPass 1 — removing {len(high_cited)} papers with >{MAX_CITATIONS} citations:")
    for pid, title, c in high_cited[:10]:
        print(f"  cited={c:7d}  {title[:70]}")
    if len(high_cited) > 10:
        print(f"  ... and {len(high_cited)-10} more")

    cur.execute("DELETE FROM papers WHERE cited_by_count > ?", (MAX_CITATIONS,))
    con.commit()
    after_pass1 = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    print(f"After Pass 1: {after_pass1} papers remain")

    # --- Pass 2: domain-specific keyword overlap ---
    # Generic words (appearing in >30% of all bios) are auto-detected from the
    # corpus — no hardcoded stopword list. Only domain-specific words count.
    # If a paper shares zero domain-specific words with the faculty bio, it
    # almost certainly belongs to a different researcher with the same name.

    fac_lookup   = load_faculty_lookup()
    generic_words = build_generic_words(fac_lookup)

    def extract_keywords(text):
        return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
                if w not in generic_words}

    papers_rows = con.execute(
        "SELECT id, faculty_id, title, abstract FROM papers ORDER BY faculty_id"
    ).fetchall()

    from collections import defaultdict
    by_fac = defaultdict(list)
    for pid, fid, title, abstract in papers_rows:
        by_fac[fid].append((pid, title, abstract))

    print(f"\nPass 2 — keyword overlap check (min shared words ≥ {MIN_KW_OVERLAP})")
    delete_ids = []
    checked_fac = 0
    for fid, fpaps in by_fac.items():
        if fid not in fac_lookup:
            continue
        _, bio_text = fac_lookup[fid]
        bio_kws = extract_keywords(bio_text)
        if len(bio_kws) < 8:
            # Bio too sparse to make a reliable judgment — skip
            continue

        for pid, title, abstract in fpaps:
            paper_text = f"{title} {abstract or ''}"
            paper_kws  = extract_keywords(paper_text)
            if not paper_kws:
                continue
            overlap = len(bio_kws & paper_kws)
            if overlap < MIN_KW_OVERLAP:
                delete_ids.append((pid, title, overlap))

        checked_fac += 1
        if checked_fac % 50 == 0:
            print(f"  Checked {checked_fac}/{len(by_fac)} faculty...")

    print(f"Pass 2 — removing {len(delete_ids)} off-field papers")
    if delete_ids:
        for pid, title, overlap in delete_ids[:10]:
            print(f"  [overlap={overlap}] {title[:70]}")
        if len(delete_ids) > 10:
            print(f"  ... and {len(delete_ids)-10} more")
        cur.executemany("DELETE FROM papers WHERE id=?", [(i,) for i, _, _ in delete_ids])
        con.commit()

    total_after = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    fac_after   = cur.execute("SELECT COUNT(DISTINCT faculty_id) FROM papers").fetchone()[0]
    con.close()

    print(f"\nSummary:")
    print(f"  Before : {total_before} papers ({fac_before} faculty)")
    print(f"  After  : {total_after} papers ({fac_after} faculty)")
    print(f"  Removed: {total_before - total_after} misattributed papers")
    print(f"\nRebuild paper index:")
    print(f"  rm -f paper_index.pkl && python3 search.py")


if __name__ == "__main__":
    main()
