"""Explore: its own page, its own bot.

Bamshad asked for an exploration function that proposes directions from a
researcher's own background, distinguished from the case where they already
know their problem. It started as a mode on the advisor and became a separate
bot, because the two do opposite things: the advisor refuses to suggest
directions in Stage 1 precisely so it cannot lead someone, and Explore does
nothing but suggest.

Explore has no proposal, no stages, and no literature search. Its job ends at
the handoff: a direction becomes a project, and the advisor takes it from there.
"""
import asyncio
import json
import sqlite3
import types

import pytest

import web_app


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


def _resp(text=None, tool=None):
    """A litellm-shaped response: either plain text or one tool call."""
    calls = None
    if tool:
        calls = [types.SimpleNamespace(
            id="call_1", function=types.SimpleNamespace(
                name=tool[0], arguments=json.dumps(tool[1])))]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=text, tool_calls=calls),
        finish_reason="tool_calls" if tool else "stop")])


@pytest.fixture
def profile(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT, "
                "title TEXT, department TEXT, college TEXT)")
    con.execute("INSERT INTO faculty (id, name, email, title, department) "
                "VALUES (7, 'Jane Doe', 'j@depaul.edu', 'Professor', 'Computing')")
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT, "
                "abstract TEXT, year INTEGER, cited_by_count INTEGER)")
    con.executemany(
        "INSERT INTO papers (id, faculty_id, title, year, cited_by_count) VALUES (?, 7, ?, ?, ?)",
        [(1, "Caseworker discretion and automation", 2024, 12)])
    con.commit()
    con.close()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "j@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}
    _run(web_app.api_profile_save(_FakeRequest(
        {"faculty_id": 7, "name": "Jane Doe", "bio_text": "Algorithmic accountability.",
         "confirmed_paper_ids": [1], "research_interests": ["accountability"]}, cookies)))
    return db_path, cookies


def _stub(monkeypatch, *responses):
    """Queue responses; the endpoint loops until one has no tool call."""
    captured = {"calls": []}
    queue = list(responses)

    def completion(**kw):
        captured["calls"].append(kw)
        return queue.pop(0)

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))
    return captured


def _chat(cookies, message="Show me some directions."):
    return json.loads(_run(web_app.api_explore_chat(_FakeRequest({"message": message}, cookies))).body)


# ── The bot reads their actual work ──────────────────────────────────────────

def test_the_prompt_carries_their_publications_and_attached_material(profile, monkeypatch):
    """Directions grounded in nothing are worthless, so the evidence must be in
    the prompt — including unpublished material the publication list misses."""
    db_path, cookies = profile
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO profile_documents (profile_id, kind, label, filename, extracted_text) "
                "VALUES (1, 'file', 'Grant proposal', 'p.pdf', 'Unpublished: appeals-process audit.')")
    con.commit()
    con.close()

    captured = _stub(monkeypatch, _resp("Here are some directions."))
    _chat(cookies)

    system = captured["calls"][0]["messages"][0]["content"]
    assert "Caseworker discretion and automation" in system
    assert "appeals-process audit" in system


def test_the_bot_is_told_not_to_claim_novelty_it_cannot_check(profile, monkeypatch):
    """It has no literature search. "Nobody has studied this" would be a
    promise the advisor then has to walk back."""
    captured = _stub(monkeypatch, _resp("Directions."))
    _chat(profile[1])

    system = captured["calls"][0]["messages"][0]["content"]
    assert "no literature search" in system.lower()
    assert "Never claim a direction is novel" in system


# ── The handoff ─────────────────────────────────────────────────────────────

def test_settling_on_a_direction_creates_a_project_and_reports_it(profile, monkeypatch):
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("start_project", {"title": "Contestability in appeals",
                                        "direction": "Look at how caseworkers contest risk "
                                                     "scores during the appeals process."})),
          _resp("Set up. The advisor takes it from here."))

    body = _chat(cookies, "Let's do the appeals one.")
    assert body["started"]["title"] == "Contestability in appeals"
    project_id = body["started"]["project_id"]

    fetched = json.loads(_run(web_app.api_project_get(project_id, _FakeRequest(cookies=cookies))).body)
    assert fetched["title"] == "Contestability in appeals"
    assert fetched["mode"] == "explore"          # provenance: this came from Explore


def test_the_chosen_direction_becomes_what_the_advisor_opens_on(profile, monkeypatch):
    """The direction lands in the project's intake background, which the advisor
    reads as the researcher's opening statement, so nobody has to restate it."""
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("start_project", {"title": "Appeals audit",
                                        "direction": "Audit the appeals process."})),
          _resp("Done."))
    body = _chat(cookies, "That one.")

    fetched = json.loads(_run(web_app.api_project_get(
        body["started"]["project_id"], _FakeRequest(cookies=cookies))).body)
    assert fetched["intake"]["background"] == "Audit the appeals process."


def test_the_direction_is_a_starting_point_not_a_written_proposal_section(profile, monkeypatch):
    """Stage 1 still has to specify it by asking. Seeding the proposal would let
    the advisor skip the interview that makes the problem specific."""
    db_path, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("start_project", {"title": "T", "direction": "A direction."})),
          _resp("Done."))
    body = _chat(cookies, "That one.")

    con = sqlite3.connect(db_path)
    row = con.execute("SELECT COUNT(*) FROM proposals WHERE project_id = ?",
                      (body["started"]["project_id"],)).fetchone()
    con.close()
    assert row[0] == 0, "Explore must not write proposal sections"


def test_a_handoff_with_no_direction_text_is_refused(profile, monkeypatch):
    """Without it the advisor opens on nothing and asks them to start over."""
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("start_project", {"title": "T", "direction": "   "})),
          _resp("Sorry, say more first."))
    body = _chat(cookies, "go")
    assert body["started"] is None


def test_an_ordinary_turn_reports_no_project(profile, monkeypatch):
    _stub(monkeypatch, _resp("Here are five directions."))
    assert _chat(profile[1])["started"] is None


# ── The conversation persists ───────────────────────────────────────────────

def test_the_exploration_resumes_instead_of_starting_over(profile, monkeypatch):
    _, cookies = profile
    _stub(monkeypatch, _resp("First answer."))
    _chat(cookies, "Show me directions.")

    history = json.loads(_run(web_app.api_explore_history(_FakeRequest(cookies=cookies))).body)["history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "First answer."


def test_starting_over_clears_the_thread_but_not_the_projects(profile, monkeypatch):
    """Rejected ideas are worse context than none, but a project already created
    is real work."""
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("start_project", {"title": "Kept", "direction": "A direction."})),
          _resp("Done."))
    body = _chat(cookies, "That one.")

    _run(web_app.api_explore_reset(_FakeRequest(cookies=cookies)))
    history = json.loads(_run(web_app.api_explore_history(_FakeRequest(cookies=cookies))).body)["history"]
    assert history == []

    still_there = json.loads(_run(web_app.api_project_get(
        body["started"]["project_id"], _FakeRequest(cookies=cookies))).body)
    assert still_there["title"] == "Kept"


# ── Guards ──────────────────────────────────────────────────────────────────

def test_exploring_requires_a_login():
    assert _run(web_app.api_explore_chat(_FakeRequest({"message": "hi"}))).status_code == 401


def test_exploring_requires_a_profile_to_read(tmp_path, monkeypatch):
    """With nothing on file the bot would invent a career."""
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "empty.db"))
    web_app._init_profiles_db()
    _run(web_app.api_auth_signup(_FakeRequest({"email": "new@depaul.edu", "password": "hunter222"})))
    cookies = {"session_token": list(web_app._auth_sessions.keys())[-1]}

    _stub(monkeypatch, _resp("x"))
    assert _run(web_app.api_explore_chat(_FakeRequest({"message": "hi"}, cookies))).status_code == 404


def test_an_empty_message_is_rejected(profile, monkeypatch):
    _stub(monkeypatch, _resp("x"))
    assert _run(web_app.api_explore_chat(
        _FakeRequest({"message": "   "}, profile[1]))).status_code == 400


# ── The advisor no longer carries an explore mode ───────────────────────────

def test_the_advisor_prompt_has_no_explore_mode_left_in_it():
    """It lives on its own page now. Two copies would drift."""
    for mode in ("problem", "explore"):
        prompt, _ = web_app._advisor_system_prompt({
            "name": "Vincent", "project_title": "t", "project_mode": mode,
            "bio": "Formal methods.", "papers": [], "proposal": {}})
        assert "EXPLORE MODE" not in prompt
        assert "explore mode is over" not in prompt


def test_the_advisor_still_does_not_open_by_summarising_past_work():
    """Bamshad's other point, unaffected by moving Explore out."""
    prompt, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "t", "project_mode": "problem",
        "bio": "Formal methods.", "papers": [], "proposal": {}})
    assert "OPEN ON THE NEW WORK, NOT THE OLD" in prompt
    assert "Show you know their work" not in prompt


def test_the_advisor_acknowledges_a_direction_handed_over_rather_than_re_asking():
    """A project arriving from Explore already states its direction."""
    prompt, _ = web_app._advisor_system_prompt({
        "name": "Vincent", "project_title": "t", "project_mode": "explore",
        "bio": "", "papers": [], "proposal": {}})
    assert "handed over from Explore" in prompt
    assert "Never make them restate it" in prompt
