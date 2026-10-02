"""MLA and APA download formatting.

A researcher submits a proposal in a required style. These check the rules both
styles share (Times New Roman 12, one-inch margins, double spacing, a top-right
page number), the places they differ (the surname in MLA's header, the title
page and bold title in APA), and that the reference list is formatted and
labelled per each style. The plain export is unchanged and keeps its own tests.
"""
import io

import pytest
from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.shared import Pt

import proposal_doc

PROPOSAL = {
    "title": "Workload-Aware Cost Optimization in Cloud Analytics",
    "abstract": "A study of cost variance under mixed concurrent workloads.",
    "methodology": "- Archival analysis.\n- Controlled experiments.",
}
REFS = [
    {"authors": ["John Smith", "Alice Wong", "Raj Patel", "Mei Lin"], "year": 2025,
     "title": "Artificial Intelligence in Modern Healthcare",
     "venue": "Journal of Medical Technology"},
    {"authors": ["Dana Alvarez"], "year": 2024,
     "title": "Scheduling under mixed workloads", "venue": "ACM TODS"},
]


def _doc(style):
    return Document(io.BytesIO(proposal_doc.build(PROPOSAL, "Jane Q. Researcher", REFS, style)))


def _text(doc):
    return [p.text for p in doc.paragraphs]


# ── Shared rules ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("style", ["mla", "apa"])
def test_times_new_roman_twelve_point(style):
    normal = _doc(style).styles["Normal"]
    assert normal.font.name == "Times New Roman"
    assert normal.font.size == Pt(12)


@pytest.mark.parametrize("style", ["mla", "apa"])
def test_one_inch_margins(style):
    sec = _doc(style).sections[0]
    assert sec.top_margin.inches == 1 and sec.bottom_margin.inches == 1
    assert sec.left_margin.inches == 1 and sec.right_margin.inches == 1


@pytest.mark.parametrize("style", ["mla", "apa"])
def test_double_spaced(style):
    assert _doc(style).styles["Normal"].paragraph_format.line_spacing_rule \
        == WD_LINE_SPACING.DOUBLE


@pytest.mark.parametrize("style", ["mla", "apa"])
def test_a_page_number_sits_in_the_top_right(style):
    hdr = _doc(style).sections[0].header.paragraphs[0]
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    assert hdr.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    # The PAGE field is injected as XML; it shows up in the header's raw markup.
    assert "PAGE" in hdr._p.xml


@pytest.mark.parametrize("style", ["mla", "apa"])
def test_body_paragraphs_have_a_half_inch_first_line_indent(style):
    doc = _doc(style)
    body = next(p for p in doc.paragraphs
                if p.text.startswith("A study of cost variance"))
    assert round(body.paragraph_format.first_line_indent.inches, 2) == 0.5


# ── Where the styles differ ─────────────────────────────────────────────────

def test_mla_header_carries_the_surname():
    hdr = _doc("mla").sections[0].header.paragraphs[0]
    assert "Researcher" in hdr.text


def test_apa_header_has_no_surname():
    hdr = _doc("apa").sections[0].header.paragraphs[0]
    assert hdr.text.strip() == ""        # page number only


def test_mla_title_is_regular_weight_and_centered():
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = _doc("mla")
    title_p = next(p for p in doc.paragraphs if "Workload-Aware" in p.text)
    assert title_p.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert not any(r.bold for r in title_p.runs)


def test_apa_title_is_bold_and_centered():
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = _doc("apa")
    title_p = next(p for p in doc.paragraphs if "Workload-Aware" in p.text)
    assert title_p.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert any(r.bold for r in title_p.runs)


def test_apa_puts_the_body_on_a_fresh_page_after_the_title_page():
    """A page break separates the APA title page from the content."""
    xml = "".join(p._p.xml for p in _doc("apa").paragraphs)
    assert "w:br" in xml and 'w:type="page"' in xml


# ── References ──────────────────────────────────────────────────────────────

def test_mla_labels_the_list_works_cited():
    assert "Works Cited" in _text(_doc("mla"))
    assert "References" not in _text(_doc("mla"))


def test_apa_labels_the_list_references():
    assert "References" in _text(_doc("apa"))
    assert "Works Cited" not in _text(_doc("apa"))


def test_mla_reference_inverts_the_first_author_and_quotes_the_title():
    line = next(t for t in _text(_doc("mla")) if "Scheduling under" in t)
    assert line.startswith("Alvarez, Dana.")
    assert '"Scheduling under mixed workloads."' in line


def test_mla_uses_et_al_for_more_than_three_authors():
    line = next(t for t in _text(_doc("mla")) if "Artificial Intelligence" in t)
    assert "Smith, John, et al." in line


def test_apa_reference_uses_initials_year_in_parens_and_ampersand():
    line = next(t for t in _text(_doc("apa")) if "Artificial Intelligence" in t)
    assert "Smith, J." in line
    assert "(2025)." in line
    assert "& Lin, M." in line


def test_references_are_alphabetical_by_author():
    wc = _text(_doc("mla"))
    i = wc.index("Works Cited")
    first, second = wc[i + 1], wc[i + 2]
    assert first.startswith("Alvarez") and second.startswith("Smith")


@pytest.mark.parametrize("style", ["mla", "apa"])
def test_the_reference_entries_have_a_hanging_indent(style):
    doc = _doc(style)
    label = "Works Cited" if style == "mla" else "References"
    entries = _text(doc)
    i = entries.index(label)
    entry_p = [p for p in doc.paragraphs if p.text == entries[i + 1]][0]
    assert round(entry_p.paragraph_format.left_indent.inches, 2) == 0.5
    assert round(entry_p.paragraph_format.first_line_indent.inches, 2) == -0.5


# ── Guards ──────────────────────────────────────────────────────────────────

def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError, match="unknown style"):
        proposal_doc.build(PROPOSAL, "Jane", REFS, "chicago")


def test_a_proposal_with_no_references_still_builds():
    doc = Document(io.BytesIO(proposal_doc.build(PROPOSAL, "Jane", [], "mla")))
    assert "Works Cited" not in _text(doc)        # no empty heading
    assert any("Workload-Aware" in t for t in _text(doc))


def test_every_written_section_reaches_the_document():
    doc = _doc("apa")
    joined = "\n".join(_text(doc))
    assert "Abstract" in joined and "cost variance" in joined
    assert "Research Design and Methodology" in joined
