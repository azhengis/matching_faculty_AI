"""Reading bio-page metadata across DePaul's template change.

A full-time Assistant Professor with a live directory page returned nothing,
because he was hired after the roster snapshot and nothing in the repo could
refresh it. While fixing that, the template turned out to have changed too:
the structured fields are still there, but the "meta-" prefix is gone.

That failure mode is the dangerous kind. Looking only for the prefixed form
returns "" for every field rather than raising, so a re-scrape would quietly
produce records with no college, department, or title.
"""
import importlib.util
import os

import pytest
from bs4 import BeautifulSoup

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")


def _stage(filename):
    """Numbered stages start with a digit, so a plain import is a syntax error."""
    path = os.path.join(PIPELINE, filename)
    spec = importlib.util.spec_from_file_location(filename[:-3].lstrip("0123456789_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage1 = _stage("1_extract_faculty.py")
stage2 = _stage("2_enrich_bios.py")

CURRENT = """<html><head>
  <meta name="FirstName" content="Matthew">
  <meta name="LastName" content="Bachman">
  <meta name="PersonnelTitle" content="Assistant Professor">
  <meta name="College" content="College of Science and Health">
  <meta name="Department" content="Neuroscience;Psychology">
  <meta name="EmploymentStatus" content="Full Time">
  <meta name="Personnel" content="Faculty">
  <meta name="Email" content="mbachma3@depaul.edu">
</head><body><h1>Matthew Bachman</h1></body></html>"""

LEGACY = CURRENT.replace('name="', 'name="meta-')


@pytest.mark.parametrize("reader", [stage1.meta, stage2.meta], ids=["stage1", "stage2"])
@pytest.mark.parametrize("html,label", [(CURRENT, "current"), (LEGACY, "legacy")])
def test_both_template_generations_are_read(reader, html, label):
    """Both readers must handle both, so a crawl during a template rollout does
    not half-succeed."""
    soup = BeautifulSoup(html, "html.parser")
    assert reader(soup, "College") == "College of Science and Health"
    assert reader(soup, "PersonnelTitle") == "Assistant Professor"
    assert reader(soup, "Email") == "mbachma3@depaul.edu"


@pytest.mark.parametrize("reader", [stage1.meta, stage2.meta], ids=["stage1", "stage2"])
def test_a_missing_tag_is_an_empty_string_not_an_error(reader):
    soup = BeautifulSoup(CURRENT, "html.parser")
    assert reader(soup, "NoSuchField") == ""


def test_a_record_is_built_from_a_current_page():
    record = stage1.person_from_page("https://www.depaul.edu/faculty/matthew-bachman", CURRENT)
    assert record["name"] == "Matthew Bachman"
    assert record["title"] == "Assistant Professor"
    assert record["employment_status"] == "Full Time"
    assert record["email"] == "mbachma3@depaul.edu"


def test_a_joint_appointment_is_comma_separated_like_college_is():
    """The page uses semicolons; every later stage splits on commas."""
    record = stage1.person_from_page("https://www.depaul.edu/faculty/x", CURRENT)
    assert record["department"] == "Neuroscience, Psychology"


def test_the_record_matches_the_shape_the_roster_already_uses():
    """Stage 2 reads this file; a missing key would fail deep in the pipeline."""
    record = stage1.person_from_page("https://www.depaul.edu/faculty/x", CURRENT)
    assert set(record) == {
        "name", "title", "department", "college", "personnel_type",
        "employment_status", "email", "bio_url", "research_topics",
        "publications", "research_summary",
    }


def test_the_name_falls_back_to_the_page_title():
    """Some pages carry no FirstName/LastName tags."""
    html = "<html><head><title>Jane Doe | DePaul University - Chicago, IL</title></head></html>"
    record = stage1.person_from_page("https://www.depaul.edu/faculty/jane-doe", html)
    assert record["name"] == "Jane Doe"


def test_a_page_with_no_name_at_all_is_skipped_rather_than_stored_blank():
    assert stage1.person_from_page("https://www.depaul.edu/faculty/x", "<html></html>") is None


# ── Merging a fresh roster over previous enrichment ─────────────────────────

def _write(tmp_path, monkeypatch, roster, enriched):
    import json
    r = tmp_path / "roster.json"
    e = tmp_path / "enriched.json"
    r.write_text(json.dumps(roster))
    if enriched is not None:
        e.write_text(json.dumps(enriched))
    monkeypatch.setattr(stage2, "ROSTER_IN", str(r))
    monkeypatch.setattr(stage2, "JSON_OUT", str(e))


def test_a_new_hire_in_the_roster_enters_the_pipeline(tmp_path, monkeypatch):
    """The bug this replaced: stage 2 read the enriched file INSTEAD of the
    roster whenever it existed, so re-running the crawl could never introduce
    anybody. That defeated the entire point of refreshing the roster."""
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Professor"},
                   {"name": "New Hire", "email": "b@depaul.edu", "title": "Assistant Professor"}],
           enriched=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Professor",
                      "research_summary": "Years of work."}])

    people = stage2.load_people()
    by_email = {p["email"]: p for p in people}
    assert "b@depaul.edu" in by_email
    assert by_email["b@depaul.edu"]["title"] == "Assistant Professor"


def test_enrichment_from_earlier_stages_is_not_clobbered(tmp_path, monkeypatch):
    """The reason the old behaviour existed. Research summaries come from later
    stages and the roster has none, so a naive overwrite would erase them."""
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Professor",
                    "research_summary": ""}],
           enriched=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Professor",
                      "research_summary": "Years of work.", "classes_taught": "CS 101"}])

    person = stage2.load_people()[0]
    assert person["research_summary"] == "Years of work."
    assert person["classes_taught"] == "CS 101"


def test_a_changed_title_is_picked_up_from_the_roster(tmp_path, monkeypatch):
    """Promotions and department moves are exactly what a refresh is for."""
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Full Professor",
                    "department": "Neuroscience"}],
           enriched=[{"name": "Old Hand", "email": "a@depaul.edu", "title": "Associate Professor",
                      "department": "Psychology", "research_summary": "Years of work."}])

    person = stage2.load_people()[0]
    assert person["title"] == "Full Professor"
    assert person["department"] == "Neuroscience"
    assert person["research_summary"] == "Years of work."


def test_somebody_no_longer_listed_is_kept_not_deleted(tmp_path, monkeypatch):
    """A page can vanish because they left, or because the crawl hiccuped.
    This file is not the place to decide which."""
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Still Here", "email": "a@depaul.edu"}],
           enriched=[{"name": "Still Here", "email": "a@depaul.edu"},
                     {"name": "Departed", "email": "z@depaul.edu",
                      "research_summary": "Their work."}])

    names = {p["name"] for p in stage2.load_people()}
    assert names == {"Still Here", "Departed"}


def test_people_are_matched_on_email_not_name_spelling(tmp_path, monkeypatch):
    """The directory renders names inconsistently; the address is stable."""
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Karen H Lee", "email": "k.lee@depaul.edu", "title": "Professor"}],
           enriched=[{"name": "Karen Lee", "email": "K.Lee@depaul.edu",
                      "research_summary": "Culture and politics."}])

    people = stage2.load_people()
    assert len(people) == 1
    assert people[0]["research_summary"] == "Culture and politics."


def test_a_first_run_with_no_enriched_file_just_uses_the_roster(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch,
           roster=[{"name": "Someone", "email": "a@depaul.edu"}], enriched=None)
    assert [p["name"] for p in stage2.load_people()] == ["Someone"]


# ── Headings own their line ─────────────────────────────────────────────────

def test_a_heading_phrase_inside_prose_does_not_split_the_bio():
    """The bug that truncated a biography mid-sentence.

    "Information for" is a footer marker. It fired inside "increase access to
    digital information for people with disabilities", cutting the bio there and
    filing its tail under a footer heading. 282 of 400 sampled pages had at
    least one heading matching mid-line."""
    text = ("BIO\n"
            "He studies how to increase access to digital information for people "
            "with disabilities.\n"
            "Research Area\n"
            "Human Computer Interaction\n")
    sections = stage2.parse_sections(text)
    assert "people with disabilities" in sections["BIO"]
    assert "Information for" not in sections


def test_a_real_heading_on_its_own_line_still_splits():
    text = "BIO\nSome prose.\nInformation for\nCurrent Students\n"
    sections = stage2.parse_sections(text)
    assert sections["BIO"] == "Some prose."


def test_a_short_heading_does_not_fire_inside_a_longer_word():
    """"BIO" must not match a line starting "Biomedical"."""
    text = "Biomedical engineering of prosthetics.\nResearch Area\nRobotics\n"
    sections = stage2.parse_sections(text)
    assert "BIO" not in sections
    assert sections["Research Area"] == "Robotics"


# ── Topic labels and biography prose are different things ───────────────────

def test_stated_research_areas_are_kept_apart_from_the_biography():
    """A page carrying both used to let two words beat three paragraphs: the
    research labels won the single field, and the biography was discarded."""
    text = ("BIO\n"
            "Alonzo obtained his Ph.D. in Computing and Information Sciences. "
            "He was previously a post-doctoral assistant professor.\n"
            "Research Area\nHuman Computer Interaction\n"
            "Specific Research Area\nComputing Accessibility\n")
    sections = stage2.parse_sections(text)

    topics = []
    for heading in stage2.RESEARCH_HEADINGS:
        for line in (sections.get(heading) or "").split("\n"):
            if line.strip() and line.strip() not in topics:
                topics.append(line.strip())

    # Priority order, not page order: RESEARCH_HEADINGS lists the more specific
    # heading first, so "Specific Research Area" leads.
    assert topics == ["Computing Accessibility", "Human Computer Interaction"]
    assert stage2.first_of(sections, stage2.BIO_HEADINGS).startswith("Alonzo obtained")


def test_a_page_with_only_labels_still_yields_a_summary():
    """Otherwise somebody whose page states areas and nothing else becomes
    unsearchable."""
    sections = stage2.parse_sections("Research Area\nRobotics\nControl Theory\n")
    assert stage2.first_of(sections, stage2.BIO_HEADINGS) == ""
    assert sections["Research Area"] == "Robotics\nControl Theory"
