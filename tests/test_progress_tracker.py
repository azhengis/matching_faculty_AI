"""The foldable progress trackers: problem statement and research questions.

The panel shows a checklist per stage, filling in as the researcher answers,
driven by the advisor's note_progress tool. Problem statement carries the
guide's three steps; research questions carry the guide's quality criteria plus
the drafted question. Each section reports whether it is active (in progress)
and complete, so the panel folds the finished ones.
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
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'P')")
    con.commit(); con.close()
    return db


def _trackers(db, project_id=1):
    con = sqlite3.connect(db)
    try:
        return web_app._read_proposal(con, project_id)["trackers"]
    finally:
        con.close()


def _section(db, sid):
    return next(s for s in _trackers(db) if s["id"] == sid)


def _items(section):
    return [it for g in section["groups"] for it in g["items"]]


# ── Two sections, in order ─────────────────────────────────────────────────

def test_there_are_problem_statement_and_research_question_sections(project):
    assert [s["id"] for s in _trackers(project)] == \
        ["problem_statement", "research_questions"]


def test_both_start_empty_so_the_whole_roadmap_is_visible(project):
    for s in _trackers(project):
        assert s["answered"] == 0
        assert all(not it["done"] for it in _items(s))


# ── Problem statement ──────────────────────────────────────────────────────

def test_problem_statement_has_the_three_steps(project):
    titles = [g["title"] for g in _section(project, "problem_statement")["groups"]]
    assert titles == ["Contextualize the problem", "Show why it matters",
                      "Set aims and objectives"]


def test_practical_phrasing_is_the_default(project):
    first = _section(project, "problem_statement")["groups"][0]["items"][0]
    assert first["label"] == "Where and when does the problem arise?"


def test_theoretical_phrasing_shows_when_the_type_is_theoretical(project):
    web_app._note_progress(1, {"problem_type": "theoretical"})
    first = _section(project, "problem_statement")["groups"][0]["items"][0]
    assert first["label"] == "What is already known about the problem?"


def test_answering_a_problem_question_ticks_it_off_with_the_answer(project):
    web_app._note_progress(1, {"ctx_1": "Turnout drops each midterm in the SE."})
    item = _section(project, "problem_statement")["groups"][0]["items"][0]
    assert item["done"] and item["note"] == "Turnout drops each midterm in the SE."


def test_problem_statement_completes_when_saved(project):
    assert _section(project, "problem_statement")["complete"] is False
    web_app._save_proposal(1, {"problem_statement": "The settled statement."})
    assert _section(project, "problem_statement")["complete"] is True


# ── Research questions ─────────────────────────────────────────────────────

def test_research_questions_lists_the_six_criteria(project):
    labels = [it["label"] for it in _items(_section(project, "research_questions"))]
    assert "Focused on a single problem or issue" in labels
    assert "Complex — not a yes/no question or an easily-found fact" in labels
    assert len(labels) == 6


def test_the_drafted_question_is_carried_as_the_section_note(project):
    web_app._note_progress(1, {"rq_question": "How have economic factors affected turnout?"})
    assert _section(project, "research_questions")["note"] == \
        "How have economic factors affected turnout?"


def test_marking_a_criterion_ticks_it_off(project):
    web_app._note_progress(1, {"rq_focused": "single issue: turnout",
                               "rq_complex": "not yes/no"})
    sec = _section(project, "research_questions")
    assert sec["answered"] == 2
    done = {it["label"] for it in _items(sec) if it["done"]}
    assert "Focused on a single problem or issue" in done
    assert "Complex — not a yes/no question or an easily-found fact" in done


def test_research_questions_complete_when_saved(project):
    assert _section(project, "research_questions")["complete"] is False
    web_app._save_proposal(1, {"research_questions": "- How does X affect Y?"})
    assert _section(project, "research_questions")["complete"] is True


# ── Active / complete drive folding ────────────────────────────────────────

def test_a_completed_section_is_marked_so_the_panel_can_fold_it(project):
    web_app._save_proposal(1, {"problem_statement": "P.", "research_questions": "Q."})
    ps = _section(project, "problem_statement")
    assert ps["complete"] is True and ps["active"] is False


# ── Merge, walk-back, bounds ───────────────────────────────────────────────

def test_it_merges_rather_than_replaces(project):
    web_app._note_progress(1, {"ctx_1": "First."})
    web_app._note_progress(1, {"rq_focused": "Second."})
    assert _section(project, "problem_statement")["answered"] == 1
    assert _section(project, "research_questions")["answered"] == 1


def test_an_empty_string_walks_an_answer_back(project):
    web_app._note_progress(1, {"rq_focused": "yes"})
    web_app._note_progress(1, {"rq_focused": ""})
    assert _section(project, "research_questions")["answered"] == 0


def test_a_call_with_nothing_is_rejected(project):
    assert web_app._note_progress(1, {})["status"] == "error"


# ── Wiring ─────────────────────────────────────────────────────────────────

def test_note_progress_accepts_the_research_question_fields():
    props = next(t["function"]["parameters"]["properties"]
                 for t in web_app._ADVISOR_TOOLS
                 if t["function"]["name"] == "note_progress")
    for f in ("rq_question", "rq_focused", "rq_complex", "rq_relevant"):
        assert f in props


def test_the_advisor_is_told_what_a_good_research_question_is():
    stable, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": {}})
    assert "WHAT A GOOD RESEARCH QUESTION IS" in stable
    assert "not a value judgment" in stable.lower() or "value judgments" in stable.lower()
    assert "yes/no" in stable


def test_the_derived_stage_still_agrees_with_the_prompt():
    assert web_app._derive_stage({}) == "1-2"
    assert web_app._derive_stage({"novelty": "N."}) == "3"
    assert web_app._derive_stage({"problem_statement": "P."}) == "4"
