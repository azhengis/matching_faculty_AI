"""Editing a saved profile corrects it rather than starting over.

Almost everything on a profile is scraped from a DePaul bio page or OpenAlex,
so it is wrong for somebody. Correcting it has to be cheap and must not cost
the user data they entered elsewhere.
"""
import asyncio
import json
import sqlite3

import web_app
from web_app import api_profile_save, api_profile_me


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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
    con.execute("INSERT INTO faculty (id, name, email, title, department, college) "
                "VALUES (7, 'Jane Doe', 'jane@depaul.edu', 'Professor', 'Computing', 'CDM')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany(
        "INSERT INTO papers (id, faculty_id, title, year, cited_by_count) VALUES (?, 7, ?, ?, ?)",
        [(1, "Old blockbuster", 2005, 900),
         (2, "Recent work", 2025, 3),
         (3, "Someone else's paper", 2019, 40)])
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    return db_path, {"session_token": token}


def _save(cookies, **fields):
    return _run(api_profile_save(_FakeRequest(fields, cookies)))


def _me(cookies):
    return json.loads(_run(api_profile_me(_FakeRequest(cookies=cookies))).body)


def test_editing_a_profile_updates_bio_interests_and_publications(tmp_path, monkeypatch):
    _, cookies = _setup(tmp_path, monkeypatch)
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="I study fairness in ML.",
          confirmed_paper_ids=[1, 2, 3], research_interests=["fairness", "ML"])

    # The edit: sharpen the bio, drop a misattributed paper, retag.
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="I study fairness in clinical ML.",
          confirmed_paper_ids=[1, 2], research_interests=["fairness", "clinical ML"])

    me = _me(cookies)
    assert me["bio"] == "I study fairness in clinical ML."
    assert me["research_interests"] == ["fairness", "clinical ML"]
    assert [p["title"] for p in me["papers"]] == ["Recent work", "Old blockbuster"]


def test_editing_does_not_wipe_a_project_description_it_no_longer_collects(tmp_path, monkeypatch):
    """The profile form dropped its project step, so it stops sending the field.

    An omitted key must preserve what is stored — otherwise every bio edit
    silently destroys the description older profiles still carry, which the
    advisor reads as background.
    """
    db_path, cookies = _setup(tmp_path, monkeypatch)
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="Bio.",
          confirmed_paper_ids=[1], research_interests=[])

    con = sqlite3.connect(db_path)
    con.execute("UPDATE profiles SET project_description = 'Legacy description'")
    con.commit()
    con.close()

    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="Revised bio.",
          confirmed_paper_ids=[1], research_interests=[])

    assert _me(cookies)["project_description"] == "Legacy description"


def test_an_explicit_project_description_is_still_written(tmp_path, monkeypatch):
    """Preserving on omission must not make the field unwritable."""
    _, cookies = _setup(tmp_path, monkeypatch)
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="Bio.",
          confirmed_paper_ids=[], research_interests=[],
          project_description="Explicitly set")

    assert _me(cookies)["project_description"] == "Explicitly set"


def test_a_corrected_bio_reaches_the_matching_overlay(tmp_path, monkeypatch):
    """A correction is pointless if matching still runs on the scraped text."""
    db_path, cookies = _setup(tmp_path, monkeypatch)
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="Scraped, and wrong.",
          confirmed_paper_ids=[], research_interests=["old topic"])
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="What I actually work on.",
          confirmed_paper_ids=[], research_interests=["new topic"])

    con = sqlite3.connect(db_path)
    bio, interests = con.execute(
        "SELECT self_bio, self_research_interests FROM faculty_overrides "
        "WHERE email = 'jane@depaul.edu'").fetchone()
    con.close()
    assert bio == "What I actually work on."
    assert json.loads(interests) == ["new topic"]


def test_confirmed_publications_come_back_newest_first(tmp_path, monkeypatch):
    """The edit form ticks boxes from this list, and recency leads everywhere."""
    _, cookies = _setup(tmp_path, monkeypatch)
    _save(cookies, faculty_id=7, name="Jane Doe", bio_text="Bio.",
          confirmed_paper_ids=[1, 2, 3], research_interests=[])

    years = [p["year"] for p in _me(cookies)["papers"]]
    assert years == sorted(years, reverse=True)
