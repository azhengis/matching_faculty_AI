"""Render a proposal as an MLA- or APA-formatted .docx.

The plain download in web_app stays as it was. This adds the two academic
styles a researcher is actually asked to submit in, applying the real rules:
Times New Roman 12, one-inch margins, double spacing throughout, a half-inch
first-line indent, page numbers in the top-right (with the author's surname for
MLA), the title set the way each style wants it, and a separate Works Cited /
References page with hanging indents.

The output is .docx rather than PDF on purpose: it carries every one of those
rules, Word opens it, and "Save as PDF" is one step — whereas a server-side PDF
renderer with running headers is a heavy native dependency on a container that
is already tight on memory. The references are generated from what the
literature search returned and are explicitly best-effort: author-name order
and missing volume/issue/page detail are exactly what a submitter must check.
"""
from __future__ import annotations

import datetime as _dt
import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

# Section order and headings, same spine as the plain export.
_SECTIONS = [
    ("Abstract", "abstract"),
    ("Keywords", "keywords"),
    ("Problem Statement", "problem_statement"),
    ("Novelty and Contribution", "novelty"),
    ("Introduction", "background"),
    ("Research Objectives", "objectives"),
    ("Research Questions", "research_questions"),
    ("Hypotheses", "hypotheses"),
    ("Assumptions and Delimitations", "assumptions_delimitations"),
    ("Literature Review", "related_work"),
    ("Research Design and Methodology", "methodology"),
    ("The Role of AI and Data Science", "ai_role"),
    ("Ethical Considerations", "ethical_considerations"),
    ("Expected Outcomes", "expected_outcomes"),
    ("Plan of Work", "plan_of_work"),
    ("Conclusions and Future Work", "conclusions_future_work"),
]

_FONT = "Times New Roman"


def _page_number_field(paragraph) -> None:
    """Insert a live {PAGE} field — a real page number Word updates itself."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.append(begin); run._r.append(instr); run._r.append(end)


def _base_document(last_name: str, style: str) -> Document:
    """A document with the shared rules both styles require."""
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = _FONT
    normal.font.size = Pt(12)
    pf = normal.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)

    for section in doc.sections:
        section.top_margin = section.bottom_margin = Inches(1)
        section.left_margin = section.right_margin = Inches(1)

        # Page number, top-right. MLA prefixes the author's surname; APA does not.
        header = section.header.paragraphs[0]
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        if style == "mla" and last_name:
            header.add_run(f"{last_name} ")
        _page_number_field(header)
        for run in header.runs:
            run.font.name = _FONT
            run.font.size = Pt(12)

    return doc


def _body_paragraph(doc, text: str, *, indent: bool = True):
    p = doc.add_paragraph(text)
    if indent:
        p.paragraph_format.first_line_indent = Inches(0.5)
    return p


def _heading(doc, text: str):
    """Section heading: bold, left, in the body font — not Word's Heading style,
    which would substitute its own typeface and colour and break the rules."""
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = True
    p.paragraph_format.first_line_indent = Inches(0)
    return p


def _centered(doc, text: str, *, bold: bool = False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.first_line_indent = Inches(0)
    run = p.add_run(text)
    run.bold = bold
    return p


def _surname(full_name: str) -> str:
    full_name = (full_name or "").strip()
    if not full_name:
        return ""
    # "Jane Q. Doe" -> "Doe". Names are messy; this is the submitter's to check.
    return full_name.split()[-1]


def _front_matter(doc, title: str, researcher_name: str, affiliation: str,
                  style: str) -> None:
    """A professor's research-proposal title block: title, author, affiliation,
    date — centered, with no instructor or course. The student heading block is
    deliberately not used; this is a proposal, not a class paper."""
    today = _dt.date.today().strftime("%d %B %Y") if style == "mla" \
        else _dt.date.today().strftime("%B %d, %Y")
    heading = (title or "Research Proposal")
    if style == "mla" and "proposal" not in heading.lower():
        heading = f"{heading}: A Research Proposal"

    if style == "mla":
        # MLA: the identifying block is LEFT-aligned at the top of the first
        # page; only the title is centered, above the body. No title page.
        if researcher_name:
            _body_paragraph(doc, researcher_name, indent=False)
        if affiliation:
            _body_paragraph(doc, affiliation, indent=False)
        _body_paragraph(doc, today, indent=False)
        _centered(doc, heading, bold=False)
    else:
        # APA: a centered title page, title in bold, then a page break.
        for _ in range(3):
            doc.add_paragraph()
        _centered(doc, heading, bold=True)
        doc.add_paragraph()
        if researcher_name:
            _centered(doc, researcher_name)
        if affiliation:
            _centered(doc, affiliation)
        _centered(doc, today)
        doc.add_page_break()


def _author_mla(authors: list) -> str:
    authors = [a for a in (authors or []) if a and a.strip()]
    if not authors:
        return ""
    def inv(a):  # "First Last" -> "Last, First"
        parts = a.split()
        return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else a
    if len(authors) == 1:
        return inv(authors[0]) + "."
    if len(authors) == 2:
        return f"{inv(authors[0])}, and {authors[1]}."
    return f"{inv(authors[0])}, et al."


def _author_apa(authors: list) -> str:
    authors = [a for a in (authors or []) if a and a.strip()]
    if not authors:
        return ""
    def inv(a):  # "First Middle Last" -> "Last, F. M."
        parts = a.split()
        if len(parts) < 2:
            return a
        initials = " ".join(f"{p[0]}." for p in parts[:-1] if p)
        return f"{parts[-1]}, {initials}"
    inverted = [inv(a) for a in authors]
    if len(inverted) == 1:
        return inverted[0]
    return ", ".join(inverted[:-1]) + ", & " + inverted[-1]


def _reference_mla(r: dict) -> str:
    who = _author_mla(r.get("authors"))
    title = (r.get("title") or "").strip().rstrip(".")
    venue = (r.get("venue") or "").strip()
    year = r.get("year")
    out = []
    if who:
        out.append(who)
    if title:
        out.append(f'"{title}."')
    tail = []
    if venue:
        tail.append(venue)
    if year:
        tail.append(str(year))
    if tail:
        out.append(", ".join(tail) + ".")
    return " ".join(out).strip()


def _reference_apa(r: dict) -> str:
    who = _author_apa(r.get("authors"))
    title = (r.get("title") or "").strip().rstrip(".")
    venue = (r.get("venue") or "").strip()
    year = r.get("year")
    out = []
    if who:
        out.append(who)
    out.append(f"({year}).") if year else out.append("(n.d.).")
    if title:
        out.append(f"{title}.")
    if venue:
        out.append(f"{venue}.")
    return " ".join(out).strip()


def _references_page(doc, references: list, style: str) -> None:
    refs = [r for r in (references or []) if (r.get("title") or "").strip()]
    if not refs:
        return
    refs.sort(key=lambda r: ((r.get("authors") or [""])[0].lower(),
                             (r.get("title") or "").lower()))
    doc.add_page_break()
    _centered(doc, "Works Cited" if style == "mla" else "References")
    fmt = _reference_mla if style == "mla" else _reference_apa
    for r in refs:
        p = doc.add_paragraph(fmt(r))
        # Hanging indent: both styles.
        p.paragraph_format.left_indent = Inches(0.5)
        p.paragraph_format.first_line_indent = Inches(-0.5)


def build(proposal: dict, researcher_name: str, references: list | None,
          style: str, affiliation: str = "") -> bytes:
    """Render the proposal in 'mla' or 'apa' style. Returns .docx bytes."""
    style = (style or "").lower()
    if style not in ("mla", "apa"):
        raise ValueError(f"unknown style {style!r}; expected 'mla' or 'apa'")

    title = (proposal.get("title") or "").strip()
    doc = _base_document(_surname(researcher_name), style)
    _front_matter(doc, title, researcher_name, affiliation, style)

    for heading, key in _SECTIONS:
        text = (proposal.get(key) or "").strip()
        if not text:
            continue
        _heading(doc, heading)
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("- ") or line.startswith("• "):
                p = doc.add_paragraph(line[2:].strip(), style="List Bullet")
                for run in p.runs:
                    run.font.name = _FONT
                    run.font.size = Pt(12)
            else:
                _body_paragraph(doc, line)

    _references_page(doc, references or [], style)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
