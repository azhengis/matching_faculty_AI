import asyncio
import json
import sqlite3

import web_app
from web_app import api_profile_save, api_profile_faculty_overrides, api_profile_get


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _make_db(tmp_path):
    db_path = tmp_path / "test_faculty.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE faculty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT, research_summary TEXT
        )
    """)
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary) VALUES (1, 'Jane Doe', 'jane@depaul.edu', 'Studies AI.')"
    )
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary) VALUES (2, 'No Email Guy', '', 'Studies things.')"
    )
    con.commit()
    con.close()
    return db_path


def test_save_profile_persists_research_interests_and_upserts_override(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": 1,
        "name": "Jane Doe",
        "email": "jane@depaul.edu",
        "bio_text": "I study reinforcement learning.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["reinforcement learning", "robotics"],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200
    profile_id = _body(response)["profile_id"]

    con = sqlite3.connect(db_path)
    profile_row = con.execute(
        "SELECT bio_text, research_interests FROM profiles WHERE id = ?", (profile_id,)
    ).fetchone()
    assert profile_row[0] == "I study reinforcement learning."
    assert json.loads(profile_row[1]) == ["reinforcement learning", "robotics"]

    override_row = con.execute(
        "SELECT self_bio, self_research_interests, self_editor_email FROM faculty_overrides WHERE email = ?",
        ("jane@depaul.edu",)
    ).fetchone()
    assert override_row[0] == "I study reinforcement learning."
    assert json.loads(override_row[1]) == ["reinforcement learning", "robotics"]
    assert override_row[2] == "jane@depaul.edu"
    con.close()


def test_save_profile_skips_override_when_faculty_id_is_none(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": None,
        "name": "Manual Person",
        "email": "manual@example.com",
        "bio_text": "I am not in the directory.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["ethics"],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM faculty_overrides").fetchone()[0]
    assert count == 0
    con.close()


def test_save_profile_skips_override_when_faculty_email_is_blank(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    body = {
        "faculty_id": 2,
        "name": "No Email Guy",
        "email": "",
        "bio_text": "I study things.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": [],
    }
    response = _run(api_profile_save(_FakeRequest(body)))
    assert response.status_code == 200  # profile save still succeeds

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM faculty_overrides").fetchone()[0]
    assert count == 0
    con.close()


def test_faculty_overrides_endpoint_returns_defaults_when_no_row(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    response = _run(api_profile_faculty_overrides("nobody@example.com"))
    assert response.status_code == 200
    assert _body(response) == {"self_bio": "", "self_research_interests": []}


def test_faculty_overrides_endpoint_returns_existing_row_case_insensitively(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO faculty_overrides (email, self_bio, self_research_interests) VALUES (?, ?, ?)",
        ("jane@depaul.edu", "I study RL.", json.dumps(["reinforcement learning"]))
    )
    con.commit()
    con.close()

    response = _run(api_profile_faculty_overrides("JANE@DEPAUL.EDU"))
    assert response.status_code == 200
    body = _body(response)
    assert body["self_bio"] == "I study RL."
    assert body["self_research_interests"] == ["reinforcement learning"]


def test_get_profile_includes_research_interests(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    save_response = _run(api_profile_save(_FakeRequest({
        "faculty_id": 1,
        "name": "Jane Doe",
        "email": "jane@depaul.edu",
        "bio_text": "I study reinforcement learning.",
        "project_description": "A project.",
        "confirmed_paper_ids": [],
        "research_interests": ["reinforcement learning"],
    })))
    profile_id = _body(save_response)["profile_id"]

    get_response = _run(api_profile_get(profile_id))
    assert get_response.status_code == 200
    assert _body(get_response)["research_interests"] == ["reinforcement learning"]
