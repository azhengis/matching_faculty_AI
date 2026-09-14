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


# ── 1. A truncated reply says so ───────────────────────────────────────────

def test_a_reply_cut_off_by_the_token_limit_is_flagged(project, monkeypatch):
    """The exact report: a message that stops mid-sentence. It used to arrive
    indistinguishable from a complete one, so nobody could tell a truncated
    answer from a badly written one."""
    _stub(monkeypatch, _resp("Right now this is observational. Do you want to keep",
                             finish="length"))
    body = _chat(project)
    assert body["truncated"] is True
    assert body["reply"].endswith("Do you want to keep")


def test_a_complete_reply_is_not_flagged(project, monkeypatch):
    _stub(monkeypatch, _resp("What do you mean by scale?", finish="stop"))
    assert _chat(project)["truncated"] is False


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
    assert calls["n"] == web_app.MAX_TOOL_ROUNDS
    assert body["reply"], "a capped turn must still say something"


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
