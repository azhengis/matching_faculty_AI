import sqlite3

import web_app


def _columns(con, table):
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def test_init_profiles_db_adds_research_interests_column(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    assert "research_interests" in _columns(con, "profiles")
    con.close()


def test_init_profiles_db_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()
    web_app._init_profiles_db()  # must not raise on second run

    con = sqlite3.connect(db_path)
    assert _columns(con, "profiles").count("research_interests") == 1
    con.close()


def test_init_profiles_db_creates_faculty_overrides_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    cols = _columns(con, "faculty_overrides")
    assert cols == ["email", "self_bio", "self_research_interests", "self_editor_email", "updated_at"]
    con.close()
