import io

import pytest
from docx import Document
from fpdf import FPDF

from doc_extract import extract_text


def _make_docx_bytes(paragraphs):
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_pdf_bytes(text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=14)
    pdf.multi_cell(0, 10, text)
    return bytes(pdf.output())


def _make_empty_pdf_bytes():
    pdf = FPDF()
    pdf.add_page()
    return bytes(pdf.output())


def test_extract_text_from_docx():
    content = _make_docx_bytes([
        "My research focuses on natural language processing.",
        "I study low-resource languages.",
    ])
    result = extract_text("statement.docx", content)
    assert "natural language processing" in result
    assert "low-resource languages" in result


def test_extract_text_from_pdf():
    content = _make_pdf_bytes("My research focuses on computer vision for medical imaging.")
    result = extract_text("statement.pdf", content)
    assert "computer vision" in result


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text("notes.txt", b"hello")


def test_extract_text_raises_on_empty_pdf():
    content = _make_empty_pdf_bytes()
    with pytest.raises(ValueError, match="No readable text found"):
        extract_text("blank.pdf", content)


def test_extract_text_raises_on_corrupted_file():
    with pytest.raises(ValueError, match="Couldn't read that file"):
        extract_text("broken.pdf", b"not a real pdf")
