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


def is_blank(text):
    """True when text has no visible characters.

    A plain .strip() is not enough: zero-width spaces, the BOM, and
    non-breaking spaces all survive it, and 27 scraped BIO sections are made of
    nothing else. Such a section is a truthy string, so it wins any `a or b`
    fallback and then gets cleaned away later, leaving the person with nothing.
    """
    return not _INVISIBLE.sub(" ", text or "").strip()


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
    if is_blank(text):
        return True
    stripped = _INVISIBLE.sub(" ", text).strip()
    return len(stripped) < 25 and stripped[0].islower()


# A heading a DePaul page uses to introduce a list of topics. The scrape keeps
# it inline, so the list below it reads as part of the bio prose.
_INTERESTS_HEADING = re.compile(
    r"^[ \t\xa0]*(?:Research(?:\s+and)?\s+Interests?|Research\s+Areas?|Areas?\s+of\s+"
    r"(?:Research|Expertise|Interest)|Specialt(?:y|ies)|Expertise)[ \t\xa0]*:?[ \t\xa0]*$",
    re.IGNORECASE | re.MULTILINE,
)

# Headings that introduce something that is NOT a research topic. A list under
# one of these is degrees or course titles, and must not become interests.
_OTHER_HEADING = re.compile(
    r"^[ \t\xa0]*(?:Education|Degrees?|Courses?(?:\s+(?:Taught|Offered))?|Teaching|"
    r"Publications?|Awards?|Professional\s+Organizations?|Contact|"
    r"Representative\s+Sample\s+of.*)[ \t\xa0]*:?[ \t\xa0]*$",
    re.IGNORECASE | re.MULTILINE,
)

_MAX_INTEREST_LEN = 70
_MAX_INTEREST_WORDS = 8
# Verbs and determiners a sentence needs but a topic label does not.
_PROSE_WORDS = re.compile(r"\b(?:is|are|was|were|has|have|had|the|his|her|their|its|he|she|they)\b",
                          re.IGNORECASE)


def _is_topic_line(line):
    """True when a line reads as a topic label rather than a sentence."""
    line = line.strip()
    if not line or len(line) > _MAX_INTEREST_LEN or len(line.split()) > _MAX_INTEREST_WORDS:
        return False
    if line.rstrip().endswith((".", "!", "?", ";", ":")):
        return False
    if _PROSE_WORDS.search(line):
        return False
    if _OTHER_HEADING.match(line):
        return False
    # A URL, handle, or bare year is a stray page artefact, not an interest.
    if re.match(r"^(?:https?://|www\.|@)", line, re.IGNORECASE) or re.fullmatch(r"[\d\s\-—]+", line):
        return False
    return True


def split_interests(summary):
    """Pull a structured topic list out of a scraped summary.

    DePaul bio pages often carry the research areas as their own short list:

        Lee earned a Ph.D. in Sociology from UT Austin in 2023.
        Research and Interests:
        Culture
        Politics
        Race and Ethnicity

    The scrape flattens that into one blob, so the topics end up buried in the
    bio while the profile's research-interests field sits empty. This returns
    ``(interests, remaining_prose)`` so the topics can go in the box they
    belong in and the bio keeps only what is actually prose.

    DELIBERATELY CONSERVATIVE. Two shapes are extracted and no others:

      A list under an explicit interests heading.

      A summary that is a topic list from top to bottom, which is what a page
      with no prose bio produces ("Computing Accessibility / Human Computer
      Interaction").

    Prose with a few short lines scattered through it is NOT mined, because
    that pattern produces junk: one such record yields "Giving Voice",
    "Backstage", and "www.Adlerimprov.com" — a play, a magazine, and a website.
    Returns ``([], summary)`` whenever nothing is confidently a list.
    """
    if not summary or not summary.strip():
        return [], summary or ""

    text = summary.replace("\r\n", "\n").replace("\xa0", " ")
    lines = text.split("\n")

    heading = _INTERESTS_HEADING.search(text)
    if heading:
        head_idx = text[:heading.start()].count("\n")
        before = lines[:head_idx]
        after = lines[head_idx + 1:]
        items, tail = [], []
        for i, raw in enumerate(after):
            if not raw.strip():
                continue
            if _OTHER_HEADING.match(raw) or not _is_topic_line(raw):
                tail = after[i:]
                break
            items.append(raw.strip())
        if items:
            prose = "\n".join(before + tail)
            return _dedupe(items), re.sub(r"\n{3,}", "\n\n", prose).strip()
        return [], summary

    # No heading: only treat it as a list when EVERY line is a topic.
    present = [ln.strip() for ln in lines if ln.strip()]
    if len(present) >= 2 and all(_is_topic_line(ln) for ln in present):
        return _dedupe(present), ""
    return [], summary


def _dedupe(items):
    """Order-preserving dedupe, case-insensitive, capped."""
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:20]


def has_site_chrome(text):
    """True when any known furniture is still present. Used by tests and by the
    repair script's reporting."""
    if not text:
        return False
    normalized = text.replace("\r\n", "\n")
    return any(p.search(normalized) for p in _CHROME)
