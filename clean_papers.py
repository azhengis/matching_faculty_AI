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
  Pass 1 — citation ceiling: papers cited >2000 are almost certainly not
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
import sys, os, re, sqlite3, pickle
import numpy as np

DB             = "faculty.db"
INDEX          = "faculty_index.pkl"
PAPER_INDEX    = "paper_index.pkl"
MODEL_NAME     = "allenai/specter2_base"
MAX_CITATIONS  = 2000   # above this almost certainly a wrong person
MIN_FIELD_SIM  = 0.40   # paper must be at least this similar to faculty's own research


def load_faculty_lookup():
    """Return {faculty_id: research_summary} for all faculty with papers."""
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

    # --- Pass 2: topic coherence via SPECTER2 ---
    if not os.path.exists(INDEX):
        print("\nNo faculty_index.pkl found — skipping Pass 2 (topic coherence check).")
        print("Run search.py once to build the index, then re-run clean_papers.py.")
        con.close()
        return

    with open(INDEX, "rb") as f:
        idx_cache = pickle.load(f)
    fac_embs = idx_cache["emb"]   # shape (n_faculty, 768)

    # We need a mapping faculty_id → index in the people list
    # Load people the same way search.py does (same order)
    FRAGMENT_STARTERS = re.compile(
        r"^(are|is|include|includes|focus|focuses|have|has|span|spans|center|"
        r"cover|covers|examine|examines|explore|explores|involve|involves|"
        r"range|ranges|consist|consists)\b", re.IGNORECASE)
    def fix_summary(t):
        t = t.strip()
        if not t: return t
        if t[0].islower() or FRAGMENT_STARTERS.match(t):
            t = "Research interests " + t[0].lower() + t[1:]
        return t
    def is_bio(s, n):
        if not s or not n: return False
        p = n.strip().split(); f, l = p[0].lower(), p[-1].lower()
        o = s.strip()[:80].lower()
        return o.startswith(f) or o.startswith(l) or o.startswith("dr. " + l)
    def clean_courses(t):
        t = re.sub(r"DePaul University.*", "", t, flags=re.DOTALL|re.IGNORECASE)
        t = re.sub(r"\(?\d{3}\)?\s*\d{3}[-.\s]\d{4}", "", t)
        t = re.sub(r"\b1\s*E\.?\s*Jackson.*", "", t, flags=re.DOTALL|re.IGNORECASE)
        return t.strip()

    rows = con.execute("""
        SELECT id, name, research_summary, classes_taught FROM faculty
        WHERE TRIM(research_summary) != '' OR TRIM(COALESCE(classes_taught,'')) != ''
    """).fetchall()
    fac_id_to_emb_idx = {}
    for list_idx, (fid, name, summary, courses) in enumerate(rows):
        s = fix_summary(summary or "")
        c = clean_courses(courses or "")
        if is_bio(s, name) and c: s = f"Courses taught: {c}\n\n{s}"
        elif not s and c: s = f"Courses taught: {c}"
        if s.strip():
            fac_id_to_emb_idx[fid] = list_idx

    # For each faculty's papers, embed and compare to faculty embedding
    print(f"\nPass 2 — topic coherence check (threshold: cosine ≥ {MIN_FIELD_SIM})")
    print("Loading SPECTER2 model...")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(MODEL_NAME)
    except ImportError:
        print("sentence-transformers not installed — skipping Pass 2")
        con.close()
        return

    papers_rows = con.execute(
        "SELECT id, faculty_id, title, abstract FROM papers ORDER BY faculty_id"
    ).fetchall()

    # Group by faculty
    from collections import defaultdict
    by_fac = defaultdict(list)
    for pid, fid, title, abstract in papers_rows:
        by_fac[fid].append((pid, title, abstract))

    delete_ids = []
    checked_fac = 0
    for fid, fpaps in by_fac.items():
        if fid not in fac_id_to_emb_idx:
            continue
        if len(fac_embs) <= fac_id_to_emb_idx[fid]:
            continue
        fac_emb = fac_embs[fac_id_to_emb_idx[fid]]

        # Embed each paper
        texts = [
            f"{title}. {(abstract or '')[:500]}" if abstract else title
            for _, title, abstract in fpaps
        ]
        pembs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        sims  = pembs @ fac_emb

        for (pid, title, _), sim in zip(fpaps, sims):
            if float(sim) < MIN_FIELD_SIM:
                delete_ids.append(pid)

        checked_fac += 1
        if checked_fac % 50 == 0:
            print(f"  Checked {checked_fac}/{len(by_fac)} faculty...")

    print(f"Pass 2 — removing {len(delete_ids)} off-field papers")
    if delete_ids:
        cur.executemany("DELETE FROM papers WHERE id=?", [(i,) for i in delete_ids])
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
