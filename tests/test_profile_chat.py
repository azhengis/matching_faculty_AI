"""The profile assistant's edit tool — the deterministic half of profile chat.

The conversation is a model; the writes are code. These tests pin the writes:
edits apply only to the caller's own profile, omitted fields keep their saved
values, paper removal is subtractive, and the public matching overlay still
obeys the email-ownership rule no matter what the chat asks for.
"""
import asyncio
import json
import sqlite3

import web_app
from web_app import _apply_profile_chat_update


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _setup(tmp_path, monkeypatch, account_email="jane@depaul.edu"):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT)")
    con.execute("INSERT INTO faculty (id, name, email, title, department) "
                "VALUES (7, 'Jane Doe', 'jane@depaul.edu', 'Professor', 'Computing')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany(
        "INSERT INTO papers (id, faculty_id, title, year, cited_by_count) VALUES (?, 7, ?, ?, ?)",
        [(1, "Real paper", 2024, 10), (2, "Also real", 2022, 5), (3, "Not mine", 2019, 40)])
    con.commit(); con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": account_email, "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    user = {"id": web_app._auth_sessions[token], "email": account_email}
    _run(web_app.api_profile_save(_FakeRequest({
        "faculty_id": 7, "name": "Jane Doe", "bio_text": "Scraped bio.",
        "confirmed_paper_ids": [1, 2, 3], "research_interests": ["ml"]},
        {"session_token": token})))
    return db_path, user


def _profile(db_path):
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT bio_text, research_interests, confirmed_paper_ids FROM profiles").fetchone()
    con.close()
    return row[0], json.loads(row[1]), json.loads(row[2])


def test_bio_update_leaves_other_fields_alone(tmp_path, monkeypatch):
    db_path, user = _setup(tmp_path, monkeypatch)
    result = _apply_profile_chat_update(user, {"bio_text": "Corrected bio."})
    assert result == {"status": "saved", "changed": ["bio"]}
    bio, interests, papers = _profile(db_path)
    assert bio == "Corrected bio."
    assert interests == ["ml"] and papers == [1, 2, 3]


def test_interests_are_a_full_replacement_deduped(tmp_path, monkeypatch):
    db_path, user = _setup(tmp_path, monkeypatch)
    _apply_profile_chat_update(user, {"research_interests": ["ml", "  fairness ", "ml", ""]})
    _, interests, _ = _profile(db_path)
    assert interests == ["ml", "fairness"]


def test_paper_removal_is_subtractive_only(tmp_path, monkeypatch):
    db_path, user = _setup(tmp_path, monkeypatch)
    result = _apply_profile_chat_update(user, {"remove_paper_ids": [3]})
    assert "publications" in result["changed"]
    _, _, papers = _profile(db_path)
    assert papers == [1, 2]


def test_empty_args_are_an_error_not_a_wipe(tmp_path, monkeypatch):
    db_path, user = _setup(tmp_path, monkeypatch)
    result = _apply_profile_chat_update(user, {})
    assert result["status"] == "error"
    assert _profile(db_path) == ("Scraped bio.", ["ml"], [1, 2, 3])


def test_chat_edits_reach_the_overlay_for_the_verified_owner(tmp_path, monkeypatch):
    db_path, user = _setup(tmp_path, monkeypatch)  # account email == faculty email
    _apply_profile_chat_update(user, {"bio_text": "What I actually do."})
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT self_bio FROM faculty_overrides WHERE email = 'jane@depaul.edu'").fetchone()
    con.close()
    assert row and row[0] == "What I actually do."


def test_chat_edits_never_reach_the_overlay_for_a_mismatched_email(tmp_path, monkeypatch):
    """The rule no conversation can talk around: your account email must match
    the faculty record before your edits change what OTHERS see."""
    db_path, user = _setup(tmp_path, monkeypatch, account_email="impostor@depaul.edu")
    _apply_profile_chat_update(user, {"bio_text": "I am definitely Jane."})
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT self_bio FROM faculty_overrides WHERE email = 'jane@depaul.edu'").fetchone()
    con.close()
    assert row is None  # own profile updated, public overlay untouched
    assert _profile(db_path)[0] == "I am definitely Jane."


def test_no_profile_yet_is_an_error(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    result = _apply_profile_chat_update({"id": 999, "email": "x@y.z"}, {"bio_text": "hi"})
    assert result["status"] == "error"
