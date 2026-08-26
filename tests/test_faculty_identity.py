"""Recognising the same person across pipeline runs.

faculty.id is referenced by papers, saved profiles, and project matches, so
stage 4 upserts on an identity rather than wiping the table. Getting that
identity wrong is expensive in both directions: too loose and distinct people
merge into one row, too strict and somebody gains a second row and their
publications split across both.

Both failure modes were live. A full directory re-crawl surfaced them.
"""
import importlib.util
import os

import pytest

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")


def _stage(filename):
    path = os.path.join(PIPELINE, filename)
    spec = importlib.util.spec_from_file_location(filename[:-3].lstrip("0123456789_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s4 = _stage("4_db_setup.py")


def person(name, email="", summary=""):
    return {"name": name, "email": email, "research_summary": summary}


# ── An email is not always an email ─────────────────────────────────────────

@pytest.mark.parametrize("junk", ["Full Time", "Part Time", "Faculty", "School of Music", "", None])
def test_a_non_address_in_the_email_column_does_not_identify_anyone(junk):
    """The original scrape shifted columns, leaving employment statuses in the
    email field. Eleven records said "Full Time"; matching on that merges
    everyone who shares an employment status into a single person."""
    assert s4._identifying_email(junk) == ""


def test_a_real_address_is_normalised():
    assert s4._identifying_email("  MBachma3@DePaul.edu ") == "mbachma3@depaul.edu"


# ── Shared inboxes ──────────────────────────────────────────────────────────

def test_a_departmental_inbox_is_detected_as_shared():
    """theatreschoolpr@depaul.edu is listed on ten different people."""
    records = [person("Bella Itkin", "theatreschoolpr@depaul.edu"),
               person("Joe Slowik", "theatreschoolpr@depaul.edu"),
               person("Ric Murphy", "theatreschoolpr@depaul.edu"),
               person("Solo Person", "solo@depaul.edu")]
    assert s4.shared_emails(records) == {"theatreschoolpr@depaul.edu"}


def test_one_person_listed_twice_is_not_a_shared_inbox():
    """A joint appointment legitimately repeats the same person and address."""
    records = [person("Jane Doe", "jdoe@depaul.edu"), person("Jane Doe", "jdoe@depaul.edu")]
    assert s4.shared_emails(records) == set()


def test_people_sharing_an_inbox_are_not_collapsed_into_one():
    """The severe bug. Keying on a shared address told _dedupe that ten people
    were one, so nine were dropped before reaching the database at all."""
    records = [person("Bella Itkin", "theatreschoolpr@depaul.edu"),
               person("Joe Slowik", "theatreschoolpr@depaul.edu"),
               person("Ric Murphy", "theatreschoolpr@depaul.edu")]
    kept = s4._dedupe(records, s4.shared_emails(records))
    assert sorted(p["name"] for p in kept) == ["Bella Itkin", "Joe Slowik", "Ric Murphy"]


def test_the_same_person_listed_per_department_still_collapses():
    """The reason _dedupe exists: two rows would compete for one publication set."""
    records = [person("Jane Doe", "jdoe@depaul.edu", "Short"),
               person("Jane Doe", "jdoe@depaul.edu", "A much longer research summary")]
    kept = s4._dedupe(records, s4.shared_emails(records))
    assert len(kept) == 1
    assert kept[0]["research_summary"] == "A much longer research summary"


# ── Identity selection ──────────────────────────────────────────────────────

def test_a_unique_address_identifies_a_person():
    assert s4._identity(person("Jane Doe", "jdoe@depaul.edu")) == ("email", "jdoe@depaul.edu")


def test_a_shared_address_falls_back_to_the_name():
    ambiguous = {"theatreschoolpr@depaul.edu"}
    assert s4._identity(person("Bella Itkin", "theatreschoolpr@depaul.edu"), ambiguous) \
        == ("name", "bella itkin")


def test_a_junk_address_falls_back_to_the_name():
    assert s4._identity(person("Alan Salzenstein", "Full Time")) == ("name", "alan salzenstein")


def test_names_are_folded_for_matching():
    assert s4._identity(person("  Karen   H   Lee  ")) == ("name", "karen h lee")


def test_a_record_with_neither_name_nor_usable_email_is_dropped():
    """_dedupe skips the empty-name key rather than storing a blank person."""
    assert s4._dedupe([person("", "Full Time")]) == []


# ── "Could not ask" is not "no" ─────────────────────────────────────────────

s3 = _stage("3_enrich_openalex.py")


class _Response:
    """Shaped like requests' Response, for the fields get() actually touches."""
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _stub_session(monkeypatch, response):
    monkeypatch.setattr(s3, "session", type("S", (), {"get": lambda *a, **k: response})())
    monkeypatch.setattr(s3.time, "sleep", lambda *_: None)


def test_exhausted_retries_raise_rather_than_look_like_a_miss(monkeypatch):
    """A throttled run printed "not found in OpenAlex" for every person and
    reported success while enriching nobody. Worse, the result is afterwards
    indistinguishable from a genuine absence."""
    _stub_session(monkeypatch, _Response(429, {"error": "Rate limit exceeded"}))

    with pytest.raises(s3.Unreachable):
        s3.get("https://api.openalex.org/authors", {})


def test_a_spent_budget_says_so_and_does_not_burn_six_retries(monkeypatch):
    """OpenAlex is metered now. A budget 429 carries a retry-after measured in
    hours, so backing off is pointless — the run should stop and say why."""
    body = {"error": "Rate limit exceeded",
            "message": "Insufficient budget. This request costs $0.001 but you only "
                       "have $0.0003 remaining. Resets at midnight UTC."}
    calls = []
    monkeypatch.setattr(s3, "session", type("S", (), {
        "get": lambda *a, **k: (calls.append(1), _Response(429, body, {"retry-after": "15585"}))[1]})())
    monkeypatch.setattr(s3.time, "sleep", lambda *_: None)

    with pytest.raises(s3.Unreachable, match="budget"):
        s3.get("https://api.openalex.org/authors", {})
    assert len(calls) == 1, "a spent budget should abort on the first response"


def test_an_ordinary_rate_limit_still_backs_off(monkeypatch):
    """"You are going too fast" is worth waiting out; only a spent budget is not."""
    calls = []
    monkeypatch.setattr(s3, "session", type("S", (), {
        "get": lambda *a, **k: (calls.append(1), _Response(429, {"error": "slow down"}))[1]})())
    monkeypatch.setattr(s3.time, "sleep", lambda *_: None)

    with pytest.raises(s3.Unreachable):
        s3.get("https://api.openalex.org/authors", {})
    assert len(calls) > 1, "a plain rate limit should be retried before giving up"


def test_a_real_negative_answer_still_returns_none(monkeypatch):
    """A 404 means OpenAlex answered and has nobody. That must not raise, or a
    single unknown name would abort the whole stage."""
    _stub_session(monkeypatch, _Response(404))
    assert s3.get("https://api.openalex.org/authors", {}) is None


def test_a_success_returns_the_payload(monkeypatch):
    _stub_session(monkeypatch, _Response(200, {"results": [{"id": "A1"}]}))
    assert s3.get("https://api.openalex.org/authors", {}) == {"results": [{"id": "A1"}]}
