import sqlite3

import pytest

import web_app


def _columns(con, table):
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def test_init_profiles_db_creates_proposals_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    cols = _columns(con, "proposals")
    assert cols == [
        "id", "project_id", "background", "objectives", "research_questions",
        "related_work", "methodology", "expected_outcomes", "created_at",
        "updated_at", "edited_sections",
    ]
    con.close()


def test_init_profiles_db_creates_projects_and_matches(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    assert _columns(con, "projects") == [
        "id", "profile_id", "title", "intake", "session_id", "status",
        "created_at", "updated_at",
    ]
    assert _columns(con, "project_matches") == [
        "id", "project_id", "faculty_id", "name", "title", "department",
        "email", "match_tier", "match_pct", "why_match", "created_at",
    ]
    con.close()


def test_init_profiles_db_proposals_table_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()
    web_app._init_profiles_db()  # must not raise on second run

    con = sqlite3.connect(db_path)
    cols = _columns(con, "proposals")
    assert cols.count("project_id") == 1
    con.close()


def test_proposals_table_enforces_one_row_per_project(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Test Person')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'P')")
    con.execute("INSERT INTO proposals (project_id, background) VALUES (1, 'first')")
    con.commit()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO proposals (project_id, background) VALUES (1, 'second')")
        con.commit()
    con.close()


def test_one_profile_may_hold_several_projects(tmp_path, monkeypatch):
    """The whole point of the re-key: a researcher runs several projects at once."""
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Test Person')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'First')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (2, 1, 'Second')")
    con.execute("INSERT INTO proposals (project_id, background) VALUES (1, 'one')")
    con.execute("INSERT INTO proposals (project_id, background) VALUES (2, 'two')")
    con.commit()

    rows = con.execute(
        "SELECT p.background FROM proposals p JOIN projects pr ON pr.id = p.project_id "
        "WHERE pr.profile_id = 1 ORDER BY p.background"
    ).fetchall()
    assert [r[0] for r in rows] == ["one", "two"]
    con.close()


def test_legacy_profile_keyed_proposals_are_migrated(tmp_path, monkeypatch):
    """An existing install must keep its proposal, re-homed under a project."""
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    # Build the pre-migration shape by hand, then let init migrate it.
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
            project_description TEXT
        )
    """)
    con.execute("""
        CREATE TABLE proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            background TEXT, objectives TEXT, research_questions TEXT,
            related_work TEXT, methodology TEXT, expected_outcomes TEXT,
            edited_sections TEXT DEFAULT '[]',
            created_at TEXT, updated_at TEXT,
            UNIQUE(profile_id)
        )
    """)
    con.execute("INSERT INTO profiles (id, name, project_description) VALUES (7, 'Old User', 'Studying bees. And more.')")
    con.execute("INSERT INTO proposals (profile_id, background, methodology) VALUES (7, 'legacy bg', 'legacy method')")
    con.commit()
    con.close()

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    assert "project_id" in _columns(con, "proposals")
    row = con.execute(
        "SELECT pr.profile_id, pr.title, p.background, p.methodology "
        "FROM proposals p JOIN projects pr ON pr.id = p.project_id"
    ).fetchone()
    assert row[0] == 7                      # adopted by a project owned by the same profile
    assert row[1] == "Studying bees."       # titled from the project description
    assert row[2] == "legacy bg"            # content preserved
    assert row[3] == "legacy method"
    con.close()


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """Re-running init must not duplicate projects or lose the proposal."""
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, project_description TEXT)")
    con.execute("""
        CREATE TABLE proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL,
            background TEXT, objectives TEXT, research_questions TEXT,
            related_work TEXT, methodology TEXT, expected_outcomes TEXT,
            created_at TEXT, updated_at TEXT, UNIQUE(profile_id)
        )
    """)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'A')")
    con.execute("INSERT INTO proposals (profile_id, background) VALUES (1, 'bg')")
    con.commit(); con.close()

    web_app._init_profiles_db()
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 1
    con.close()
