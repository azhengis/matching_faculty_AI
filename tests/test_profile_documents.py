import asyncio
import io
import json
import os
import sqlite3

from docx import Document
from fastapi import UploadFile

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


def _init_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(web_app, "UPLOADS_DIR", str(uploads_dir))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    return db_path, uploads_dir


def _signup_and_save_profile(email="jane@depaul.edu"):
    _run(web_app.api_auth_signup(_FakeRequest({"email": email, "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    _run(web_app.api_profile_save(_FakeRequest(
        {"name": "Jane Doe", "bio_text": "bio", "project_description": "proj",
         "confirmed_paper_ids": [], "research_interests": []},
        cookies={"session_token": token}
    )))
    return token


def test_add_link_requires_login(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    response = _run(web_app.api_profile_add_link(
        _FakeRequest({"label": "Site", "url": "https://x.com"}, cookies={})
    ))
    assert response.status_code == 401


def test_add_link_requires_existing_profile(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    _run(web_app.api_auth_signup(_FakeRequest({"email": "jane@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]

    response = _run(web_app.api_profile_add_link(
        _FakeRequest({"label": "Site", "url": "https://x.com"}, cookies={"session_token": token})
    ))
    assert response.status_code == 400


def test_add_link_creates_row(tmp_path, monkeypatch):
    db_path, _ = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    response = _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "Google Scholar", "url": "https://scholar.google.com/x"},
        cookies={"session_token": token}
    )))
    assert response.status_code == 200
    body = _body(response)
    assert body["kind"] == "link"
    assert body["label"] == "Google Scholar"

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT kind, label, url FROM profile_documents").fetchone()
    con.close()
    assert row == ("link", "Google Scholar", "https://scholar.google.com/x")


def test_add_document_extracts_docx_text_and_stores_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    doc = Document()
    doc.add_paragraph("My CV highlights reinforcement learning research.")
    buf = io.BytesIO()
    doc.save(buf)
    upload = UploadFile(file=io.BytesIO(buf.getvalue()), filename="cv.docx")

    response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="My CV"
    ))
    assert response.status_code == 200
    body = _body(response)
    assert body["has_text"] is True

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT label, filename, stored_filename, extracted_text FROM profile_documents"
    ).fetchone()
    con.close()
    assert row[0] == "My CV"
    assert row[1] == "cv.docx"
    assert "reinforcement learning" in row[3]
    assert os.path.exists(os.path.join(uploads_dir, row[2]))


def test_add_document_without_extractable_text_still_stores_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    upload = UploadFile(file=io.BytesIO(b"just some plain text"), filename="notes.txt")

    response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label=""
    ))
    assert response.status_code == 200
    body = _body(response)
    assert body["has_text"] is False
    assert body["label"] == "notes.txt"  # falls back to filename when no label given

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT stored_filename FROM profile_documents").fetchone()
    con.close()
    assert os.path.exists(os.path.join(uploads_dir, row[0]))


def test_add_document_with_corrupted_pdf_still_stores_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()

    upload = UploadFile(file=io.BytesIO(b"not a real pdf"), filename="broken.pdf")

    response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label=""
    ))
    assert response.status_code == 200
    body = _body(response)
    assert body["has_text"] is False

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT stored_filename, extracted_text FROM profile_documents"
    ).fetchone()
    con.close()
    assert row[1] is None
    assert os.path.exists(os.path.join(uploads_dir, row[0]))


def test_list_documents_returns_both_kinds(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "Site", "url": "https://x.com"}, cookies={"session_token": token}
    )))
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")
    _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))

    response = _run(web_app.api_profile_list_documents(_FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    kinds = sorted(d["kind"] for d in _body(response)["documents"])
    assert kinds == ["file", "link"]


def test_delete_document_removes_row_and_file(tmp_path, monkeypatch):
    db_path, uploads_dir = _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")
    add_response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))
    doc_id = _body(add_response)["id"]

    con = sqlite3.connect(db_path)
    stored_filename = con.execute(
        "SELECT stored_filename FROM profile_documents WHERE id = ?", (doc_id,)
    ).fetchone()[0]
    con.close()
    assert os.path.exists(os.path.join(uploads_dir, stored_filename))

    response = _run(web_app.api_profile_delete_document(doc_id, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert not os.path.exists(os.path.join(uploads_dir, stored_filename))

    con = sqlite3.connect(db_path)
    count = con.execute("SELECT COUNT(*) FROM profile_documents").fetchone()[0]
    con.close()
    assert count == 0


def test_delete_document_requires_ownership(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token_a = _signup_and_save_profile(email="a@depaul.edu")
    _run(web_app.api_auth_signup(_FakeRequest({"email": "b@depaul.edu", "password": "hunter222"})))
    token_b = list(web_app._auth_sessions.keys())[-1]
    _run(web_app.api_profile_save(_FakeRequest(
        {"name": "B", "bio_text": "", "project_description": "",
         "confirmed_paper_ids": [], "research_interests": []},
        cookies={"session_token": token_b}
    )))

    add_response = _run(web_app.api_profile_add_link(_FakeRequest(
        {"label": "A's site", "url": "https://a.com"}, cookies={"session_token": token_a}
    )))
    doc_id = _body(add_response)["id"]

    response = _run(web_app.api_profile_delete_document(doc_id, _FakeRequest(cookies={"session_token": token_b})))
    assert response.status_code == 404


def test_get_document_file_serves_only_owned_files(tmp_path, monkeypatch):
    _init_db(tmp_path, monkeypatch)
    token = _signup_and_save_profile()
    upload = UploadFile(file=io.BytesIO(b"hello world"), filename="notes.txt")
    add_response = _run(web_app.api_profile_add_document(
        req=_FakeRequest(cookies={"session_token": token}), file=upload, label="Notes"
    ))
    doc_id = _body(add_response)["id"]

    response = _run(web_app.api_profile_document_file(doc_id, _FakeRequest(cookies={"session_token": token})))
    assert response.status_code == 200
    assert response.path.endswith(".txt")

    response_401 = _run(web_app.api_profile_document_file(doc_id, _FakeRequest(cookies={})))
    assert response_401.status_code == 401

    _run(web_app.api_auth_signup(_FakeRequest({"email": "other@depaul.edu", "password": "hunter222"})))
    token_b = list(web_app._auth_sessions.keys())[-1]
    _run(web_app.api_profile_save(_FakeRequest(
        {"name": "Other User", "bio_text": "", "project_description": "",
         "confirmed_paper_ids": [], "research_interests": []},
        cookies={"session_token": token_b}
    )))

    response_cross_user = _run(web_app.api_profile_document_file(
        doc_id, _FakeRequest(cookies={"session_token": token_b})
    ))
    assert response_cross_user.status_code == 404
