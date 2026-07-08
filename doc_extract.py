"""
doc_extract.py
--------------
Pure text-extraction helpers for the profile document-upload feature.
Supports .pdf and .docx only. Every failure mode raises ValueError with
a message meant to be shown directly to the faculty member uploading
the file.
"""
import io


def extract_pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def extract_docx_text(content: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(content))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text(filename: str, content: bytes) -> str:
    """Extract readable text from a .pdf or .docx file's raw bytes.

    Raises ValueError with a user-facing message if the extension is
    unsupported, the file can't be parsed, or no text is found.
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        extractor = extract_pdf_text
    elif name.endswith(".docx"):
        extractor = extract_docx_text
    else:
        raise ValueError("Unsupported file type. Please upload a .pdf or .docx file.")

    try:
        text = extractor(content)
    except ValueError:
        raise
    except Exception:
        raise ValueError(
            "Couldn't read that file. It may be corrupted — try a different "
            "file or 'Type it out' instead."
        )

    if not text.strip():
        raise ValueError("No readable text found in that file. Try 'Type it out' instead.")

    return text
