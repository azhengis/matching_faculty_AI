import asyncio
import io
import json

from docx import Document
from fastapi import UploadFile
from fpdf import FPDF

from web_app import api_profile_extract_file


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _body(response):
    return json.loads(response.body)


def test_extract_file_endpoint_returns_text_for_valid_docx():
    doc = Document()
    doc.add_paragraph("My research focuses on reinforcement learning for robotics.")
    buf = io.BytesIO()
    doc.save(buf)
    upload = UploadFile(file=io.BytesIO(buf.getvalue()), filename="statement.docx")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 200
    assert "reinforcement learning" in _body(response)["text"]


def test_extract_file_endpoint_rejects_bad_extension():
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="notes.txt")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 400
    assert "error" in _body(response)


def test_extract_file_endpoint_rejects_oversized_file():
    big_content = b"x" * (10 * 1024 * 1024 + 1)
    upload = UploadFile(file=io.BytesIO(big_content), filename="statement.pdf")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 400
    assert "error" in _body(response)


def test_extract_file_endpoint_returns_422_for_empty_pdf():
    pdf = FPDF()
    pdf.add_page()
    upload = UploadFile(file=io.BytesIO(bytes(pdf.output())), filename="blank.pdf")

    response = _run(api_profile_extract_file(upload))

    assert response.status_code == 422
    assert "error" in _body(response)
