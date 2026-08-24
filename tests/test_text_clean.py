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
