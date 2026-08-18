"""Explore mode: the second door into the advisor.

Bamshad asked for an exploration function that proposes directions from a
faculty member's own background, for the researcher who wants to expand their
work rather than one who already knows their problem. It governs the OPENING
only — once a direction is picked, the saved proposal drives the stages exactly
as it does for any other project.
"""
import asyncio
import json
import sqlite3

import web_app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _setup(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT, college TEXT)")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "j@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}
    _run(web_app.api_profile_save(_FakeRequest(
        {"faculty_id": None, "name": "Jane", "bio_text": "Formal methods.",
         "confirmed_paper_ids": [], "research_interests": []}, cookies)))
    return cookies


def _create(cookies, **body):
    return json.loads(_run(web_app.api_projects_create(_FakeRequest(body, cookies))).body)


def test_a_project_can_start_in_explore_mode(tmp_path, monkeypatch):
    cookies = _setup(tmp_path, monkeypatch)
    created = _create(cookies, start_blank=True, mode="explore")

    assert created["mode"] == "explore"
    assert created["title"] == "Exploring new directions"


def test_a_normal_project_is_unaffected(tmp_path, monkeypatch):
    cookies = _setup(tmp_path, monkeypatch)
    created = _create(cookies, start_blank=True)

    assert created["mode"] == "problem"
    assert created["title"] == "Untitled project"


def test_an_unrecognised_mode_falls_back_rather_than_being_stored(tmp_path, monkeypatch):
    """The mode reaches a prompt, so only the two known values may be trusted."""
    cookies = _setup(tmp_path, monkeypatch)
    assert _create(cookies, start_blank=True, mode="../../etc/passwd")["mode"] == "problem"


def test_the_mode_survives_so_a_resumed_exploration_is_not_reopened_as_an_interview(
        tmp_path, monkeypatch):
    cookies = _setup(tmp_path, monkeypatch)
    pid = _create(cookies, start_blank=True, mode="explore")["project_id"]

    fetched = json.loads(_run(web_app.api_project_get(pid, _FakeRequest(cookies=cookies))).body)
    assert fetched["mode"] == "explore"


def test_explore_mode_suspends_the_no_suggestions_rule_only_for_itself():
    """Stage 1 forbids proposing directions. Explore is the one exception, and
    it must not leak into an ordinary project."""
    explore, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "Exploring new directions",
        "project_mode": "explore", "bio": "Formal methods.", "papers": [],
        "research_activities": "Works on temporal logic verification.", "proposal": {}})
    problem, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "A project", "project_mode": "problem",
        "bio": "Formal methods.", "papers": [], "proposal": {}})

    assert "EXPLORE MODE" in explore
    assert "EXPLORE MODE" not in problem
    # The researcher's name is interpolated, not left as a literal placeholder.
    assert "Vincent did not arrive with a problem" in explore
    assert "{name}" not in explore


def test_explore_hands_back_to_stage_one_once_a_direction_is_chosen():
    """Otherwise it would keep offering menus forever."""
    explore, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "t", "project_mode": "explore",
        "bio": "", "papers": [], "proposal": {}})

    assert "explore mode is over" in explore
    assert "Stage 1" in explore


def test_the_opening_does_not_summarise_past_work_back_at_them():
    """Bamshad: the first interaction should focus on the new problem rather
    than referencing past work, so a faculty member moving into a new field is
    not framed as continuing the old one. The profile is still fully available
    to the model; it just does not lead."""
    problem, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "t", "project_mode": "problem",
        "bio": "Formal methods.", "papers": [], "proposal": {}})

    assert "OPEN ON THE NEW WORK, NOT THE OLD" in problem
    assert "does not have to relate to their previous work" in problem
    # The old instruction to lead with their research area must be gone.
    assert "Show you know their work" not in problem


def test_explore_mode_is_exempt_from_that_rule():
    """Suggesting directions from existing work is the entire point of Explore."""
    explore, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "t", "project_mode": "explore",
        "bio": "Formal methods.", "papers": [], "proposal": {}})

    assert "The exception is EXPLORE mode" in explore
    assert "EXPLORE MODE" in explore
