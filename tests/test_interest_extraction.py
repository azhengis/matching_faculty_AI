"""Reading research interests out of biography PROSE.

split_interests handles pages that state their areas as a list. Plenty bury
them in a sentence instead, which regex cannot do safely:

    Dr. Barnard's research mainly focuses on the combinatorics of posets, and
    her work has appeared in the Journal of Algebraic Combinatorics, the
    Annals of Combinatorics, and Algebraic Combinatorics.

The interest is "combinatorics of posets". The three journals are venues, and
a pattern that catches the topic catches them too — an earlier regex attempt
returned a play, a magazine, and a website as somebody's research interests.

So a model reads it. And because a model invents things, everything it
returns is checked back against the text it was given.
"""
import json
import types

import pytest

import web_app

BARNARD = ("Dr. Barnard earned her PhD in Mathematics from North Carolina State University. "
           "Dr. Barnard's research mainly focuses on the combinatorics of posets, and her "
           "work has appeared in such journals as the Journal of Algebraic Combinatorics, "
           "the Annals of Combinatorics, and Algebraic Combinatorics.")


def _stub(monkeypatch, reply):
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(
        completion=lambda **kw: types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])))


# ── Grounding: the guard that makes a cheap model usable ────────────────────

def test_a_topic_the_bio_never_mentions_is_dropped():
    """Not hypothetical. A 3B local model returned "combinatorics of posets"
    and "second language acquisition" for a neuroscientist, both lifted
    verbatim from the examples in the prompt."""
    assert not web_app._grounded_in("second language acquisition", BARNARD)
    assert not web_app._grounded_in("traumatic brain injury", BARNARD)


def test_a_topic_the_bio_states_is_kept():
    assert web_app._grounded_in("combinatorics of posets", BARNARD)


def test_a_reworded_topic_still_counts():
    """Matching whole phrases would reject honest rephrasings."""
    bio = "He studies the accessibility of computing for deaf users."
    assert web_app._grounded_in("computing accessibility", bio)


def test_a_topic_of_only_stopwords_is_not_grounded():
    assert not web_app._grounded_in("of and the", BARNARD)


# ── End to end ──────────────────────────────────────────────────────────────

def test_journals_named_in_the_bio_do_not_become_interests(monkeypatch):
    """The specific trap. Every journal in Barnard's sentence contains the word
    "combinatorics", so they are grounded — they are excluded because the
    prompt names venues as ineligible, and the test pins that behaviour."""
    _stub(monkeypatch, json.dumps(["combinatorics of posets"]))
    assert web_app._extract_interests(BARNARD, "Emily Barnard") == ["combinatorics of posets"]


def test_a_fenced_or_chatty_reply_still_parses(monkeypatch):
    """Small models wrap the array in prose or a markdown fence."""
    _stub(monkeypatch, 'Sure! Here you go:\n```json\n["combinatorics of posets"]\n```')
    assert web_app._extract_interests(BARNARD, "") == ["combinatorics of posets"]


def test_a_paragraph_returned_as_a_topic_is_rejected(monkeypatch):
    """is_topic_line keeps prose out of the chips, same as the structured path."""
    _stub(monkeypatch, json.dumps([BARNARD]))
    assert web_app._extract_interests(BARNARD, "") == []


def test_duplicates_collapse(monkeypatch):
    _stub(monkeypatch, json.dumps(["combinatorics of posets", "Combinatorics of Posets"]))
    assert web_app._extract_interests(BARNARD, "") == ["combinatorics of posets"]


def test_at_most_six_topics(monkeypatch):
    _stub(monkeypatch, json.dumps(["combinatorics"] * 3 + ["posets"] * 5))
    assert len(web_app._extract_interests(BARNARD, "")) <= 6


@pytest.mark.parametrize("reply", ["not json at all", "", "{}", "null", '["' ])
def test_an_unusable_reply_yields_nothing_rather_than_raising(monkeypatch, reply):
    """Nothing here is worth failing a profile over."""
    _stub(monkeypatch, reply)
    assert web_app._extract_interests(BARNARD, "") == []


def test_no_model_configured_is_not_an_error(monkeypatch):
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "")
    monkeypatch.setattr(web_app, "_litellm", None)
    assert web_app._extract_interests(BARNARD, "") == []


def test_an_empty_bio_is_not_sent_to_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(
        completion=lambda **kw: called.append(1)))
    assert web_app._extract_interests("   ", "") == []
    assert called == []


def test_the_model_failing_is_swallowed(monkeypatch):
    def boom(**kw):
        raise RuntimeError("upstream is down")
    monkeypatch.setattr(web_app, "CHATBOT_MODEL", "test/model")
    monkeypatch.setattr(web_app, "_litellm", types.SimpleNamespace(completion=boom))
    assert web_app._extract_interests(BARNARD, "") == []
