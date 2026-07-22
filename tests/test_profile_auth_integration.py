import asyncio
import json
import sqlite3

import web_app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _init_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    return db_path


def _signup(email="jane@depaul.edu", password="hunter222"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": password})))
    return list(web_app._auth_sessions.keys())[-1]


def _make_faculty(db_path, faculty_id, email):
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE IF NOT EXISTS faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, research_summary TEXT)"
    )
    con.execute(
        "INSERT INTO faculty (id, name, email, research_summary) VALUES (?, ?, ?, ?)",
        (faculty_id, "Jane Doe", email, "Studies AI.")
    )
    con.commit()
    con.close()


def test_save_profile_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_profile_save(_FakeRequest({"name": "Jane Doe"}, cookies={})))
    assert response.status_code == 401


def test_save_profile_upserts_one_row_per_user(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    token = _signup()

    body1 = {"name": "Jane Doe", "bio_text": "First bio.", "project_description": "p1",
             "confirmed_paper_ids": [], "research_interests": []}
    r1 = _run(web_app.api_profile_save(_FakeRequest(body1, cookies={"session_token": token})))
    assert r1.status_code == 200
    profile_id_1 = _body(r1)["profile_id"]

    body2 = {"name": "Jane Doe", "bio_text": "Updated bio.", "project_description": "p2",
             "confirmed_paper_ids": [], "research_interests": ["nlp"]}
    r2 = _run(web_app.api_profile_save(_FakeRequest(body2, cookies={"session_token": token})))
    assert r2.status_code == 200
    profile_id_2 = _body(r2)["profile_id"]

    assert profile_id_1 == profile_id_2

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    bio = con.execute("SELECT bio_text FROM profiles WHERE id = ?", (profile_id_1,)).fetchone()[0]
    email = con.execute("SELECT email FROM profiles WHERE id = ?", (profile_id_1,)).fetchone()[0]
    con.close()
    assert count == 1
    assert bio == "Updated bio."
    assert email == "jane@depaul.edu"  # always the account's own email, never client-submitted


def test_save_profile_writes_override_when_email_matches_faculty(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    _make_faculty(db_path, faculty_id=1, email="jane@depaul.edu")
    token = _signup(email="jane@depaul.edu")

    body = {"faculty_id": 1, "name": "Jane Doe", "bio_text": "I study AI.",
            "project_description": "p", "confirmed_paper_ids": [], "research_interests": ["ai"]}
    response = _run(web_app.api_profile_save(_FakeRequest(body, cookies={"session_token": token})))
    assert response.status_code == 200

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT self_bio FROM faculty_overrides WHERE email = 'jane@depaul.edu'").fetchone()
    con.close()
    assert row is not None
    assert row[0] == "I study AI."


def test_save_profile_skips_override_when_email_does_not_match_faculty(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    _make_faculty(db_path, faculty_id=1, email="somebody-else@depaul.edu")
    token = _signup(email="jane@depaul.edu")

    body = {"faculty_id": 1, "name": "Jane Doe", "bio_text": "I study AI.",
            "project_description": "p", "confirmed_paper_ids": [], "research_interests": []}
    response = _run(web_app.api_profile_save(_FakeRequest(body, cookies={"session_token": token})))
    assert response.status_code == 200  # profile save still succeeds

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM faculty_overrides").fetchone()[0]
    con.close()
    assert count == 0


def test_get_my_profile_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_profile_me(_FakeRequest(cookies={})))
    assert response.status_code == 401


def test_get_my_profile_returns_404_when_none_saved(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup()

    response = _run(web_app.api_profile_me(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 404


def test_get_my_profile_returns_saved_profile(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup()
    body = {"name": "Jane Doe", "bio_text": "My bio.", "project_description": "p",
            "confirmed_paper_ids": [], "research_interests": ["ai"]}
    _run(web_app.api_profile_save(_FakeRequest(body, cookies={"session_token": token})))

    response = _run(web_app.api_profile_me(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    body = _body(response)
    assert body["name"] == "Jane Doe"
    assert body["bio"] == "My bio."


def test_get_proposal_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_project_proposal(1, _FakeRequest(cookies={})))
    assert response.status_code == 401


def test_project_routes_require_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    anon = _FakeRequest(cookies={})

    assert _run(web_app.api_projects_list(anon)).status_code == 401
    assert _run(web_app.api_projects_create(_FakeRequest({"intake": {}}))).status_code == 401
    assert _run(web_app.api_project_get(1, anon)).status_code == 401
    assert _run(web_app.api_project_matches(1, anon)).status_code == 401
    assert _run(web_app.api_project_delete(1, anon)).status_code == 401


def test_faculty_overrides_lookup_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_profile_faculty_overrides("jane@depaul.edu", _FakeRequest(cookies={})))
    assert response.status_code == 401


def test_advisor_chat_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_advisor_chat(_FakeRequest({"message": "hi"}, cookies={})))
    assert response.status_code == 401


def test_advisor_chat_returns_503_when_chatbot_model_unset(tmp_path, monkeypatch):
    # This dev environment has no CHATBOT_MODEL set (confirmed elsewhere in
    # this project) — a logged-in user should still see the existing 503,
    # proving the new auth gate doesn't block a legitimate user from
    # reaching that pre-existing behavior.
    _init_db(tmp_path, monkeypatch)
    token = _signup()

    response = _run(web_app.api_advisor_chat(_FakeRequest({"message": "hi"}, cookies={"session_token": token})))
    assert response.status_code == 503
