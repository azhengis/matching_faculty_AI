"""Building a profile from the account email alone.

Most DePaul faculty are in the scraped directory under their real address, so
signing in as jdoe@depaul.edu should skip the name search. Email equality is
the same ownership rule the matching overlay already uses, so this grants no
new access — but that makes it worth pinning: a near-miss address must not
match, and an existing profile must never be overwritten.
"""
import asyncio
import json
import sqlite3

import web_app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _setup(tmp_path, monkeypatch, account_email):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "research_summary TEXT, title TEXT, department TEXT)")
    con.execute("INSERT INTO faculty (id, name, email, research_summary) "
                "VALUES (7, 'Jane Doe', 'Jane@DePaul.edu', 'Scraped summary.')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany("INSERT INTO papers (id, faculty_id, title, year, cited_by_count) "
                    "VALUES (?, 7, ?, ?, ?)",
                    [(1, "Newer", 2024, 3), (2, "Older", 2015, 90)])
    con.commit(); con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": account_email, "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    return db_path, {"session_token": token}


def test_matches_case_insensitively_and_pulls_everything(tmp_path, monkeypatch):
    db_path, cookies = _setup(tmp_path, monkeypatch, "jane@depaul.edu")  # record says Jane@DePaul.edu
    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert r.status_code == 200
    body = json.loads(r.body)
    assert body["matched"] is True and body["papers"] == 2

    me = json.loads(_run(web_app.api_profile_me(_FakeRequest(cookies=cookies))).body)
    assert me["bio"] == "Scraped summary."
    assert [p["title"] for p in me["papers"]] == ["Newer", "Older"]  # recency order


def test_a_prior_self_edit_outranks_the_scrape(tmp_path, monkeypatch):
    """Someone who corrected their bio before should get their own words back."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "jane@depaul.edu")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty_overrides (email, self_bio, self_research_interests) "
                "VALUES ('jane@depaul.edu', 'What I actually study.', '[\"fairness\"]')")
    con.commit(); con.close()

    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    me = json.loads(_run(web_app.api_profile_me(_FakeRequest(cookies=cookies))).body)
    assert me["bio"] == "What I actually study."
    assert me["research_interests"] == ["fairness"]


def test_unknown_email_does_not_match(tmp_path, monkeypatch):
    _, cookies = _setup(tmp_path, monkeypatch, "stranger@depaul.edu")
    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert r.status_code == 404


def test_a_near_miss_address_does_not_match(tmp_path, monkeypatch):
    """Matching is exact-after-normalization — no fuzzy name or domain logic,
    because a false match hands someone another person's profile."""
    _, cookies = _setup(tmp_path, monkeypatch, "jane@depaul.education")
    assert _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies))).status_code == 404


def test_existing_profile_is_never_overwritten(tmp_path, monkeypatch):
    db_path, cookies = _setup(tmp_path, monkeypatch, "jane@depaul.edu")
    _run(web_app.api_profile_save(_FakeRequest(
        {"faculty_id": None, "name": "Jane Doe", "bio_text": "My own bio.",
         "confirmed_paper_ids": [], "research_interests": []}, cookies)))

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert r.status_code == 404
    me = json.loads(_run(web_app.api_profile_me(_FakeRequest(cookies=cookies))).body)
    assert me["bio"] == "My own bio."


def test_requires_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "jane@depaul.edu")
    assert _run(web_app.api_profile_automatch(_FakeRequest())).status_code == 401
