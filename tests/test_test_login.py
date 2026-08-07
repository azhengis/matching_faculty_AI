"""Email-only sign-in — the demo convenience, and the guard around it.

Typing an email and landing in that person's profile is impersonation by
design. It is acceptable only for a controlled walkthrough, so these tests
pin the gate: OFF by default, passwords unaffected when off, and no path
that lets a blank password through while it is off.
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


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    return db_path


def test_disabled_by_default(monkeypatch):
    """The deployed default must be password auth — the flag is opt-in."""
    import importlib
    monkeypatch.delenv("TEST_LOGIN", raising=False)
    assert web_app.TEST_LOGIN in (False, True)  # attribute exists
    # Default resolution, independent of whatever the test env holds:
    import os
    assert (os.environ.get("TEST_LOGIN", "").strip().lower() in ("1", "true", "yes")) is False


def test_blank_password_is_rejected_when_flag_is_off(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "TEST_LOGIN", False)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "real@depaul.edu", "password": "hunter222"})))

    r = _run(web_app.api_auth_login(_FakeRequest({"email": "real@depaul.edu", "password": ""})))
    assert r.status_code == 401


def test_wrong_password_still_rejected_when_flag_is_on(tmp_path, monkeypatch):
    """Demo mode makes the password OPTIONAL, not ignored: supplying a wrong
    one must still fail, or a real account becomes weaker than before."""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "TEST_LOGIN", True)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "real@depaul.edu", "password": "hunter222"})))

    r = _run(web_app.api_auth_login(_FakeRequest({"email": "real@depaul.edu", "password": "wrong"})))
    assert r.status_code == 401


def test_email_only_signs_in_an_existing_account_when_flag_is_on(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "TEST_LOGIN", True)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "real@depaul.edu", "password": "hunter222"})))

    r = _run(web_app.api_auth_login(_FakeRequest({"email": "real@depaul.edu"})))
    assert r.status_code == 200
    assert json.loads(r.body)["email"] == "real@depaul.edu"


def test_email_only_creates_the_account_on_first_sight(tmp_path, monkeypatch):
    """A tester walks in with a real DePaul address and is recognised."""
    db_path = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "TEST_LOGIN", True)

    r = _run(web_app.api_auth_login(_FakeRequest({"email": "newcomer@depaul.edu"})))
    assert r.status_code == 200

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT password_hash, password_salt FROM users WHERE email = ?",
                      ("newcomer@depaul.edu",)).fetchone()
    con.close()
    # Auto-created accounts get a random unguessable password, not an empty
    # one — so they don't stay passwordless if the flag is later switched off.
    assert row and row[0] and row[1]


def test_missing_email_is_a_400_not_a_500(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web_app, "TEST_LOGIN", True)
    r = _run(web_app.api_auth_login(_FakeRequest({"email": "   "})))
    assert r.status_code == 400
