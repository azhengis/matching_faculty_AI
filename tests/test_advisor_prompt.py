"""Assembling the advisor's system prompt.

The prompt-caching split (61efdef) left the `stable = ...` assignment indented
inside the `else:` branch that handles Stages 1-2. Stages 1-2 worked, and
nothing else did: the moment a project saved a novelty claim, the next turn
raised UnboundLocalError on `return stable, volatile`. Stage gating is derived
from saved columns precisely so it survives restarts, which meant the crash was
permanent for that project rather than something a reload cleared.

These tests build the prompt at every stage a real project passes through.
"""
import pytest

import web_app

STAGES = [
    ("stages 1-2", {}, "STAGES 1-2"),
    ("stage 3", {"novelty": "Nobody has tested whether X holds for Y."}, "STAGE 3"),
    ("stage 4", {"problem_statement": "Caseworkers cannot contest risk scores."}, "STAGE 4"),
    ("stage 4 over stage 3", {"novelty": "N.", "problem_statement": "P."}, "STAGE 4"),
]


@pytest.mark.parametrize("label,proposal,expected", STAGES, ids=[s[0] for s in STAGES])
def test_the_prompt_builds_at_every_stage(label, proposal, expected):
    """The regression. Three of these four raised UnboundLocalError."""
    stable, volatile = web_app._advisor_system_prompt(
        {"name": "Jane", "proposal": proposal})
    assert stable and volatile
    assert expected in stable


@pytest.mark.parametrize("label,proposal,expected", STAGES, ids=[s[0] for s in STAGES])
def test_exactly_one_stage_directive_is_present(label, proposal, expected):
    """Two stage lines at once would tell the advisor to be in both places."""
    stable, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": proposal})
    directives = [line for line in stable.splitlines()
                  if line.startswith(("STAGES 1-2 —", "STAGE 3 —", "STAGE 4 —"))
                  and ("Nothing is settled" in line or "is settled and saved" in line
                       or "Novelty is settled" in line)]
    assert len(directives) == 1, f"expected one stage directive, got {directives}"


def test_a_saved_problem_statement_outranks_a_saved_novelty_claim():
    """A project that acquired a problem statement before novelty existed as a
    step must not be dragged back to Stage 3."""
    stable, _ = web_app._advisor_system_prompt(
        {"name": "Jane", "proposal": {"novelty": "N.", "problem_statement": "P."}})
    assert "STAGE 4 — BUILD THE PROPOSAL" in stable
    assert "STAGE 3 — WRITE THE PROBLEM STATEMENT. Novelty is settled" not in stable


def test_whitespace_only_sections_do_not_advance_the_stage():
    """A save that wrote only a blank line would otherwise skip the interview."""
    stable, _ = web_app._advisor_system_prompt(
        {"name": "Jane", "proposal": {"novelty": "   \n  ", "problem_statement": ""}})
    assert "STAGES 1-2" in stable


def test_the_instruction_half_is_identical_across_stages_apart_from_the_directive():
    """The stable half is what prompt caching bills at a tenth of the rate. If
    anything but the stage line varied, the cache would miss on every save."""
    prompts = {}
    for label, proposal, _ in STAGES:
        stable, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": proposal})
        body = [l for l in stable.splitlines()
                if not l.startswith(("STAGES 1-2 —", "STAGE 3 — WRITE", "STAGE 4 — BUILD"))]
        prompts[label] = body
    baseline = prompts["stages 1-2"]
    for label, body in prompts.items():
        assert body == baseline, f"{label} diverges from the cached instruction half"


def test_the_live_proposal_lands_in_the_volatile_half_not_the_stable_one():
    """Order is what makes the cache work: instructions first, the proposal
    last. Text that changes on every save must not sit in the cached prefix."""
    _, volatile = web_app._advisor_system_prompt(
        {"name": "Jane", "proposal": {"problem_statement": "A distinctive marker phrase."}})
    stable, _ = web_app._advisor_system_prompt(
        {"name": "Jane", "proposal": {"problem_statement": "A distinctive marker phrase."}})
    assert "A distinctive marker phrase." in volatile
    assert "A distinctive marker phrase." not in stable
