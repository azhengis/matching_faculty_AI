"""A project should not sit in the list as "Untitled project".

The advisor is told to propose a working title in its first substantive reply
and save it. It does not always comply, and when it only SAYS the title in the
chat without calling save_proposal, the project keeps the placeholder while the
conversation plainly has a subject.

The older fallback only fired when a problem statement or background was
saved — neither of which happens until Stage 3 — so a project could stay
untitled through the entire interview. These cover the safety net that closes
that, and the two things it must never do: overwrite a real name, or read
meaning into the scripted openers the app sends on the researcher's behalf.
"""
import sqlite3

import pytest

import web_app


@pytest.fixture
def project(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db))
    web_app._init_profiles_db()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'Untitled project')")
    con.commit(); con.close()
    # Deterministic: no model call, so the fallback path is what is measured.
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "")
    return db


def _title(db):
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0]
    finally:
        con.close()


def _said(*texts):
    return [{"role": "user", "content": t} for t in texts]


# ── It names the thing ─────────────────────────────────────────────────────

def test_a_substantive_answer_names_the_project(project):
    web_app._name_project_if_untitled(1, _said(
        "I want to look at how caseworkers contest algorithmic risk scores "
        "during the child welfare appeals process."))
    assert _title(project) != "Untitled project"
    assert "caseworker" in _title(project).lower()


def test_the_explore_placeholder_is_replaced_too(project, monkeypatch):
    """A project handed over from Explore starts as "Exploring new
    directions", which is just as unhelpful once there is a real subject."""
    con = sqlite3.connect(project)
    con.execute("UPDATE projects SET title = 'Exploring new directions' WHERE id = 1")
    con.commit(); con.close()

    web_app._name_project_if_untitled(1, _said(
        "I study how volunteer experiences shape later civic engagement in "
        "first-generation college students."))
    assert _title(project) not in ("Exploring new directions", "Untitled project")


# ── It never overwrites a real name ────────────────────────────────────────

def test_a_title_the_advisor_saved_is_left_alone(project):
    """The advisor complying is the normal path; the net is only for when it
    does not."""
    web_app._save_proposal(1, {"title": "Contestability in Benefits Appeals"})
    web_app._name_project_if_untitled(1, _said("A long substantive message about something else entirely."))
    assert _title(project) == "Contestability in Benefits Appeals"


def test_a_rename_from_the_projects_page_survives(project):
    con = sqlite3.connect(project)
    con.execute("UPDATE projects SET title = 'My Own Name' WHERE id = 1")
    con.commit(); con.close()

    web_app._name_project_if_untitled(1, _said("Another long substantive message about the research."))
    assert _title(project) == "My Own Name"


# ── It reads the researcher, not the app ───────────────────────────────────

@pytest.mark.parametrize("opener", sorted(web_app._SCRIPTED_OPENERS))
def test_the_scripted_openers_never_become_a_title(opener, project):
    """These are sent by the app on the researcher's behalf. They never typed
    them and never see them, so naming a project "Back Where We Were" from the
    resume message would be inventing a subject out of plumbing."""
    web_app._name_project_if_untitled(1, _said(opener))
    assert _title(project) == "Untitled project"


def test_the_advisors_own_words_are_not_used(project):
    """Only the researcher's messages. Naming the project from the advisor's
    question would name it after what the advisor was curious about."""
    history = [{"role": "assistant",
                "content": "What is a research question you have been thinking about lately, "
                           "even if it is still rough and not fully formed yet?"}]
    web_app._name_project_if_untitled(1, history)
    assert _title(project) == "Untitled project"


def test_a_very_short_reply_is_not_enough_to_name_a_project(project):
    """"yes", "not sure", "ok" say nothing about the subject."""
    web_app._name_project_if_untitled(1, _said("yes", "not sure", "ok"))
    assert _title(project) == "Untitled project"


def test_the_first_substantive_message_wins(project):
    """Not the longest, not the latest — what they said the project was about
    when they first said anything real."""
    web_app._name_project_if_untitled(1, _said(
        "Hello.",
        "I am studying vertebral morphology in stream fish across flow regimes.",
        "Also, separately, I have been reading about machine learning in ecology lately."))
    assert "vertebral" in _title(project).lower() or "morphology" in _title(project).lower()


# ── It cannot break a turn ─────────────────────────────────────────────────

def test_a_missing_project_is_not_an_error(project):
    web_app._name_project_if_untitled(999, _said("A long substantive message about research."))


def test_a_database_failure_does_not_propagate(project, monkeypatch):
    """Naming is a convenience. Losing the researcher's turn over it would be
    a far worse trade than an unnamed project."""
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sqlite3, "connect", boom)
    web_app._name_project_if_untitled(1, _said("A long substantive message about research."))


def test_it_runs_on_every_completed_advisor_turn():
    """Structural: if the call is dropped the tests above still pass, because
    they call the helper directly."""
    import inspect
    src = inspect.getsource(web_app.api_advisor_chat)
    assert "_name_project_if_untitled(project_id, history)" in src
