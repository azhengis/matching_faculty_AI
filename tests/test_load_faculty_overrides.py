import json
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
    # Mirrors the real papers schema — load_faculty ranks titles by citations
    # when it has to compose a research summary for a faculty member with no bio.
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "faculty_id INTEGER, title TEXT, abstract TEXT, year INTEGER, "
                "cited_by_count INTEGER DEFAULT 0)")
    con.commit()
    con.close()
    return db_path


def test_load_faculty_uses_self_bio_and_interests_when_override_exists(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Dr. Jane Doe is a professor.', '')"
    )
    con.execute("""
        CREATE TABLE faculty_overrides (
            email TEXT PRIMARY KEY, self_bio TEXT,
            self_research_interests TEXT DEFAULT '[]',
            self_editor_email TEXT, updated_at TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "I study reinforcement learning for robotics.",
         json.dumps(["reinforcement learning", "robotics"]))
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    summary = people[0]["research_summary"]
    assert summary.startswith("Research interests: reinforcement learning, robotics")
    assert "I study reinforcement learning for robotics." in summary
    assert "Dr. Jane Doe is a professor." not in summary


def test_load_faculty_unchanged_when_no_override_exists(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'John Smith', 'john@depaul.edu', 'John studies computer vision.', '')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    assert people[0]["research_summary"] == "John studies computer vision."


def test_load_faculty_override_survives_faculty_id_churn(tmp_path, monkeypatch):
    """Simulates a pipeline re-run: faculty.id changes, but the override still
    joins correctly because faculty_overrides is keyed by email, not id."""
    db_path = _make_db(tmp_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Old scraped bio.', '')"
    )
    con.execute("""
        CREATE TABLE faculty_overrides (
            email TEXT PRIMARY KEY, self_bio TEXT,
            self_research_interests TEXT DEFAULT '[]',
            self_editor_email TEXT, updated_at TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "My real self-written bio.", json.dumps(["nlp"]))
    )
    con.commit()

    # Simulate pipeline/4_db_setup.py's DELETE FROM faculty + re-INSERT, which
    # reassigns a new AUTOINCREMENT id even though it's the same person.
    con.execute("DELETE FROM faculty")
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary, classes_taught) "
        "VALUES (50, 'Jane Doe', 'jane@depaul.edu', 'Old scraped bio.', '')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(sm, "DB", str(db_path))

    people = sm.load_faculty()

    assert len(people) == 1
    assert people[0]["id"] == 50
    assert "My real self-written bio." in people[0]["research_summary"]
