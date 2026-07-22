import asyncio
import json
import sqlite3

import web_app
from web_app import _save_proposal, api_project_proposal, api_project_proposal_edit


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


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    return db_path


def _profile_with_project(db_path, token=None, profile_id=1, project_id=1):
    con = sqlite3.connect(db_path)
    if token is None:
        con.execute("INSERT INTO profiles (id, name) VALUES (?, 'Jane Doe')", (profile_id,))
    else:
        con.execute("INSERT INTO profiles (id, name, user_id) VALUES (?, 'Jane Doe', ?)",
                    (profile_id, web_app._auth_sessions[token]))
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (?, ?, 'A project')",
                (project_id, profile_id))
    con.commit()
    con.close()


def test_save_proposal_upserts_and_preserves_omitted_optional_fields(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    _profile_with_project(db_path)

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
        "FROM proposals WHERE project_id = 1"
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
        "SELECT background, research_questions, related_work, expected_outcomes "
        "FROM proposals WHERE project_id = 1"
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == "Updated background."
    assert rows[0][1] == "How does AI challenge consent, revisited?"
    assert rows[0][2] == "Manovich, Paglen."
    assert rows[0][3] == "A framework for ethical guidelines."


def test_save_proposal_accepts_a_single_section(tmp_path, monkeypatch):
    """Sections are saved one at a time as the conversation settles each one."""
    db_path = _setup(tmp_path, monkeypatch)
    _profile_with_project(db_path)

    assert _save_proposal(1, {"background": "Just the background."}) == {"status": "saved"}
    assert _save_proposal(1, {"methodology": "Just the methodology."}) == {"status": "saved"}

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT background, methodology, objectives FROM proposals WHERE project_id = 1").fetchone()
    con.close()
    assert row == ("Just the background.", "Just the methodology.", "")


def test_save_proposal_rejects_a_call_with_no_section_text(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    _profile_with_project(db_path)

    result = _save_proposal(1, {})
    assert result["status"] == "error"

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
    con.close()


def test_save_proposal_coerces_a_list_section_into_bullets(tmp_path, monkeypatch):
    """A weaker local model sometimes returns a list where a string was asked for."""
    db_path = _setup(tmp_path, monkeypatch)
    _profile_with_project(db_path)

    _save_proposal(1, {"research_questions": ["First question", "Second question"]})

    con = sqlite3.connect(db_path)
    value = con.execute("SELECT research_questions FROM proposals WHERE project_id = 1").fetchone()[0]
    con.close()
    assert value == "- First question\n- Second question"


def test_save_proposal_skips_write_when_project_id_missing(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)

    result = _save_proposal(None, {"background": "test"})
    assert result == {"status": "error", "reason": "no project"}

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    con.close()
    assert count == 0


def test_hand_edited_sections_survive_the_advisor(tmp_path, monkeypatch):
    """A section the researcher edited is never overwritten by save_proposal."""
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup("editor@depaul.edu")
    _profile_with_project(db_path, token)

    _run(api_project_proposal_edit(1, _FakeRequest(
        {"section": "methodology", "text": "My own wording."},
        cookies={"session_token": token})))

    result = _save_proposal(1, {"methodology": "Advisor rewrite.", "background": "Advisor bg."})
    assert result["skipped_sections"] == ["methodology"]

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT methodology, background FROM proposals WHERE project_id = 1").fetchone()
    con.close()
    assert row[0] == "My own wording."   # protected
    assert row[1] == "Advisor bg."       # unlocked section still written


def test_releasing_a_section_hands_it_back_to_the_advisor(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup("releaser@depaul.edu")
    _profile_with_project(db_path, token)

    req = lambda body: _FakeRequest(body, cookies={"session_token": token})
    _run(api_project_proposal_edit(1, req({"section": "methodology", "text": "Mine."})))
    _run(api_project_proposal_edit(1, req({"section": "methodology", "release": True})))

    assert _save_proposal(1, {"methodology": "Advisor rewrite."}) == {"status": "saved"}

    con = sqlite3.connect(db_path)
    value = con.execute("SELECT methodology FROM proposals WHERE project_id = 1").fetchone()[0]
    con.close()
    assert value == "Advisor rewrite."


def test_get_proposal_returns_empty_defaults_when_none_saved(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup("nobody@depaul.edu")
    _profile_with_project(db_path, token)

    response = _run(api_project_proposal(1, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert _body(response) == {
        "background": "", "objectives": "", "research_questions": "",
        "related_work": "", "methodology": "", "expected_outcomes": "",
        "edited_sections": [],
    }


def test_get_proposal_returns_saved_values(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup("john@depaul.edu")
    _profile_with_project(db_path, token, profile_id=2, project_id=5)

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO proposals (project_id, background, objectives, research_questions, methodology) "
        "VALUES (5, 'bg', 'obj', 'rq', 'method')"
    )
    con.commit()
    con.close()

    response = _run(api_project_proposal(5, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    body = _body(response)
    assert body["background"] == "bg"
    assert body["objectives"] == "obj"
    assert body["research_questions"] == "rq"
    assert body["methodology"] == "method"
    assert body["related_work"] == ""
    assert body["expected_outcomes"] == ""


def test_another_users_project_is_not_readable(tmp_path, monkeypatch):
    """Project routes must refuse a project the caller does not own."""
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    owner = _signup("owner@depaul.edu")
    _profile_with_project(db_path, owner, profile_id=1, project_id=1)
    intruder = _signup("intruder@depaul.edu")

    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name, user_id) VALUES (2, 'Other', ?)",
                (web_app._auth_sessions[intruder],))
    con.commit()
    con.close()

    response = _run(api_project_proposal(1, _FakeRequest(cookies={"session_token": intruder})))
    assert response.status_code == 404

    response = _run(api_project_proposal_edit(1, _FakeRequest(
        {"section": "background", "text": "hacked"},
        cookies={"session_token": intruder})))
    assert response.status_code == 404
