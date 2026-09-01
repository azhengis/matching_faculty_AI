"""Stripping DePaul site furniture out of scraped text.

The bio scrape took a page container that included the site footer, so the
university's address block sat inside 729 of 1,389 research summaries. One
faculty member's entire "research summary" read:

    Computing Accessibility
    DePaul University / 1 E. Jackson Blvd. / Chicago, IL 60604 / ...
    Human Computer Interaction
"""
import pytest

from text_clean import strip_site_chrome, has_site_chrome, is_junk_summary

FOOTER = ("DePaul University\n1 E. Jackson Blvd.\nChicago, IL 60604\n"
          "(312) 362-8000\n-or- 1 (800) 4DE PAUL (outside Illinois)")
NAV = ("Information for\nCurrent Students\nVisitors\nFaculty and Staff\n"
       "Alumni\nParents and Families")


# ── Excision, not truncation ─────────────────────────────────────────────────

def test_research_after_the_footer_survives():
    """The reason this cannot truncate. On 45 records the footer sits in the
    middle, and cutting from it onward drops half of somebody's research."""
    text = f"Computing Accessibility\n\n{FOOTER}\n{NAV}\n\nHuman Computer Interaction"
    assert strip_site_chrome(text) == "Computing Accessibility\n\nHuman Computer Interaction"


def test_a_trailing_footer_is_removed_without_touching_the_bio():
    bio = ("Patrizia Lombardi Acerra holds an MA in Pastoral Studies. She is the "
           "Founder of the International Voices Project.")
    assert strip_site_chrome(f"{bio}\n \n{FOOTER}") == bio


def test_a_summary_that_is_only_footer_becomes_empty():
    """196 summaries were nothing but the address block. Empty is the honest
    answer; the alternative is matching people on a street address."""
    assert strip_site_chrome(FOOTER) == ""


def test_the_audience_menu_goes_too():
    assert strip_site_chrome(f"Robotics\n{NAV}") == "Robotics"


# ── Robustness to what the scrape actually captured ─────────────────────────

@pytest.mark.parametrize("variant", [
    FOOTER,
    FOOTER.replace("\n", "\r\n"),                       # Windows line endings
    FOOTER.replace(" ", "\xa0", 1),                     # non-breaking space
    FOOTER.replace("1 E. Jackson", "1 E Jackson"),      # missing period
    FOOTER.replace("(312) 362-8000", "312 362-8000"),   # unparenthesised area code
    FOOTER.upper(),
])
def test_footer_variants_are_all_recognised(variant):
    assert strip_site_chrome(f"Robotics\n{variant}") == "Robotics"
    assert has_site_chrome(variant)


def test_clean_text_is_returned_unchanged():
    """Runs over the whole corpus, so it must not damage the 601 good records."""
    bio = "I study fairness in clinical risk models.\n\nAlso algorithmic accountability."
    assert strip_site_chrome(bio) == bio


def test_running_twice_changes_nothing_further():
    text = f"Robotics\n{FOOTER}\n\nControl theory"
    once = strip_site_chrome(text)
    assert strip_site_chrome(once) == once


def test_empty_and_none_are_passed_through():
    assert strip_site_chrome("") == ""
    assert strip_site_chrome(None) is None
    assert has_site_chrome(None) is False


def test_a_real_mention_of_the_university_is_not_a_footer():
    """"DePaul University" appears in genuine bios constantly. Only the full
    address block counts as furniture."""
    bio = "He currently serves as co-head of the BFA Acting program at DePaul University."
    assert strip_site_chrome(bio) == bio
    assert not has_site_chrome(bio)


# ── Telling debris from a short real answer ─────────────────────────────────

@pytest.mark.parametrize("real", [
    "Design", "Animation", "Simulation", "Screenwriting", "Irish History",
    "Post Production", "Computer Science", "Cinema Production",
])
def test_short_but_real_research_areas_are_kept(real):
    """These are complete answers to what somebody researches. A blanket
    "under 25 characters is useless" rule deleted every one of them once the
    footer padding came off."""
    assert not is_junk_summary(real)


@pytest.mark.parametrize("fragment", ["and", "her other", "is mathematics"])
def test_fragments_torn_out_of_a_sentence_are_junk(fragment):
    assert is_junk_summary(fragment)


@pytest.mark.parametrize("invisible", ["​", "​​​", "\xa0​", "   "])
def test_invisible_characters_only_is_junk(invisible):
    assert is_junk_summary(invisible)


def test_already_empty_is_not_reported_as_junk():
    """There is nothing to clear, so the repair script should not count it."""
    assert not is_junk_summary("")
    assert not is_junk_summary(None)


def test_a_long_lowercase_summary_is_kept():
    """fix_summary repairs these by prefixing "Research interests"; the length
    bound keeps the fragment rule off real prose."""
    text = ("investigates how caseworkers contest algorithmic risk scores in "
            "child welfare settings")
    assert not is_junk_summary(text)


# ── Lifting a research-interests list out of the bio ────────────────────────

def test_a_list_under_an_interests_heading_moves_to_the_interests_field():
    """The reported case. The topics were sitting in the bio while the
    interests box read "None yet"."""
    summary = ("Lee earned a Ph.D. in Sociology from the University of Texas at Austin in 2023.\n\n"
               "Research and Interests:\n\nCulture\n\nPolitics\n\nRace and Ethnicity\n\nSocial Inequality")
    from text_clean import split_interests
    interests, prose = split_interests(summary)
    assert interests == ["Culture", "Politics", "Race and Ethnicity", "Social Inequality"]
    assert prose.startswith("Lee earned a Ph.D.")
    assert "Culture" not in prose


def test_a_summary_that_is_nothing_but_topics_becomes_interests():
    """A page with no prose bio produces only the list."""
    from text_clean import split_interests
    interests, prose = split_interests("Computing Accessibility\n\nHuman Computer Interaction")
    assert interests == ["Computing Accessibility", "Human Computer Interaction"]
    assert prose == ""


def test_prose_is_never_mined_for_stray_short_lines():
    """This is why the parser is conservative. Mining prose for short lines
    yields a play, a magazine, and a website as somebody's research interests."""
    from text_clean import split_interests
    summary = ("He coached the August Wilson monologue competition\nGiving Voice\n, produced by "
               "Denzel Washington. His advice columns for\nBackstage\nmagazine and his site\n"
               "www.Adlerimprov.com\nreflect his commitment to emerging talent.")
    interests, prose = split_interests(summary)
    assert interests == []
    assert prose == summary


def test_a_degree_list_is_not_research_interests():
    from text_clean import split_interests
    interests, _ = split_interests(
        "Education\nMFA, Screenwriting — DePaul University\nMA, Cinema Studies — DePaul University")
    assert interests == []


def test_ordinary_prose_is_left_alone():
    from text_clean import split_interests
    bio = "I study fairness in clinical risk models and how caseworkers contest them."
    assert split_interests(bio) == ([], bio)


def test_duplicates_are_collapsed_case_insensitively():
    from text_clean import split_interests
    interests, _ = split_interests("Research Interests:\n\nEcology\n\nZoology\n\necology")
    assert interests == ["Ecology", "Zoology"]


def test_empty_input_is_safe():
    from text_clean import split_interests
    assert split_interests("") == ([], "")
    assert split_interests(None) == ([], "")


# ── Page text: break lines at blocks, not inside sentences ──────────────────

def _soup(html):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def test_inline_markup_does_not_split_a_sentence():
    """soup.get_text("\\n") puts a newline between every pair of strings, so one
    italicised title turned one sentence into three lines. 247 bios had it."""
    from text_clean import page_text
    html = "<p>His directing work in <em>Patriot Act</em>, devised with Smith, is acclaimed.</p>"
    assert page_text(_soup(html)) == "His directing work in Patriot Act, devised with Smith, is acclaimed."


def test_block_elements_still_start_new_lines():
    """The section parser needs headings on their own line."""
    from text_clean import page_text
    html = "<div><h2>BIO</h2><p>Some prose.</p><h2>Research Area</h2><p>Robotics</p></div>"
    lines = [l for l in page_text(_soup(html)).split("\n") if l.strip()]
    assert lines == ["BIO", "Some prose.", "Research Area", "Robotics"]


def test_a_br_inside_a_sentence_is_rejoined():
    """Authors insert <br> for visual wrapping. A line ending without terminal
    punctuation before one starting lowercase is one sentence."""
    from text_clean import page_text
    html = "<p>He was exposed to theater in third grade<br>through the public school system.</p>"
    assert page_text(_soup(html)) == "He was exposed to theater in third grade through the public school system."


def test_a_br_between_sentences_is_kept_as_a_break():
    from text_clean import page_text
    html = "<p>He directs plays.<br>She writes them.</p>"
    assert page_text(_soup(html)) == "He directs plays.\nShe writes them."


def test_a_list_is_not_collapsed_into_one_line():
    """Topic lists must survive: each item on its own line."""
    from text_clean import page_text
    html = "<ul><li>Computing Accessibility</li><li>Human Computer Interaction</li></ul>"
    lines = [l for l in page_text(_soup(html)).split("\n") if l.strip()]
    assert lines == ["Computing Accessibility", "Human Computer Interaction"]


def test_a_lowercase_list_is_not_rejoined_into_a_sentence():
    """The reflow rule needs a SINGLE newline; blocks are separated by a blank
    line, which is what keeps it away from list items that happen to start
    lowercase."""
    from text_clean import page_text
    html = "<ul><li>computing accessibility</li><li>human computer interaction</li></ul>"
    lines = [l for l in page_text(_soup(html)).split("\n") if l.strip()]
    assert lines == ["computing accessibility", "human computer interaction"]


def test_site_chrome_is_still_stripped_from_block_separated_text():
    """page_text separates blocks with a BLANK line, and the footer patterns
    were written for single newlines — they stopped matching and the address
    came back."""
    from text_clean import page_text, has_site_chrome, strip_site_chrome
    html = ("<div><p>Robotics</p><div>DePaul University</div><div>1 E. Jackson Blvd.</div>"
            "<div>Chicago, IL 60604</div><div>(312) 362-8000</div>"
            "<div>-or- 1 (800) 4DE PAUL (outside Illinois)</div></div>")
    text = page_text(_soup(html))
    assert has_site_chrome(text)
    assert strip_site_chrome(text) == "Robotics"
