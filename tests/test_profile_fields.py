"""Typing the bio and interests directly, without going through the assistant.

Requested in the 7/29 review: the assistant should not be the only way to fix
your own bio. Both editors write the same columns through the same overlay
sync, so the panel and the assistant can never disagree about what is on file.
"""
import asyncio
import json
import sqlite3

import web_app
from web_app import api_profile_fields, api_profile_me, api_profile_save


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT, college TEXT)")
    con.execute("INSERT INTO faculty (id, name, email) VALUES (7, 'Jane Doe', 'jane@depaul.edu')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}
    _run(api_profile_save(_FakeRequest(
        {"faculty_id": 7, "name": "Jane Doe", "bio_text": "Scraped bio.",
         "confirmed_paper_ids": [], "research_interests": ["fairness"]}, cookies)))
    return db_path, cookies


def _fields(cookies, **body):
    return _run(api_profile_fields(_FakeRequest(body, cookies)))


def _me(cookies):
    return json.loads(_run(api_profile_me(_FakeRequest(cookies=cookies))).body)


def test_editing_the_bio_leaves_interests_alone(tmp_path, monkeypatch):
    _, cookies = _setup(tmp_path, monkeypatch)
    _fields(cookies, bio_text="My own words.")

    me = _me(cookies)
    assert me["bio"] == "My own words."
    assert me["research_interests"] == ["fairness"]


def test_editing_interests_leaves_the_bio_alone(tmp_path, monkeypatch):
    _, cookies = _setup(tmp_path, monkeypatch)
    _fields(cookies, research_interests=["privacy in health data"])

    me = _me(cookies)
    assert me["bio"] == "Scraped bio."
    assert me["research_interests"] == ["privacy in health data"]


def test_interests_keep_full_phrases_and_drop_blanks_and_duplicates(tmp_path, monkeypatch):
    """Bamshad asked for interests richer than one word, so nothing truncates
    a phrase. Blank lines are a typing artefact, not an interest."""
    _, cookies = _setup(tmp_path, monkeypatch)
    _fields(cookies, research_interests=[
        "fairness auditing of clinical risk models",
        "   ",
        "privacy in health data",
        "fairness auditing of clinical risk models",
    ])

    assert _me(cookies)["research_interests"] == [
        "fairness auditing of clinical risk models",
        "privacy in health data",
    ]


def test_a_typed_empty_bio_is_accepted_but_the_assistant_still_cannot_send_one(tmp_path, monkeypatch):
    """Clearing your own bio is deliberate when you typed it. The same empty
    string arriving from the model is a mistake worth refusing."""
    _, cookies = _setup(tmp_path, monkeypatch)

    _fields(cookies, bio_text="")
    assert _me(cookies)["bio"] == ""

    result = web_app._apply_profile_chat_update({"id": 1, "email": "jane@depaul.edu"}, {"bio_text": "  "})
    assert result["status"] == "error"


def test_a_typed_edit_reaches_the_matching_overlay(tmp_path, monkeypatch):
    """A correction nobody else sees is not a correction."""
    db_path, cookies = _setup(tmp_path, monkeypatch)
    _fields(cookies, bio_text="What I actually work on.",
            research_interests=["network security"])

    con = sqlite3.connect(db_path)
    bio, interests = con.execute(
        "SELECT self_bio, self_research_interests FROM faculty_overrides "
        "WHERE email = 'jane@depaul.edu'").fetchone()
    con.close()
    assert bio == "What I actually work on."
    assert json.loads(interests) == ["network security"]


def test_malformed_and_empty_payloads_are_rejected(tmp_path, monkeypatch):
    _, cookies = _setup(tmp_path, monkeypatch)
    assert _fields(cookies, research_interests="not a list").status_code == 400
    assert _fields(cookies).status_code == 400


def test_editing_requires_a_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert _fields({}, bio_text="anyone?").status_code == 401
