"""Rename a project and remove individual matched collaborators."""
import asyncio
import json
import sqlite3

import web_app


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(resp):
    return json.loads(resp.body)


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
    return db_path


def _signup(email="prof@depaul.edu"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": "hunter222"})))
    return list(web_app._auth_sessions.keys())[-1]


def _project(db_path, token, title="A project", project_id=1):
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profiles (id, name, user_id) VALUES (1, 'Jane Doe', ?)",
                (web_app._auth_sessions[token],))
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (?, 1, ?)", (project_id, title))
    con.commit()
    con.close()


def test_rename_updates_the_title(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup()
    _project(db_path, token)

    resp = _run(web_app.api_project_rename(1, _FakeRequest({"title": "  Recovery groups vs peer networks  "},
                                                           cookies={"session_token": token})))
    assert resp.status_code == 200
    assert _body(resp)["title"] == "Recovery groups vs peer networks"   # trimmed

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Recovery groups vs peer networks"
    con.close()


def test_rename_rejects_empty_title(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup()
    _project(db_path, token, title="Original")

    resp = _run(web_app.api_project_rename(1, _FakeRequest({"title": "   "}, cookies={"session_token": token})))
    assert resp.status_code == 400
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Original"  # unchanged
    con.close()


def test_cannot_rename_another_users_project(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    owner = _signup("owner@depaul.edu")
    _project(db_path, owner, title="Owned")
    intruder = _signup("intruder@depaul.edu")

    resp = _run(web_app.api_project_rename(1, _FakeRequest({"title": "Hijacked"},
                                                           cookies={"session_token": intruder})))
    assert resp.status_code == 404
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Owned"
    con.close()


def test_remove_one_match_leaves_the_others(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    token = _signup()
    _project(db_path, token)

    con = sqlite3.connect(db_path)
    con.executemany(
        "INSERT INTO project_matches (id, project_id, name) VALUES (?, 1, ?)",
        [(10, "Keep Me"), (11, "Remove Me")])
    con.commit()
    con.close()

    resp = _run(web_app.api_project_match_delete(1, 11, _FakeRequest(cookies={"session_token": token})))
    assert resp.status_code == 200

    con = sqlite3.connect(db_path)
    names = [r[0] for r in con.execute("SELECT name FROM project_matches WHERE project_id = 1")]
    con.close()
    assert names == ["Keep Me"]


def test_cannot_remove_a_match_from_another_users_project(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    web_app._auth_sessions.clear()
    owner = _signup("owner@depaul.edu")
    _project(db_path, owner)
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO project_matches (id, project_id, name) VALUES (10, 1, 'Keep Me')")
    con.commit()
    con.close()
    intruder = _signup("intruder@depaul.edu")

    resp = _run(web_app.api_project_match_delete(1, 10, _FakeRequest(cookies={"session_token": intruder})))
    assert resp.status_code == 404
    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM project_matches WHERE id = 10").fetchone()[0] == 1
    con.close()
