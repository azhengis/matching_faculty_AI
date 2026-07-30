"""Publication recency drives the synthesised research summary.

Citations accumulate with age, so ranking a faculty member's papers by
citation count describes them by their biggest hit rather than their current
work — a professor whose 2008 paper still dominates gets embedded, and
therefore matched, on research they may have left a decade ago. Recency leads
instead, with a short tail of most-cited work of any age so a collaborator
search can still reach what someone built their reputation on.
"""
import sqlite3

import search as sm


def _make_db(tmp_path):
    db_path = tmp_path / "test_faculty.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, research_summary TEXT, classes_taught TEXT
        )
    """)
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "faculty_id INTEGER, title TEXT, abstract TEXT, year INTEGER, "
                "cited_by_count INTEGER DEFAULT 0)")
    # No bio and no courses, so the summary has to be composed from papers.
    con.execute("INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
                "VALUES (1, 'Jane Doe', 'jane@depaul.edu', '', '')")
    con.commit()
    con.close()
    return db_path


def _add_papers(db_path, rows):
    con = sqlite3.connect(db_path)
    con.executemany(
        "INSERT INTO papers (faculty_id, title, year, cited_by_count) VALUES (1, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _summary(db_path, monkeypatch):
    monkeypatch.setattr(sm, "DB", str(db_path))
    return next(p for p in sm.load_faculty() if p["id"] == 1)["research_summary"]


def test_recent_work_leads_the_summary(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    _add_papers(db_path, [
        ("Consumption values and market choices", 1991, 9527),   # the old blockbuster
        ("The future of political marketing", 2025, 4),          # what they actually do now
        ("Understanding the 2022 midterm elections", 2023, 11),
    ])
    summary = _summary(db_path, monkeypatch)

    recent = summary.index("The future of political marketing")
    seminal = summary.index("Consumption values and market choices")
    assert recent < seminal, "current work must lead, not the most-cited paper"


def test_most_cited_work_still_appears_for_broader_matching(tmp_path, monkeypatch):
    """Recency identifies the research area; older work stays reachable.

    A search for the methods someone is known for should still find them, so
    the seminal tail is appended rather than dropped.
    """
    db_path = _make_db(tmp_path)
    recent = [(f"Recent paper {i}", 2020 + i, 1) for i in range(9)]
    _add_papers(db_path, recent + [("Quantum secret sharing", 1999, 4367)])

    summary = _summary(db_path, monkeypatch)
    assert "Quantum secret sharing" in summary
    assert "Recent paper 8" in summary


def test_summary_is_capped_and_does_not_repeat_a_title(tmp_path, monkeypatch):
    """A profile can carry a thousand papers; a wall of titles dilutes the
    embedding. A paper that is both recent and most-cited is listed once."""
    db_path = _make_db(tmp_path)
    _add_papers(db_path, [(f"Paper {i}", 2000 + i, i) for i in range(30)])

    summary = _summary(db_path, monkeypatch)
    titles = [ln for ln in summary.splitlines() if ln.startswith("- ")]
    assert len(titles) <= 12
    assert len(titles) == len(set(titles))


def test_papers_with_no_year_do_not_crash_or_outrank_dated_work(tmp_path, monkeypatch):
    """Some sources return a paper with a null year; it must sort last, not first."""
    db_path = _make_db(tmp_path)
    _add_papers(db_path, [
        ("Undated paper", None, 0),
        ("Dated recent paper", 2024, 0),
    ])
    summary = _summary(db_path, monkeypatch)
    assert summary.index("Dated recent paper") < summary.index("Undated paper")
