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
        "id", "profile_id", "background", "objectives", "research_questions",
        "related_work", "methodology", "expected_outcomes", "created_at", "updated_at",
    ]
    con.close()


def test_init_profiles_db_proposals_table_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()
    web_app._init_profiles_db()  # must not raise on second run

    con = sqlite3.connect(db_path)
    cols = _columns(con, "proposals")
    assert cols.count("profile_id") == 1
    con.close()


def test_proposals_table_enforces_one_row_per_profile(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Test Person')")
    con.execute("INSERT INTO proposals (profile_id, background) VALUES (1, 'first')")
    con.commit()

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO proposals (profile_id, background) VALUES (1, 'second')")
        con.commit()
    con.close()
