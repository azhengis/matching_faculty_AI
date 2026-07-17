import asyncio
import json
import sqlite3

import pytest

import web_app


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


@pytest.fixture(autouse=True)
def _clear_sessions():
    web_app._auth_sessions.clear()
    yield
    web_app._auth_sessions.clear()


def _init_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    return db_path


def test_signup_creates_user_and_sets_session_cookie(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_auth_signup(_FakeRequest({"email": "Jane@Depaul.edu", "password": "hunter222"})))
    assert response.status_code == 200
    assert _body(response) == {"email": "jane@depaul.edu"}
    assert "session_token=" in response.headers.get("set-cookie", "")
    assert len(web_app._auth_sessions) == 1

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT email FROM users").fetchone()
    con.close()
    assert row[0] == "jane@depaul.edu"


def test_signup_rejects_duplicate_email(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))

    response = _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "different1"})))
    assert response.status_code == 400
    assert "already exists" in _body(response)["error"]


def test_signup_rejects_short_password(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "short"})))
    assert response.status_code == 400
    assert "8 characters" in _body(response)["error"]


def test_login_succeeds_with_correct_credentials(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    web_app._auth_sessions.clear()  # simulate a fresh session, e.g. after logout

    response = _run(web_app.api_auth_login(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    assert response.status_code == 200
    assert _body(response) == {"email": "jane@depaul.edu"}
    assert len(web_app._auth_sessions) == 1


def test_login_fails_with_wrong_password(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))

    response = _run(web_app.api_auth_login(_FakeRequest({"email": "jane@depaul.edu", "password": "wrongpass"})))
    assert response.status_code == 401
    assert _body(response)["error"] == "Incorrect email or password."


def test_login_fails_with_unknown_email(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_auth_login(_FakeRequest({"email": "nobody@depaul.edu", "password": "whatever1"})))
    assert response.status_code == 401
    assert _body(response)["error"] == "Incorrect email or password."


def test_logout_clears_session(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[0]

    response = _run(web_app.api_auth_logout(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert token not in web_app._auth_sessions


def test_me_returns_current_user_when_logged_in(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[0]

    response = _run(web_app.api_auth_me(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert _body(response)["email"] == "jane@depaul.edu"


def test_me_returns_401_when_not_logged_in(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)

    response = _run(web_app.api_auth_me(_FakeRequest(cookies={})))
    assert response.status_code == 401
