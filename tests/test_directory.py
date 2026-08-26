"""The people directory: look anybody up, read-only unless it's you.

Browsing is separate from claiming. /api/profile/search exists to help somebody
find their OWN record and caps at six results; this one pages and searches the
unit fields too, because "School of Music" is a reasonable thing to type into a
people search.

The ownership rule is the load-bearing part. A directory that let colleagues
rewrite each other's listings would feed those edits straight into matching, so
only the person themselves is offered a way in.
"""
import asyncio
import json
import sqlite3

import pytest

import web_app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, title TEXT, "
                "department TEXT, college TEXT, email TEXT, bio_url TEXT, "
                "research_summary TEXT, publications_text TEXT, classes_taught TEXT)")
    con.executemany(
        "INSERT INTO faculty (id, name, title, department, college, email, bio_url, "
        "research_summary, publications_text, classes_taught) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(1, "Matthew Bachman", "Assistant Professor", "Neuroscience", "Science and Health",
          "mbachma3@depaul.edu", "https://www.depaul.edu/faculty/matthew-bachman",
          "Attention and value-based decision making.", "", "NEU 201"),
         (2, "Dana Hall", "Professor", "Music Performance", "School of Music",
          "dhall@depaul.edu", "", "Jazz percussion and improvisation.", "", ""),
         (3, "Ann Marie Brink", "Associate Professor", "Music Performance", "School of Music",
          "abrink@depaul.edu", "", "", "", "")])
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany("INSERT INTO papers (faculty_id, title, year, cited_by_count) VALUES (?,?,?,?)",
                    [(1, "Contesting risk scores", 2025, 4), (1, "Caseworker discretion", 2023, 30)])
    con.commit()
    con.close()
    return db_path


def _signin(email):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": "hunter222"})))
    return {"session_token": list(web_app._auth_sessions.keys())[-1]}


def _search(cookies, **kw):
    return json.loads(_run(web_app.api_directory_search(_FakeRequest(cookies=cookies), **kw)).body)


def _detail(cookies, fid):
    return json.loads(_run(web_app.api_faculty_profile(fid, _FakeRequest(cookies=cookies))).body)


# ── Finding people ──────────────────────────────────────────────────────────

def test_searching_a_surname_finds_the_person(db):
    cookies = _signin("someone@depaul.edu")
    found = _search(cookies, q="bachman")
    assert [p["name"] for p in found["people"]] == ["Matthew Bachman"]
    assert found["total"] == 1


def test_searching_a_unit_finds_everyone_in_it(db):
    """A people search should accept "School of Music", not only a name."""
    cookies = _signin("someone@depaul.edu")
    found = _search(cookies, q="School of Music")
    assert {p["name"] for p in found["people"]} == {"Dana Hall", "Ann Marie Brink"}


def test_a_name_match_outranks_a_department_match(db):
    """Somebody typing a name wants the person, even when the word also appears
    in somebody else's unit."""
    con = sqlite3.connect(db)
    con.execute("INSERT INTO faculty (id, name, department, email) "
                "VALUES (4, 'Music', 'Neuroscience', 'music@depaul.edu')")
    con.commit()
    con.close()

    cookies = _signin("someone@depaul.edu")
    assert _search(cookies, q="music")["people"][0]["name"] == "Music"


def test_an_empty_query_lists_everybody(db):
    cookies = _signin("someone@depaul.edu")
    assert _search(cookies, q="")["total"] == 4 - 1  # three seeded faculty


def test_results_page(db):
    cookies = _signin("someone@depaul.edu")
    first = _search(cookies, q="", limit=2, offset=0)
    second = _search(cookies, q="", limit=2, offset=2)
    assert len(first["people"]) == 2
    assert first["total"] == 3
    names = {p["name"] for p in first["people"]} | {p["name"] for p in second["people"]}
    assert len(names) == 3, "paging must not repeat or drop anybody"


def test_a_result_carries_enough_to_scan(db):
    cookies = _signin("someone@depaul.edu")
    person = _search(cookies, q="bachman")["people"][0]
    assert person["title"] == "Assistant Professor"
    assert person["blurb"].startswith("Attention and value-based")
    assert person["papers"] == 2


def test_no_match_is_an_empty_list_not_an_error(db):
    cookies = _signin("someone@depaul.edu")
    found = _search(cookies, q="zzzznobody")
    assert found["people"] == [] and found["total"] == 0


# ── Read-only unless it is your own record ─────────────────────────────────

def test_your_own_record_is_flagged(db):
    """The only place an edit path is offered."""
    cookies = _signin("mbachma3@depaul.edu")
    assert _detail(cookies, 1)["is_you"] is True


def test_somebody_elses_record_is_not(db):
    cookies = _signin("mbachma3@depaul.edu")
    assert _detail(cookies, 2)["is_you"] is False


def test_the_flag_ignores_case_and_padding(db):
    """Directory addresses are inconsistently cased; the account's is not."""
    con = sqlite3.connect(db)
    con.execute("UPDATE faculty SET email = '  MBachma3@DePaul.edu ' WHERE id = 1")
    con.commit()
    con.close()
    assert _detail(_signin("mbachma3@depaul.edu"), 1)["is_you"] is True


def test_a_record_with_no_email_belongs_to_nobody(db):
    """Otherwise an account with a blank email would match every blank record."""
    con = sqlite3.connect(db)
    con.execute("UPDATE faculty SET email = '' WHERE id = 3")
    con.commit()
    con.close()
    assert _detail(_signin("someone@depaul.edu"), 3)["is_you"] is False


# ── Guards ─────────────────────────────────────────────────────────────────

def test_the_directory_requires_a_login(db):
    assert _run(web_app.api_directory_search(_FakeRequest(), q="a")).status_code == 401
    assert _run(web_app.api_faculty_profile(1, _FakeRequest())).status_code == 401


def test_the_page_size_is_capped(db):
    """A caller cannot ask for the whole table in one request."""
    cookies = _signin("someone@depaul.edu")
    assert _search(cookies, q="", limit=100000)["limit"] == 100


def test_a_negative_offset_is_clamped(db):
    cookies = _signin("someone@depaul.edu")
    assert _search(cookies, q="", offset=-5)["offset"] == 0


def test_a_quote_in_the_query_is_not_sql(db):
    """The query is parameterised; this would be a syntax error if it were not."""
    cookies = _signin("someone@depaul.edu")
    found = _search(cookies, q="' OR 1=1 --")
    assert found["people"] == []
