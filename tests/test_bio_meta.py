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
