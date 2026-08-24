"""Strip DePaul site furniture out of scraped text.

The bio scrape takes a page container that includes the site footer, so the
university's address block ended up inside 729 of 1,389 research summaries and
69 course lists. It is not merely ugly: it is identical text on more than half
the corpus, so it lands in the SPECTER2 embedding as a constant that every
polluted record shares, pulling unrelated faculty toward each other.

EXCISION, NOT TRUNCATION. The obvious fix — delete everything from "DePaul
University" onward — is wrong. On 45 records the footer sits in the MIDDLE with
real research text after it:

    Computing Accessibility
    DePaul University / 1 E. Jackson Blvd. / ... / (outside Illinois)
    Human Computer Interaction

Truncating there silently drops "Human Computer Interaction" — the half of that
person's research areas the scrape got right. So the blocks are cut out and the
surrounding text is rejoined.

Kept dependency-free so both search.py and the numbered pipeline stages can
import it. Those stages start with a digit and cannot import each other, which
is why this lives at the repo root rather than in pipeline/.
"""
import re

# The address block that closes every depaul.edu page. Tolerant about spacing
# and punctuation because the scrape preserved whatever the page had, including
# \r\n line endings and non-breaking spaces (\s matches \xa0 on str patterns).
_FOOTER_ADDRESS = re.compile(
    r"[ \t\xa0]*DePaul\s+University[ \t\xa0]*\r?\n"
    r"[ \t\xa0]*1\s*E\.?\s*Jackson\s*Blvd\.?[ \t\xa0]*\r?\n"
    r"[ \t\xa0]*Chicago,?\s*IL\s*60604[ \t\xa0]*\r?\n"
    r"[ \t\xa0]*\(?312\)?\s*362[-.\s]?8000[ \t\xa0]*\r?\n"
    r"[ \t\xa0]*-?\s*or\s*-?\s*1\s*\(?800\)?\s*4DE\s*PAUL\s*\(\s*outside\s+Illinois\s*\)",
    re.IGNORECASE,
)

# The "Information for" audience menu that sits beside the address on some
# templates. Each entry is optional so a partial capture is still removed.
_FOOTER_NAV = re.compile(
    r"[ \t\xa0]*Information\s+for[ \t\xa0]*\r?\n"
    r"(?:[ \t\xa0]*(?:Current\s+Students|Visitors|Faculty\s+and\s+Staff|Alumni|"
    r"Parents\s+and\s+Families)[ \t\xa0]*\r?\n?)+",
    re.IGNORECASE,
)

_CHROME = (_FOOTER_ADDRESS, _FOOTER_NAV)


def strip_site_chrome(text):
    """Remove DePaul page furniture, rejoining the real text around it.

    Returns the input unchanged when there is nothing to strip, so it is safe
    to run over the whole corpus repeatedly.
    """
    if not text:
        return text

    cleaned = text.replace("\r\n", "\n")
    for pattern in _CHROME:
        cleaned = pattern.sub("\n", cleaned)

    # Excision leaves the blank lines that surrounded the block. Collapse them
    # so "areas\n\n\n\nmore areas" reads as two paragraphs, not a gap.
    cleaned = re.sub(r"[ \t\xa0]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Zero-width spaces, the BOM, and non-breaking spaces all survive a .strip().
# Several scraped summaries are nothing but these.
_INVISIBLE = re.compile(r"[​‌‍⁠﻿\xa0\s]+")


def is_junk_summary(text):
    """True when a research summary carries no research content at all.

    This replaces a blanket "shorter than 25 characters is useless" rule. That
    rule was fair when short summaries were reliably scraper debris, but
    excising the site footer left the real short ones exposed: "Screenwriting",
    "Irish History", "Computer Science", "Cinema Production" are complete
    answers to what somebody researches, and the length rule deleted all of
    them the moment the footer padding came off.

    Two things are junk whatever their length:

      Text that is only invisible characters. Eleven summaries are a run of
      zero-width spaces and nothing else.

      Sentence fragments the scraper tore out of the middle of a paragraph.
      They give themselves away by starting mid-sentence: "and", "her other",
      "is mathematics". A genuine research area is a noun phrase, so it is
      capitalised. The length bound stays on this branch only, because a long
      lowercase summary is usually real prose, and fix_summary already repairs
      those by prefixing "Research interests".
    """
    if not text:
        return False                      # empty already; nothing to clear
    stripped = _INVISIBLE.sub(" ", text).strip()
    if not stripped:
        return True
    return len(stripped) < 25 and stripped[0].islower()


def has_site_chrome(text):
    """True when any known furniture is still present. Used by tests and by the
    repair script's reporting."""
    if not text:
        return False
    normalized = text.replace("\r\n", "\n")
    return any(p.search(normalized) for p in _CHROME)
