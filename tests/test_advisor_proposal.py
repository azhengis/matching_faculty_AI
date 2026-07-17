import asyncio
import json
import sqlite3

import web_app
from web_app import _save_proposal, api_profile_proposal


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


def _signup(email, password="hunter222"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": password})))
    return list(web_app._auth_sessions.keys())[-1]


def test_save_proposal_upserts_and_preserves_omitted_optional_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane Doe')")
    con.commit()
    con.close()

    result = _save_proposal(1, {
        "background": "AI ethics in imagery.",
        "objectives": "Understand consent issues.",
        "research_questions": "How does AI challenge consent?",
        "related_work": "Manovich, Paglen.",
        "methodology": "Historical analysis + case studies.",
        "expected_outcomes": "A framework for ethical guidelines.",
    })
    assert result == {"status": "saved"}

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT background, objectives, research_questions, related_work, methodology, expected_outcomes "
        "FROM proposals WHERE profile_id = 1"
    ).fetchone()
    con.close()
    assert row == (
        "AI ethics in imagery.", "Understand consent issues.",
        "How does AI challenge consent?", "Manovich, Paglen.",
        "Historical analysis + case studies.", "A framework for ethical guidelines.",
    )

    # Second call omits related_work/expected_outcomes — they must be
    # preserved from the first call, not wiped to empty.
    result2 = _save_proposal(1, {
        "background": "Updated background.",
        "objectives": "Understand consent issues.",
        "research_questions": "How does AI challenge consent, revisited?",
        "methodology": "Historical analysis + case studies.",
    })
    assert result2 == {"status": "saved"}

    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT background, research_questions, related_work, expected_outcomes FROM proposals WHERE profile_id = 1"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "Updated background."
    assert rows[0][1] == "How does AI challenge consent, revisited?"
    assert rows[0][2] == "Manovich, Paglen."
    assert rows[0][3] == "A framework for ethical guidelines."


def test_save_proposal_skips_write_when_profile_id_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    result = _save_proposal(None, {"background": "test"})
    assert result == {"status": "error", "reason": "no profile"}

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    con.close()
    assert count == 0


def test_get_proposal_returns_empty_defaults_when_none_saved(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    token = _signup("nobody@depaul.edu")

    response = _run(api_profile_proposal(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert _body(response) == {
        "background": "", "objectives": "", "research_questions": "",
        "related_work": "", "methodology": "", "expected_outcomes": "",
    }


def test_get_proposal_returns_saved_values(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    token = _signup("john@depaul.edu")
    user_id = web_app._auth_sessions[token]

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name, user_id) VALUES (2, 'John Smith', ?)", (user_id,))
    con.execute(
        "INSERT INTO proposals (profile_id, background, objectives, research_questions, methodology) "
        "VALUES (2, 'bg', 'obj', 'rq', 'method')"
    )
    con.commit()
    con.close()

    response = _run(api_profile_proposal(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    body = _body(response)
    assert body["background"] == "bg"
    assert body["objectives"] == "obj"
    assert body["research_questions"] == "rq"
    assert body["methodology"] == "method"
    assert body["related_work"] == ""
    assert body["expected_outcomes"] == ""
