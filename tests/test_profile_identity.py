"""Name, contact email, and photo: the fields you type rather than describe.

A signed-in address that isn't in the directory used to become a name:
azhengis@depaul.edu turned into "Azhengis", which reads as the system knowing
you and getting it wrong. It is blank now, and these endpoints are how it gets
filled. The security line worth pinning: changing your contact email must not
change the account you sign in with, and must not re-link the faculty record,
or it becomes a way to claim someone else's publications.
"""
import asyncio
import json
import os
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


class _FakeUpload:
    def __init__(self, filename, content):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


def _setup(tmp_path, monkeypatch, account_email="new.person@depaul.edu"):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(web_app, "UPLOADS_DIR", str(uploads))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "research_summary TEXT, title TEXT, department TEXT)")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.commit(); con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": account_email, "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    return db_path, {"session_token": token}, str(uploads)


def test_unknown_email_leaves_the_name_blank(tmp_path, monkeypatch):
    """No more guessing a person's name out of their address."""
    db_path, cookies, _ = _setup(tmp_path, monkeypatch, "azhengis@depaul.edu")
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))

    con = sqlite3.connect(db_path)
    name = con.execute("SELECT name FROM profiles").fetchone()[0]
    con.close()
    assert name == ""


def test_identity_sets_name_and_contact_email(tmp_path, monkeypatch):
    db_path, cookies, _ = _setup(tmp_path, monkeypatch)
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))

    r = _run(web_app.api_profile_identity(_FakeRequest(
        {"name": "Aruzhan Zhengis", "email": "contact@depaul.edu"}, cookies)))
    assert r.status_code == 200

    me = json.loads(_run(web_app.api_profile_me(_FakeRequest(cookies=cookies))).body)
    assert me["name"] == "Aruzhan Zhengis"
    assert me["email"] == "contact@depaul.edu"


def test_changing_contact_email_does_not_move_the_sign_in_account(tmp_path, monkeypatch):
    """The account keeps its own address, so this cannot be used to take over
    another login, and the faculty link is untouched."""
    db_path, cookies, _ = _setup(tmp_path, monkeypatch, "real@depaul.edu")
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    _run(web_app.api_profile_identity(_FakeRequest(
        {"name": "Someone", "email": "someone.else@depaul.edu"}, cookies)))

    con = sqlite3.connect(db_path)
    account = con.execute("SELECT email FROM users").fetchone()[0]
    fid = con.execute("SELECT faculty_id FROM profiles").fetchone()[0]
    con.close()
    assert account == "real@depaul.edu"
    assert fid is None


def test_blank_name_is_rejected(tmp_path, monkeypatch):
    _, cookies, _ = _setup(tmp_path, monkeypatch)
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    r = _run(web_app.api_profile_identity(_FakeRequest({"name": "   "}, cookies)))
    assert r.status_code == 400


def test_photo_upload_rejects_non_images(tmp_path, monkeypatch):
    _, cookies, _ = _setup(tmp_path, monkeypatch)
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    r = _run(web_app.api_profile_photo_upload(
        _FakeRequest(cookies=cookies), file=_FakeUpload("payload.svg", b"<svg onload=alert(1)>")))
    assert r.status_code == 400


def test_photo_upload_ignores_the_supplied_filename(tmp_path, monkeypatch):
    """A traversal filename must never reach the filesystem: the stored name is
    generated, and only the extension is taken from the upload."""
    db_path, cookies, uploads = _setup(tmp_path, monkeypatch)
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    r = _run(web_app.api_profile_photo_upload(
        _FakeRequest(cookies=cookies),
        file=_FakeUpload("../../../../etc/passwd.png", b"\x89PNG fake")))
    assert r.status_code == 200

    con = sqlite3.connect(db_path)
    stored = con.execute("SELECT photo_file FROM profiles").fetchone()[0]
    con.close()
    assert stored and "/" not in stored and ".." not in stored
    assert os.path.exists(os.path.join(uploads, stored))


def test_replacing_a_photo_deletes_the_old_file(tmp_path, monkeypatch):
    db_path, cookies, uploads = _setup(tmp_path, monkeypatch)
    _run(web_app.api_profile_automatch(_FakeRequest(cookies=cookies)))
    _run(web_app.api_profile_photo_upload(
        _FakeRequest(cookies=cookies), file=_FakeUpload("a.png", b"first")))
    con = sqlite3.connect(db_path)
    first = con.execute("SELECT photo_file FROM profiles").fetchone()[0]
    con.close()

    _run(web_app.api_profile_photo_upload(
        _FakeRequest(cookies=cookies), file=_FakeUpload("b.png", b"second")))
    assert not os.path.exists(os.path.join(uploads, first))


def test_endpoints_require_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert _run(web_app.api_profile_identity(_FakeRequest({"name": "X"}))).status_code == 401
    assert _run(web_app.api_profile_photo(_FakeRequest())).status_code == 401
