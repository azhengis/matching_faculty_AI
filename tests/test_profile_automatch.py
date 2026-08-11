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


def test_unknown_email_gets_an_empty_profile_not_a_search_box(tmp_path, monkeypatch):
    """Nobody meets a name search. An address the directory doesn't know still
    lands in the assistant, with an empty profile to fill in by talking."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "stranger@depaul.edu")
    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert r.status_code == 200
    assert json.loads(r.body) == {"matched": False, "created_empty": True}

    con = sqlite3.connect(db_path)
    fid, bio, papers = con.execute(
        "SELECT faculty_id, bio_text, confirmed_paper_ids FROM profiles").fetchone()
    con.close()
    assert fid is None and bio == "" and json.loads(papers) == []


def test_a_near_miss_address_is_never_linked_to_the_record(tmp_path, monkeypatch):
    """Matching stays exact-after-normalization. A near miss may get an empty
    profile, but it must never be attached to somebody else's faculty record,
    because that would hand over their publications and their overlay."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "jane@depaul.education")
    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is False

    con = sqlite3.connect(db_path)
    fid = con.execute("SELECT faculty_id FROM profiles").fetchone()[0]
    con.close()
    assert fid is None


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


def test_invisible_characters_in_the_directory_still_match(tmp_path, monkeypatch):
    """21 scraped addresses carry a trailing zero-width space. Those people
    could never be matched by anything a human types."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "jbrooke@depaul.edu")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty (id, name, email, research_summary) "
                "VALUES (9, 'J Brooke', 'jbrooke@depaul.edu​', 'Summary.')")
    con.commit(); con.close()

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is True


def test_matches_across_depaul_subdomains(tmp_path, monkeypatch):
    """Signing in as name@depaul.edu should find name@cs.depaul.edu. Nobody
    remembers which subdomain the directory captured."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "mobasher@depaul.edu")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty (id, name, email, research_summary) "
                "VALUES (10, 'Bamshad Mobasher', 'mobasher@cs.depaul.edu', 'Summary.')")
    con.commit(); con.close()

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is True
    assert json.loads(r.body)["name"] == "Bamshad Mobasher"


def test_a_different_local_part_never_matches(tmp_path, monkeypatch):
    """bmobasher@ is not mobasher@. Guessing they are the same person is how
    you sign someone in as a colleague."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "bmobasher@cs.depaul.edu")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty (id, name, email, research_summary) "
                "VALUES (11, 'Bamshad Mobasher', 'mobasher@cs.depaul.edu', 'Summary.')")
    con.commit(); con.close()

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is False


def test_an_ambiguous_local_part_is_refused(tmp_path, monkeypatch):
    """Two people sharing a local part across subdomains: match neither."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "jsmith@depaul.edu")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty (id, name, email) VALUES (12, 'J Smith', 'jsmith@cs.depaul.edu')")
    con.execute("INSERT INTO faculty (id, name, email) VALUES (13, 'Jo Smith', 'jsmith@cdm.depaul.edu')")
    con.commit(); con.close()

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is False


def test_non_depaul_domain_does_not_get_the_subdomain_fallback(tmp_path, monkeypatch):
    """The fallback is for DePaul subdomains only, not for any address that
    happens to share a local part."""
    db_path, cookies = _setup(tmp_path, monkeypatch, "mobasher@gmail.com")
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO faculty (id, name, email) VALUES (14, 'B M', 'mobasher@cs.depaul.edu')")
    con.commit(); con.close()

    r = _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    assert json.loads(r.body)["matched"] is False
