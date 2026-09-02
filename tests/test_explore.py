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
    promise the advisor then has to walk back — and agreeing when the
    researcher says it is the same error."""
    captured = _stub(monkeypatch, _resp("A question back."))
    _chat(profile[1])

    system = captured["calls"][0]["messages"][0]["content"]
    assert "YOU HAVE NO LITERATURE SEARCH" in system
    assert "Never claim a direction is novel" in system
    assert "never agree when they say so" in system


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


# ── Tone: honest, not supportive ────────────────────────────────────────────

def _advisor(**over):
    base = {"name": "Vincent", "project_title": "t", "project_mode": "problem",
            "bio": "", "papers": [], "proposal": {}}
    base.update(over)
    return web_app._advisor_system_prompt(base)[0]


def _explore():
    return web_app._explore_agent_system_prompt(
        {"name": "Vincent", "papers": [], "documents": [], "interests": [],
         "bio": "", "activities": ""})


@pytest.mark.parametrize("prompt_name,prompt", [("advisor", None), ("explore", None)])
def test_both_bots_are_told_not_to_capitulate(prompt_name, prompt):
    """A model that agrees with whatever was said last is useless to somebody
    stress-testing their own proposal against it."""
    text = _advisor() if prompt_name == "advisor" else _explore()
    assert "You're right" in text
    assert "good point" in text
    assert "fair enough" in text


def test_the_advisor_must_go_at_the_weakest_part():
    """The value of the exchange is finding the flaw a reviewer would find in
    six months, while it is still cheap to fix."""
    text = _advisor()
    assert "GO AT THE WEAKEST PART" in text
    assert "without cushioning it" in text


def test_the_advisor_separates_courtesy_to_the_person_from_rigour_on_the_work():
    """Unsparing about the proposal is not the same as rude to the professor,
    and the prompt has to say which one it means."""
    text = _advisor()
    assert "THE PROPOSAL IS THE CLIENT, NOT THE PERSON" in text
    assert "never manufacture one to seem rigorous" in text


def test_explore_does_not_praise_the_idea():
    """Warmth toward an idea reads as agreement the bot has not earned, and
    this bot's whole job is to test the idea rather than endorse it."""
    text = _explore()
    assert "No praise." in text
    for word in ("exciting", "promising", "rich", "timely"):
        assert word in text, f"{word!r} should be named as a banned descriptor"


# ── The interview model ─────────────────────────────────────────────────────

def test_explore_does_not_supply_the_direction():
    """The inversion. It used to open with 4-6 directions of its own; a
    direction the bot produced is one the researcher politely accepts and
    never pursues, and it finds them the wrong collaborators."""
    text = _explore()
    assert "YOU DO NOT SUPPLY THE DIRECTION" in text
    assert "4-6 CONCRETE directions" not in text


def test_an_example_may_not_introduce_a_direction():
    """The precise failure: "is it professional developers, open-source
    contributors, or a controlled task?" reads as clarification and is three
    research designs the researcher never mentioned."""
    text = _explore()
    assert "NEVER USE AN EXAMPLE TO CLARIFY AN AMBIGUOUS IDEA" in text
    assert "professional developers inside a company" in text


def test_suggestions_are_available_only_on_an_explicit_request():
    """Bamshad asked for a suggestion capability; it survives as opt-in rather
    than as the thing that happens to people who never asked."""
    text = _explore()
    assert "ONE EXCEPTION" in text
    assert "Wanting to be asked is not asking" in text


def test_the_funnel_distinguishes_an_interest_from_a_research_problem():
    """An interesting topic is not automatically a research problem, and the
    rungs are what stop the conversation skipping that."""
    text = _explore()
    assert "interest → observation → problem" in text
    assert "THESE ARE NOT THE SAME THING" in text


def test_vague_words_are_challenged():
    text = _explore()
    for word in ('"better"', '"effective"', '"accurate"', '"novel"'):
        assert word in text, f"{word} should be named as a placeholder to challenge"


def test_the_direction_is_confirmed_before_handoff():
    text = _explore()
    assert "Do not declare a direction early" in text
    assert "ask them to confirm or correct it" in text


def test_praise_adjectives_stay_banned_in_the_advisor():
    """The pre-existing rule must survive the new ones."""
    text = _advisor()
    assert "not a cheerleader" in text
    assert "that's a good anchor" in text


# ── The Socratic interview rule ─────────────────────────────────────────────

def test_the_advisor_must_not_hand_over_candidate_answers():
    """Reviewed behaviour: the bot answered with "For instance..." and gave
    conceptual options the professor had not considered. Three plausible
    answers teach them what is expected, they pick one to be agreeable, and
    the proposal is then partly the bot's — which also corrupts matching."""
    text = _advisor()
    assert "NEVER HAND THEM POSSIBLE ANSWERS" in text
    for banned in ('"for instance"', '"for example"', '"such as"'):
        assert banned in text, f"{banned} should be named"
    assert 'is it A, B, or C' in text


def test_the_advisor_is_given_the_socratic_moves_instead():
    text = _advisor()
    for move in ('"What do you mean by ___?"',
                 '"Why is that important?"',
                 '"What would you want to observe?"',
                 '"What makes you believe that?"',
                 '"What would allow you to answer that?"'):
        assert move in text, f"missing {move}"


def test_only_an_explicit_request_unlocks_options():
    text = _advisor()
    assert "Anything short of asking is not asking" in text


def test_a_stalled_conversation_asks_permission_before_offering_options():
    """The dead-end case. Never supplying options would stall forever;
    supplying them unasked breaks the rule. Asking first does neither."""
    text = _advisor()
    assert "ASK WHETHER THEY WANT OPTIONS" in text
    assert "A yes is the explicit request" in text


def test_stage_one_stays_out_of_data_and_methods():
    """Reviewed behaviour: it asked about data collection before the problem
    was narrowed, which settles the problem to fit the available data."""
    text = _advisor()
    assert "STAY OUT OF METHODS AND DATA HERE" in text
    assert "Methods are Stage 4" in text


def test_option_blocks_are_confined_to_three_cases():
    text = _advisor()
    assert "THREE PLACES ONLY" in text
    assert "in Stages 1 and 3 entirely, ask what they mean instead" in text


# ── Gap reasoning before the search ─────────────────────────────────────────

def test_the_researcher_states_the_gap_before_the_search_runs():
    """The bot has a literature search and the professor does not, so
    searching first hands them a gap they then agree with."""
    text = _advisor()
    assert "FIRST, MAKE THEM SAY WHAT THE GAP IS" in text
    assert "Their reasoning comes first" in text


def test_the_gap_kinds_are_not_listed_for_them():
    """Asking "is it data, method, or population?" is the banned shape. The
    prompt has to ask for the kind without enumerating it as a choice."""
    text = _advisor()
    assert 'Ask it as "what kind of gap is it?" and let them name it' in text


def test_the_research_questions_still_come_before_the_gap():
    """Confirmed decision: questions first, then the gap. A later spec
    proposed the reverse and was not adopted."""
    text = _advisor()
    assert text.index("STAGE 1 — SPECIFY THE RESEARCH PROBLEM") < text.index("STAGE 2 — TEST WHETHER IT IS NOVEL")
    assert "SPECIFIC RESEARCH QUESTIONS" in text.split("STAGE 2 — TEST WHETHER IT IS NOVEL")[0]


# ── Legibility and the closing check ───────────────────────────────────────

def test_the_advisor_explains_why_it_is_asking_when_it_changes_layer():
    text = _advisor()
    assert "SAY WHY YOU ARE ASKING" in text
    assert "not narrating internals" in text


def test_the_opening_removes_the_burden_of_organising_anything():
    text = _advisor()
    assert "not worry about organising it" in text


def test_the_proposal_is_judged_as_a_whole_before_it_is_called_done():
    """Incremental saving settles each section alone; nothing otherwise looks
    at whether they contradict each other."""
    text = _advisor()
    assert "BEFORE YOU CALL THE PROPOSAL DONE" in text
    assert "judged TOGETHER" in text


# ── "What we've uncovered" ──────────────────────────────────────────────────

def _note(cookies, **fields):
    return json.loads(_run(web_app.api_explore_chat(_FakeRequest({"message": "x"}, cookies))).body)


def test_a_settled_rung_appears_in_the_panel(profile, monkeypatch):
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("note_understanding", {"interest": "How developers use AI suggestions"})),
          _resp("What have you seen?"))
    assert _chat(cookies)["understanding"] == {"interest": "How developers use AI suggestions"}


def test_a_later_rung_does_not_wipe_an_earlier_one(profile, monkeypatch):
    """The model sends only what just became clear, so a note about the
    observation must not blank the interest recorded three turns ago."""
    _, cookies = profile
    _stub(monkeypatch, _resp(tool=("note_understanding", {"interest": "AI suggestions"})),
          _resp("And?"))
    _chat(cookies)
    _stub(monkeypatch, _resp(tool=("note_understanding", {"observation": "Good ones get ignored"})),
          _resp("Why?"))
    got = _chat(cookies)["understanding"]
    assert got == {"interest": "AI suggestions", "observation": "Good ones get ignored"}


def test_an_empty_value_clears_a_rung(profile, monkeypatch):
    """A correction has to be able to walk something back."""
    _, cookies = profile
    _stub(monkeypatch, _resp(tool=("note_understanding", {"interest": "A", "problem": "B"})), _resp("ok"))
    _chat(cookies)
    _stub(monkeypatch, _resp(tool=("note_understanding", {"problem": ""})), _resp("ok"))
    assert _chat(cookies)["understanding"] == {"interest": "A"}


def test_the_panel_survives_a_reload(profile, monkeypatch):
    _, cookies = profile
    _stub(monkeypatch, _resp(tool=("note_understanding", {"question": "What drives acceptance?"})),
          _resp("ok"))
    _chat(cookies)
    body = json.loads(_run(web_app.api_explore_history(_FakeRequest(cookies=cookies))).body)
    assert body["understanding"] == {"question": "What drives acceptance?"}


def test_starting_over_clears_the_panel_too(profile, monkeypatch):
    """A fresh exploration must not open under the last one's conclusions."""
    _, cookies = profile
    _stub(monkeypatch, _resp(tool=("note_understanding", {"interest": "A"})), _resp("ok"))
    _chat(cookies)
    _run(web_app.api_explore_reset(_FakeRequest(cookies=cookies)))
    body = json.loads(_run(web_app.api_explore_history(_FakeRequest(cookies=cookies))).body)
    assert body["understanding"] == {}


def test_an_unknown_field_is_ignored(profile, monkeypatch):
    """The panel renders four rungs; anything else would never be shown."""
    _, cookies = profile
    _stub(monkeypatch,
          _resp(tool=("note_understanding", {"interest": "A", "methodology": "regression"})),
          _resp("ok"))
    assert _chat(cookies)["understanding"] == {"interest": "A"}


def test_noting_nothing_is_an_error_not_a_silent_success(profile, monkeypatch):
    _, cookies = profile
    _stub(monkeypatch, _resp(tool=("note_understanding", {})), _resp("ok"))
    assert _chat(cookies)["understanding"] == {}


def test_the_bot_is_told_to_note_only_their_own_words(profile, monkeypatch):
    captured = _stub(monkeypatch, _resp("A question."))
    _chat(profile[1])
    system = captured["calls"][0]["messages"][0]["content"]
    assert "Never write into it something they have not said" in system
    assert "an empty field is honest and a guessed one is not" in system
