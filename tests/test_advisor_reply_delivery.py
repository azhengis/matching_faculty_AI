"""Getting the advisor's whole answer to the screen.

A researcher reported a reply that stopped mid-sentence. Chasing it turned up
three separate ways text could go missing between the model and the browser,
none of which failed loudly:

  1. finish_reason "length" was treated exactly like "stop", so a reply that
     ran into max_tokens was delivered as though it were complete.
  2. Anything the advisor wrote BEFORE calling a tool was dropped; only the
     final message was returned.
  3. The template's option parser threw away every line after the first "[n]",
     so a trailing question vanished — and a literature citation like "As
     Smith [1] showed" turned that sentence into a button and deleted the
     rest of the message.

All three produce the same symptom and none of them logged anything.
"""
import asyncio
import json
import re
import sqlite3
import types
from pathlib import Path

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


def _resp(text=None, tool=None, finish=None):
    calls = None
    if tool:
        calls = [types.SimpleNamespace(id="call_1", function=types.SimpleNamespace(
            name=tool[0], arguments=json.dumps(tool[1])))]
    return types.SimpleNamespace(choices=[types.SimpleNamespace(
        message=types.SimpleNamespace(content=text, tool_calls=calls),
        finish_reason=finish or ("tool_calls" if tool else "stop"))])


@pytest.fixture
def project(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db))
    web_app._init_profiles_db()
    web_app._auth_sessions.clear()
    web_app._sessions.clear()

    _run(web_app.api_auth_signup(_FakeRequest({"email": "j@depaul.edu", "password": "hunter222"})))
    token = list(web_app._auth_sessions.keys())[-1]
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name, user_id) VALUES (1, 'Jane', ?)",
                (web_app._auth_sessions[token],))
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'P')")
    con.commit(); con.close()
    return {"session_token": token}


def _stub(monkeypatch, *responses):
    queue = list(responses)
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(
        completion=lambda **kw: queue.pop(0)))


def _chat(cookies, message="Go on."):
    return json.loads(_run(web_app.api_advisor_chat(
        _FakeRequest({"message": message, "project_id": 1}, cookies))).body)


# ── 1. A reply cut off at the token limit is finished, not flagged ──────────

def test_a_reply_cut_off_by_the_token_limit_is_finished_automatically(project, monkeypatch):
    """The exact report: a message that stops mid-sentence. Rather than hand the
    researcher a half-answer and ask them to type "continue", the server keeps
    calling the model until it finishes and stitches the pieces into one reply.
    A continuation picks up exactly where the last one stopped, so the join
    carries no separator — the sentence reads straight through."""
    _stub(monkeypatch,
          _resp("Right now this is observational. Do you want to keep", finish="length"),
          _resp(" it that way, or add a comparison group?", finish="stop"))
    body = _chat(project)
    assert body["reply"] == (
        "Right now this is observational. Do you want to keep"
        " it that way, or add a comparison group?")
    assert "truncated" not in body


def test_the_merged_transcript_is_a_single_assistant_message(project, monkeypatch):
    """A continuation must fold into the message it continues, not append a
    second assistant turn — otherwise the next request has two assistant
    messages back to back and the stored history reads as two half-sentences."""
    _stub(monkeypatch,
          _resp("The gap here is that nobody has measured", finish="length"),
          _resp(" uptake over more than a single term.", finish="stop"))
    _chat(project)

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    contents = [e.get("content") for e in hist if e["role"] == "assistant"]
    assert contents == [
        "The gap here is that nobody has measured"
        " uptake over more than a single term."]


def test_a_complete_reply_is_not_flagged(project, monkeypatch):
    _stub(monkeypatch, _resp("What do you mean by scale?", finish="stop"))
    assert "truncated" not in _chat(project)


# ── 0b. Attachments (file / photo) ─────────────────────────────────────────

def test_a_file_attachment_is_folded_into_the_message(project, monkeypatch):
    """An attached document's extracted text is folded into the stored message,
    so the advisor reads it and it persists."""
    def completion(**kw):
        return _resp("Thanks, I read it.", finish="stop")
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))

    body = json.loads(_run(web_app.api_advisor_chat(_FakeRequest(
        {"message": "Here is my draft.", "project_id": 1,
         "attachment": {"kind": "file", "name": "draft.pdf",
                        "text": "The turnover gap is X."}}, project))).body)
    assert body["reply"] == "Thanks, I read it."

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute("SELECT chat_history FROM projects WHERE id=1").fetchone()[0])
    con.close()
    user = [e["content"] for e in hist if e["role"] == "user"][-1]
    assert "draft.pdf" in user and "The turnover gap is X." in user


def test_an_image_attachment_reaches_the_model_but_not_the_stored_history(project, monkeypatch):
    """A photo is passed to the model as an image for this turn, but the stored
    transcript keeps only a placeholder — so a big image is not re-sent forever."""
    captured = {}

    def completion(**kw):
        captured["messages"] = list(kw["messages"])   # snapshot before the loop appends
        return _resp("I can see it.", finish="stop")
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))

    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    body = json.loads(_run(web_app.api_advisor_chat(_FakeRequest(
        {"message": "What is this?", "project_id": 1,
         "attachment": {"kind": "image", "name": "chart.png", "data_url": data_url}}, project))).body)
    assert body["reply"] == "I can see it."

    last = captured["messages"][-1]
    assert isinstance(last["content"], list)
    assert any(b.get("type") == "image_url" for b in last["content"])

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute("SELECT chat_history FROM projects WHERE id=1").fetchone()[0])
    con.close()
    stored = [e["content"] for e in hist if e["role"] == "user"][-1]
    assert "[Attached image: chart.png]" in stored
    assert "base64" not in stored        # the raw image is NOT persisted


def test_a_message_can_be_empty_when_something_is_attached(project, monkeypatch):
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm",
                        types.SimpleNamespace(completion=lambda **kw: _resp("Got it.", finish="stop")))
    body = json.loads(_run(web_app.api_advisor_chat(_FakeRequest(
        {"message": "", "project_id": 1,
         "attachment": {"kind": "file", "name": "n.txt", "text": "hello"}}, project))).body)
    assert body["reply"] == "Got it."


# ── 0a. The fixed opening greeting ─────────────────────────────────────────

def test_the_first_message_is_a_fixed_greeting_with_the_name(project, monkeypatch):
    """The opening is a canned greeting addressed by first name, not a
    model-written one — so the model is not even called for it."""
    calls = {"n": 0}

    def completion(**kw):
        calls["n"] += 1
        return _resp("model generated", finish="stop")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))

    body = _chat(project, "Let's start a new project.")
    assert body["reply"].startswith("Hi Jane!")
    assert "shape it into" in body["reply"]
    assert calls["n"] == 0        # the model was NOT called for the opening

    # Stored as the assistant's first turn, after the hidden kickoff.
    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    assert hist[-1]["role"] == "assistant" and hist[-1]["content"].startswith("Hi Jane!")


def test_the_message_after_the_greeting_goes_to_the_model(project, monkeypatch):
    """Only the FIRST turn is canned; everything after is the real advisor."""
    _chat(project, "Let's start a new project.")        # greeting, no model
    _stub(monkeypatch, _resp("A real reply.", finish="stop"))
    assert _chat(project, "I study turnover.")["reply"] == "A real reply."


# ── 0. Hand-editing a checklist answer in the panel ────────────────────────

def test_a_checklist_answer_can_be_hand_edited_and_locks(project):
    """The panel's problem-statement questions can be answered by hand, not only
    by the advisor. A hand-entered answer locks: the advisor can neither
    overwrite it nor ask that question again."""
    r = _run(web_app.api_project_progress_edit(
        1, _FakeRequest({"key": "ctx_1", "text": "low-income Southeast neighbourhoods"}, project)))
    assert json.loads(r.body)["status"] == "saved"

    con = sqlite3.connect(web_app.DB_PATH)
    prog = json.loads(con.execute("SELECT progress FROM projects WHERE id=1").fetchone()[0])
    assert prog["ctx_1"] == "low-income Southeast neighbourhoods"
    assert "ctx_1" in prog["_locked"]

    # The advisor must NOT overwrite a hand-locked item.
    web_app._note_progress(1, {"ctx_1": "something the model made up"})
    prog = json.loads(con.execute("SELECT progress FROM projects WHERE id=1").fetchone()[0])
    assert prog["ctx_1"] == "low-income Southeast neighbourhoods"

    # The tracker carries the note and marks it locked for the UI.
    proposal = web_app._read_proposal(con, 1)
    items = [it for trk in proposal["trackers"] for g in trk["groups"] for it in g["items"]]
    ctx1 = next(it for it in items if it["key"] == "ctx_1")
    assert ctx1["note"] == "low-income Southeast neighbourhoods"
    assert ctx1["locked"] is True
    con.close()


def test_releasing_a_hand_edited_item_returns_it_to_the_advisor(project):
    _run(web_app.api_project_progress_edit(1, _FakeRequest({"key": "ctx_2", "text": "mine"}, project)))
    _run(web_app.api_project_progress_edit(1, _FakeRequest({"key": "ctx_2", "release": True}, project)))
    web_app._note_progress(1, {"ctx_2": "advisor value"})

    con = sqlite3.connect(web_app.DB_PATH)
    prog = json.loads(con.execute("SELECT progress FROM projects WHERE id=1").fetchone()[0])
    con.close()
    assert prog["ctx_2"] == "advisor value"        # lock gone, advisor can write
    assert "ctx_2" not in prog.get("_locked", [])


def test_an_unknown_checklist_key_is_rejected(project):
    r = _run(web_app.api_project_progress_edit(1, _FakeRequest({"key": "not_a_field", "text": "x"}, project)))
    assert r.status_code == 400


def test_trackers_tag_which_items_are_editable_answers_vs_criteria():
    """The research-questions items are pass/fail criteria, not free-text
    answers, so the panel must not offer an edit box there. The question itself
    is the research-questions tracker's editable note."""
    trackers = web_app._build_trackers({}, {})
    by_id = {t["id"]: t for t in trackers}
    assert by_id["problem_statement"]["item_kind"] == "answer"
    assert by_id["research_design"]["item_kind"] == "answer"
    assert by_id["research_questions"]["item_kind"] == "criteria"
    assert by_id["research_questions"]["note_key"] == "rq_question"


def test_the_drafted_question_shows_and_locks_in_the_research_questions_box(project):
    _run(web_app.api_project_progress_edit(
        1, _FakeRequest({"key": "rq_question", "text": "Does remote work cut turnover?"}, project)))
    con = sqlite3.connect(web_app.DB_PATH)
    rq = next(t for t in web_app._read_proposal(con, 1)["trackers"]
              if t["id"] == "research_questions")
    con.close()
    assert rq["note"] == "Does remote work cut turnover?"
    assert rq["note_locked"] is True


# ── 1a. A candidate list inside a question is rewritten away ───────────────

def test_looks_like_options_catches_the_real_failure_shapes():
    f = web_app._looks_like_options
    # The two reported openers.
    assert f("What kind of system were you watching this happen in? Something "
             "from your CMS work, a particular cloud platform, a class of "
             "workload (batch, streaming, transactional)?")
    assert f("what specific system or platform have your tests actually been "
             "on, one cloud data warehouse, a particular distributed database, "
             "something you built yourself?")
    # Bare "X, Y, or Z?" menu and an example marker.
    assert f("Is it batch, streaming, or transactional?")
    assert f("What is the setting, such as a hospital or a clinic?")
    # A question that merely names the researcher's OWN terms is left alone.
    assert not f("You mentioned turnout, pay, and culture. Which came first?")
    assert not f("What do you mean by diminishing returns here?")
    assert not f("Where does the problem arise?")
    # A STRUCTURED list is legitimate drafted content, never stripped: the
    # research questions presented with "which of these is most central?".
    assert not f("Here are the questions I drafted:\n[1] Does X affect Y?\n"
                 "[2] Does Z moderate it?\nWhich of these feels most central?")
    assert not f("Draft questions:\n- Does X affect Y, or not?\n- What explains Z?")
    assert not f("Two options:\n1. A longitudinal study\n2. A cross-section\n"
                 "Which fits, a or b?")


def test_a_drafted_research_question_list_is_not_stripped(project, monkeypatch):
    """The reported break: the advisor drafted the questions and asked which was
    central, and the backstop ate them, leaving a dangling question with nothing
    below. A structured list is drafted content and must survive untouched."""
    drafted = ("Here are the research questions I drafted:\n"
               "[1] Does remote work reduce turnover in mid-size firms?\n"
               "[2] Does it vary by neighbourhood?\n"
               "Which of these feels most central?")
    calls = {"n": 0}

    def completion(**kw):
        calls["n"] += 1
        return _resp(drafted, finish="stop")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))
    body = _chat(project)
    assert body["reply"] == drafted
    assert calls["n"] == 1   # no rewrite pass fired


def test_an_inline_option_list_in_a_question_is_rewritten(project, monkeypatch):
    """The deterministic backstop: the advisor offered a menu, so the server
    makes one rewrite pass and the researcher sees a single clean question."""
    _stub(monkeypatch,
          _resp("What system were you watching this on, a cloud warehouse, a "
                "particular database, something you built yourself?", finish="stop"),
          _resp("What system were you watching this on?", finish="stop"))
    body = _chat(project)
    assert body["reply"] == "What system were you watching this on?"

    # The stored transcript carries the cleaned version, so a reload matches.
    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    assert hist[-1]["content"] == "What system were you watching this on?"


def test_a_clean_question_is_not_rewritten(project, monkeypatch):
    """No heuristic match means no extra model call — the backstop stays out of
    the way of a well-formed question."""
    calls = {"n": 0}

    def completion(**kw):
        calls["n"] += 1
        return _resp("What system were you watching this on?", finish="stop")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))
    assert _chat(project)["reply"] == "What system were you watching this on?"
    assert calls["n"] == 1


# ── 1b. An orphaned tool_use never reaches the model ───────────────────────

def test_a_tool_that_raises_still_gets_a_result_so_the_turn_is_valid(project, monkeypatch):
    """A tool_use with no tool_result after it is a 400 from Anthropic, and the
    orphan used to be persisted and poison every later turn. If a tool raises,
    the model gets an error result for that call and carries on."""
    _stub(monkeypatch,
          _resp("Checking the literature.",
                tool=("search_literature", {"query": "x"})),
          _resp("Nothing solid came back — tell me more about the setting."))

    def boom(**kw):
        raise RuntimeError("OpenAlex timed out")
    monkeypatch.setattr(web_app, "_search_literature", boom)

    reply = _chat(project)["reply"]
    assert "tell me more about the setting" in reply

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    # Every assistant tool_call is answered by a tool result with the same id.
    for e in hist:
        if e["role"] == "assistant" and e.get("tool_calls"):
            call_ids = {c["id"] for c in e["tool_calls"]}
            answered = {x["tool_call_id"] for x in hist if x["role"] == "tool"}
            assert call_ids <= answered


def test_a_conversation_already_holding_an_orphan_is_healed(project, monkeypatch):
    """The conversation the researcher already wedged: a stored tool_use with no
    result. On the next message it is paired-off before the request is built,
    so the turn goes through instead of 400ing again."""
    con = sqlite3.connect(web_app.DB_PATH)
    poisoned = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "Let me look.",
         "tool_calls": [{"id": "toulu_orphan", "type": "function",
                         "function": {"name": "search_literature", "arguments": "{}"}}]},
        # no tool result — this is the orphan
    ]
    con.execute("UPDATE projects SET chat_history = ? WHERE id = 1",
                (json.dumps(poisoned),))
    con.commit(); con.close()
    web_app._sessions.clear()

    _stub(monkeypatch, _resp("Good question — what's the population?"))
    reply = _chat(project, "a fresh message")["reply"]
    assert "what's the population?" in reply


def test__pair_tools_strips_both_kinds_of_orphan():
    pair = lambda call_id: {"id": call_id, "type": "function",
                            "function": {"name": "f", "arguments": "{}"}}
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "tool_calls": [pair("1")]},
        {"role": "tool", "tool_call_id": "1", "content": "{}"},       # paired — kept
        {"role": "assistant", "content": "b", "tool_calls": [pair("2")]},  # orphan call
        {"role": "assistant", "content": "c"},
        {"role": "tool", "tool_call_id": "9", "content": "{}"},       # orphan result
        {"role": "assistant", "content": "d"},
    ]
    out = web_app._pair_tools(msgs)
    # The paired call/result survive; the orphan call keeps its prose but loses
    # the dangling tool_calls; the orphan result is dropped entirely.
    assert [m["role"] for m in out] == [
        "user", "assistant", "tool", "assistant", "assistant", "assistant"]
    assert out[3] == {"role": "assistant", "content": "b"}   # tool_calls stripped
    assert all(m.get("role") != "tool" or m.get("tool_call_id") == "1" for m in out)


# ── 2. Nothing said before a tool call is lost ─────────────────────────────

def test_what_the_advisor_says_before_reaching_for_a_tool_still_arrives(project, monkeypatch):
    """"Let me check the literature on that." used to vanish, and the results
    arrived with no lead-in explaining where they came from."""
    _stub(monkeypatch,
          _resp("Let me check what already exists on this.",
                tool=("search_literature", {"query": "algorithmic accountability"})),
          _resp("Four papers bear on it directly."))
    monkeypatch.setattr(web_app, "_search_literature", lambda **kw: {"results": []})

    reply = _chat(project)["reply"]
    assert "Let me check what already exists" in reply
    assert "Four papers bear on it directly." in reply


def test_a_silent_tool_call_does_not_produce_blank_padding(project, monkeypatch):
    """The common case: the model calls a tool with no preamble. The reply must
    not start with empty lines where the missing text would have been."""
    _stub(monkeypatch,
          _resp(None, tool=("save_proposal", {"background": "Some background."})),
          _resp("Saved. Next question."))
    assert _chat(project)["reply"] == "Saved. Next question."


def test_the_transcript_still_records_each_message_separately(project, monkeypatch):
    """Joining for display must not collapse the stored history, which is what
    the model is re-sent next turn."""
    _stub(monkeypatch,
          _resp("First.", tool=("save_proposal", {"background": "B."})),
          _resp("Second."))
    _chat(project)

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    contents = [e.get("content") for e in hist if e["role"] == "assistant"]
    assert contents == ["First.", "Second."]


# ── 3. The option parser never eats prose ──────────────────────────────────

TEMPLATE = (Path(web_app.__file__).parent / "templates" / "advisor.html").read_text()


def _parse_reply(text):
    """Python mirror of parseReply in advisor.html. Kept in step by the
    structural test below, which fails if the template's version is replaced
    with something that scans forward from the first option line again."""
    lines = (text or "").split("\n")
    is_opt = lambda l: re.match(r"^\s*\[(\d+)\]\s*(.+)$", l)
    start = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if not lines[i].strip():
            continue
        if is_opt(lines[i]):
            start = i
            continue
        break
    options = [m.group(2).strip() for l in lines[start:] if (m := is_opt(l))]
    main = "\n".join(lines[:start]).rstrip()
    return main, options


def test_a_normal_menu_still_becomes_buttons():
    main, options = _parse_reply("Pick an approach.\n\n[1] Archival\n[2] Interviews")
    assert main == "Pick an approach."
    assert options == ["Archival", "Interviews"]


def test_a_question_after_the_menu_is_not_swallowed():
    """The advisor is told to put nothing after the options. When it forgets,
    the trailing line used to disappear entirely."""
    text = "Pick one.\n\n[1] Archival\n[2] Interviews\n\nWhich fits your data?"
    main, _ = _parse_reply(text)
    assert "Which fits your data?" in main


def test_a_citation_in_prose_does_not_become_a_button_and_delete_the_message():
    """The worst version. Stage 2 discusses numbered literature, so "[1]" in
    prose is not hypothetical — and it used to turn that sentence into a button
    and drop every line after it."""
    text = "As Smith [1] showed, this is settled.\n\nWhat do you mean by scale?"
    main, options = _parse_reply(text)
    assert options == []
    assert "What do you mean by scale?" in main
    assert "As Smith [1] showed" in main


@pytest.mark.parametrize("text", [
    "Plain question with no options?",
    "Pick one.\n\n[1] A\n[2] B",
    "Pick one.\n\n[1] A\n[2] B\n\nTrailing thought.",
    "As Smith [1] showed.\n\nAnd then a question?",
    "",
])
def test_no_text_is_ever_dropped(text):
    """The invariant that matters: every non-option line reaches the screen."""
    main, options = _parse_reply(text)
    for line in text.split("\n"):
        if not line.strip() or re.match(r"^\s*\[(\d+)\]\s*(.+)$", line):
            continue
        assert line in main, f"dropped: {line!r}"


def test_the_template_parser_scans_backward_from_the_end():
    """Structural guard on the real implementation. The bug was a forward scan
    with a latching flag; this fails if that shape comes back."""
    assert "for (let i = lines.length - 1; i >= 0; i--)" in TEMPLATE
    assert "let inOpts = false" not in TEMPLATE


# ── 4. The turn always ends in JSON ────────────────────────────────────────

def test_a_runaway_tool_loop_ends_instead_of_hanging(project, monkeypatch):
    """The loop was `while True`. A model that keeps reaching for a tool would
    never return, and the host's proxy would eventually answer the browser with
    an HTML error page instead."""
    calls = {"n": 0}

    def completion(**kw):
        calls["n"] += 1
        return _resp("Looking again.", tool=("search_literature", {"query": "x"}))

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))
    monkeypatch.setattr(web_app, "_search_literature", lambda **kw: {"results": []})

    body = _chat(project)
    assert calls["n"] == web_app.MAX_TURN_ROUNDS
    assert body["reply"], "a capped turn must still say something"


def test_an_endless_continuation_also_ends_instead_of_hanging(project, monkeypatch):
    """Auto-continue must honour the same cap. A model that reports "length"
    forever (max_tokens too small for its plan) would otherwise loop without
    end; it stops at the cap and returns the partial it has assembled."""
    calls = {"n": 0}

    def completion(**kw):
        calls["n"] += 1
        return _resp(" more", finish="length")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))

    body = _chat(project)
    assert calls["n"] == web_app.MAX_TURN_ROUNDS
    assert body["reply"].strip(), "a capped turn must still say something"


def test_every_model_call_in_the_turn_is_bounded(project, monkeypatch):
    """A hung upstream call is what produces the proxy timeout in the first
    place, so the timeout has to be on the call, not only on the loop."""
    seen = []

    def completion(**kw):
        seen.append(kw.get("timeout"))
        return _resp("Done.")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=completion))
    _chat(project)
    assert seen == [web_app.LLM_CALL_TIMEOUT]


def test_the_conversation_survives_a_failed_turn(project, monkeypatch):
    """What the error message promises the reader: reload and it is still
    there."""
    def boom(**kw):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=boom))
    _run(web_app.api_advisor_chat(_FakeRequest(
        {"message": "My question.", "project_id": 1}, project)))

    con = sqlite3.connect(web_app.DB_PATH)
    hist = json.loads(con.execute(
        "SELECT chat_history FROM projects WHERE id = 1").fetchone()[0])
    con.close()
    assert any(e["role"] == "user" and e["content"] == "My question." for e in hist)


def test_the_client_reads_the_body_before_trusting_it_as_json():
    """Structural guard on the fix. res.json() on an HTML error page throws a
    parser error, which is what reached the researcher as "The string did not
    match the expected pattern."."""
    assert "const body = await res.text();" in TEMPLATE
    assert "try { data = JSON.parse(body); } catch (_)" in TEMPLATE
    assert "await res.json();\n    if (data.error)" not in TEMPLATE
