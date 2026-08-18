"""The summary of research activities.

Asked for in the 7/29 review: a section beyond publications that reads the
proposals, unpublished manuscripts, and CV too, and says what problems this
researcher has been working on. Distinct from the bio, which stays theirs to
write. Generated on request, then editable by hand or through the assistant.
"""
import asyncio
import json
import sqlite3
import types

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


class _FakeResponse:
    def __init__(self, text):
        self.choices = [types.SimpleNamespace(
            message=types.SimpleNamespace(content=text), finish_reason="stop")]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT, college TEXT)")
    con.execute("INSERT INTO faculty (id, name, email, title, department) "
                "VALUES (7, 'Jane Doe', 'jane@depaul.edu', 'Professor', 'Computing')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany(
        "INSERT INTO papers (id, faculty_id, title, year, cited_by_count) VALUES (?, 7, ?, ?, ?)",
        [(1, "Contesting risk scores in child welfare", 2025, 4),
         (2, "Caseworker discretion and automation", 2023, 30)])
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}
    _run(web_app.api_profile_save(_FakeRequest(
        {"faculty_id": 7, "name": "Jane Doe", "bio_text": "I study algorithmic accountability.",
         "confirmed_paper_ids": [1, 2], "research_interests": ["algorithmic accountability"]}, cookies)))
    return db_path, cookies


def _stub_model(monkeypatch, reply="Jane Doe works on contestability of risk scores."):
    captured = {}
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(
        completion=lambda **kw: (captured.update(kw), _FakeResponse(reply))[1]))
    return captured


def _me(cookies):
    return json.loads(_run(web_app.api_profile_me(_FakeRequest(cookies=cookies))).body)


def test_the_summary_reads_unpublished_material_not_just_publications(profile, monkeypatch):
    """The whole reason this section exists: a grant proposal or unpublished
    manuscript shows a direction the publication list does not."""
    db_path, cookies = profile
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profile_documents (profile_id, kind, label, filename, extracted_text) "
                "VALUES (1, 'file', 'Grant proposal', 'prop.pdf', "
                "'Unpublished proposal: a county-level audit of contestability mechanisms.')")
    con.commit()
    con.close()

    captured = _stub_model(monkeypatch)
    _run(web_app.api_profile_activities(_FakeRequest(cookies=cookies)))

    source = captured["messages"][1]["content"]
    assert "county-level audit of contestability" in source
    assert "Contesting risk scores in child welfare" in source


def test_generating_saves_and_exposes_the_summary(profile, monkeypatch):
    _, cookies = profile
    _stub_model(monkeypatch, "Jane Doe studies contestability in child welfare.")

    body = json.loads(_run(web_app.api_profile_activities(_FakeRequest(cookies=cookies))).body)
    assert body["research_activities"] == "Jane Doe studies contestability in child welfare."
    assert _me(cookies)["research_activities"] == "Jane Doe studies contestability in child welfare."


def test_a_hand_edit_replaces_the_generated_draft(profile, monkeypatch):
    """The draft is a starting point, never the last word."""
    _, cookies = profile
    _stub_model(monkeypatch)
    _run(web_app.api_profile_activities(_FakeRequest(cookies=cookies)))

    _run(web_app.api_profile_fields(_FakeRequest({"research_activities": "My own summary."}, cookies)))
    assert _me(cookies)["research_activities"] == "My own summary."


def test_the_assistant_can_revise_the_summary(profile):
    _, cookies = profile
    result = web_app._apply_profile_chat_update(
        {"id": 1, "email": "jane@depaul.edu"}, {"research_activities": "Revised on request."})

    assert result["status"] == "saved"
    assert "research_activities" in result["changed"]
    assert _me(cookies)["research_activities"] == "Revised on request."


def test_editing_the_summary_leaves_the_bio_alone(profile):
    """The bio is how they describe themselves; the summary is the evidence
    read back. Writing one must never touch the other."""
    _, cookies = profile
    _run(web_app.api_profile_fields(_FakeRequest({"research_activities": "Activity summary."}, cookies)))

    me = _me(cookies)
    assert me["bio"] == "I study algorithmic accountability."
    assert me["research_activities"] == "Activity summary."


def test_generating_with_nothing_on_file_is_refused_not_invented(tmp_path, monkeypatch):
    """With no publications, documents, or bio there is nothing to summarise,
    and a model asked anyway would simply make something up."""
    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT, college TEXT)")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "new@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}
    _run(web_app.api_profile_save(_FakeRequest(
        {"faculty_id": None, "name": "New Person", "bio_text": "",
         "confirmed_paper_ids": [], "research_interests": []}, cookies)))

    _stub_model(monkeypatch)
    res = _run(web_app.api_profile_activities(_FakeRequest(cookies=cookies)))
    assert res.status_code == 400


def test_generating_requires_a_login(profile, monkeypatch):
    _stub_model(monkeypatch)
    assert _run(web_app.api_profile_activities(_FakeRequest())).status_code == 401
