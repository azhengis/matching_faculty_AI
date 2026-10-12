"""The problem-statement progress tracker.

The panel shows the exact questions a problem statement needs — contextualize,
show why it matters, set aims and objectives — as a checklist that fills in as
the researcher answers them. It is driven by the advisor's note_progress tool,
mirroring Explore's note_understanding: the advisor records each answer in the
researcher's own words the moment it is given, never ahead of it.
"""
import json
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


def _tracker(db, project_id=1):
    con = sqlite3.connect(db)
    try:
        return web_app._read_proposal(con, project_id)["tracker"]
    finally:
        con.close()


# ── The checklist is the research-guide's questions ────────────────────────

def test_the_checklist_has_the_three_steps(project):
    titles = [s["title"] for s in _tracker(project)["steps"]]
    assert titles == ["Contextualize the problem", "Show why it matters",
                      "Set aims and objectives"]


def test_it_starts_empty_so_the_roadmap_shows_before_anything_is_answered(project):
    t = _tracker(project)
    assert t["answered"] == 0 and t["total"] == 8
    assert all(not it["done"] for step in t["steps"] for it in step["items"])


def test_practical_phrasing_is_the_default(project):
    q = _tracker(project)["steps"][0]["items"][0]["question"]
    assert q == "Where and when does the problem arise?"


def test_theoretical_phrasing_shows_when_the_type_is_theoretical(project):
    web_app._note_progress(1, {"problem_type": "theoretical"})
    q = _tracker(project)["steps"][0]["items"][0]["question"]
    assert q == "What is already known about the problem?"


# ── note_progress fills it, in the researcher's words ──────────────────────

def test_answering_a_question_ticks_it_off_with_the_answer(project):
    web_app._note_progress(1, {"ctx_1": "Turnout drops each midterm in the Southeast."})
    item = _tracker(project)["steps"][0]["items"][0]
    assert item["done"] is True
    assert item["note"] == "Turnout drops each midterm in the Southeast."


def test_it_merges_rather_than_replaces(project):
    web_app._note_progress(1, {"ctx_1": "First answer."})
    web_app._note_progress(1, {"ctx_2": "Second answer."})
    t = _tracker(project)
    assert t["answered"] == 2
    assert t["steps"][0]["items"][0]["note"] == "First answer."
    assert t["steps"][0]["items"][1]["note"] == "Second answer."


def test_an_empty_string_walks_an_answer_back(project):
    web_app._note_progress(1, {"ctx_1": "An answer."})
    web_app._note_progress(1, {"ctx_1": ""})
    assert _tracker(project)["steps"][0]["items"][0]["done"] is False


def test_a_call_with_nothing_to_record_is_rejected(project):
    assert web_app._note_progress(1, {})["status"] == "error"


def test_long_answers_are_capped(project):
    web_app._note_progress(1, {"aim": "word " * 500})
    note = next(it["note"] for s in _tracker(project)["steps"] for it in s["items"]
                if it["key"] == "aim")
    assert len(note) <= 400


# ── It completes with the problem statement ────────────────────────────────

def test_it_is_marked_complete_once_the_problem_statement_is_saved(project):
    assert _tracker(project)["complete"] is False
    web_app._save_proposal(1, {"problem_statement": "The settled problem statement."})
    assert _tracker(project)["complete"] is True


# ── Stage is derived the same way the prompt derives it ────────────────────

def test_the_stage_matches_the_saved_state(project):
    con = sqlite3.connect(project)
    assert web_app._read_proposal(con, 1)["stage"] == "1-2"
    con.close()
    web_app._save_proposal(1, {"novelty": "N."})
    con = sqlite3.connect(project)
    assert web_app._read_proposal(con, 1)["stage"] == "3"
    con.close()
    web_app._save_proposal(1, {"problem_statement": "P."})
    con = sqlite3.connect(project)
    p = web_app._read_proposal(con, 1)
    assert p["stage"] == "4" and p["stage_label"] == "Building the proposal"
    con.close()


def test_the_derived_stage_agrees_with_the_prompt():
    """_derive_stage is the single source; the prompt reads the same thing."""
    assert web_app._derive_stage({}) == "1-2"
    assert web_app._derive_stage({"novelty": "N."}) == "3"
    assert web_app._derive_stage({"problem_statement": "P."}) == "4"


# ── Wiring ─────────────────────────────────────────────────────────────────

def test_note_progress_is_a_registered_advisor_tool():
    names = {t["function"]["name"] for t in web_app._ADVISOR_TOOLS}
    assert "note_progress" in names


def test_the_advisor_is_told_to_fill_the_tracker():
    stable, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": {}})
    assert "note_progress" in stable
    assert "THE PROBLEM-STATEMENT TRACKER" in stable
