"""The advisor against Bamshad's written specification.

Bamshad supplied three things: a three-stage flow with a named deliverable at
the end of each stage, a suggested organization for the finished proposal, and
a nineteen-point framework of lenses for interrogating a research idea. This
file holds the prompt and the schema to all three.

These are structural tests, not behavioural ones. They cannot prove the model
follows an instruction; they prove the instruction is still present. That is
worth having on its own, because the prompt is one 70KB string that several
people edit, and a rule deleted in a rewrite is invisible until a professor
notices the bot stopped doing something. Two of the rules covered here were in
fact dropped by an earlier rewrite and only caught by tests like these.
"""
import pytest

import web_app

import advisor_prompt

# Every stage's instructions joined together. Since the prompt became
# stage-conditional, no single turn carries all the rules — Stage 1 does not
# ship the proposal-building sections, Stage 4 does not ship the interview.
# These tests ask "is this rule still in the prompt at all", which is a
# question about the whole set; where a rule has to reach a PARTICULAR stage,
# the tests below say so explicitly.
STABLE = "\n".join(
    web_app._advisor_system_prompt({"name": "Jane", "proposal": p})[0]
    for p in ({}, {"novelty": "N."}, {"problem_statement": "P."})
)


def _stage_prompt(stage):
    proposal = {"1-2": {}, "3": {"novelty": "N."},
                "4": {"problem_statement": "P."}}[stage]
    return web_app._advisor_system_prompt({"name": "Jane", "proposal": proposal})[0]
TOOLS = {t["function"]["name"]: t["function"] for t in web_app._ADVISOR_TOOLS}
SAVE_PROPOSAL = TOOLS["save_proposal"]["parameters"]["properties"]


# ── The finished proposal carries Bamshad's suggested organization ──────────

#   Title, Abstract, Keywords / Introduction and Overview (background, hypotheses
#   and objectives, assumptions and delimitations, importance and benefits) /
#   Related Work / Research Design and Methodology / Plan of Work and Outcomes /
#   Conclusions and Future Work / References
REQUIRED_SECTIONS = [
    "title", "abstract", "keywords", "background", "objectives", "hypotheses",
    "assumptions_delimitations", "related_work", "methodology",
    "expected_outcomes", "plan_of_work", "conclusions_future_work",
]


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_the_proposal_has_every_section_the_organization_calls_for(section):
    assert section in web_app._PROPOSAL_FIELDS


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_the_advisor_can_actually_save_each_one(section):
    """A field in the schema that save_proposal cannot accept is a section the
    advisor can never fill."""
    assert section in SAVE_PROPOSAL


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_each_section_reaches_the_downloaded_document(section, tmp_path):
    """The .docx is the deliverable. A section saved but never rendered would
    be invisible in the only artifact that leaves the app."""
    from docx import Document
    marker = f"UNIQUE-MARKER-{section}"
    docx = web_app._build_proposal_docx("Jane Doe", {section: marker})
    path = tmp_path / "p.docx"
    path.write_bytes(docx)
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert marker in text, f"{section} never reaches the .docx"


@pytest.mark.parametrize("template", ["advisor.html", "projects.html"])
def test_both_panels_can_render_every_section(template):
    """Each template keeps its own label map. A section saved by the advisor
    but absent from the map is written to the database and never shown, which
    looks exactly like the advisor having ignored the request."""
    from pathlib import Path
    html = (Path(web_app.__file__).parent / "templates" / template).read_text()
    missing = [f for f in web_app._PROPOSAL_FIELDS if f not in html]
    assert missing == [], f"{template} cannot render {missing}"


# ── Title-first ────────────────────────────────────────────────────────────

def test_the_title_is_a_saved_section_not_a_generated_one():
    """Bamshad asked for the work to start from a project title and a problem
    description, literally. The title used to be generated from the problem
    statement after the fact, which meant nobody ever agreed to it."""
    assert "title" in web_app._PROPOSAL_FIELDS
    assert "title" in SAVE_PROPOSAL


def test_the_title_is_named_in_the_first_substantive_reply():
    assert "NAME THE PROJECT FIRST" in STABLE
    assert "FIRST substantive answer, not later" in STABLE


def test_the_advisor_proposes_the_title_rather_than_asking_for_it():
    """A cold "what would you call this?" is the grant-form opening the prompt
    bans everywhere else, and a researcher with a rough observation has no
    title yet. It proposes; they correct."""
    assert "you propose, they correct" in STABLE
    assert "never a message of its own" in STABLE


def test_a_title_the_researcher_supplied_is_used_verbatim():
    assert "use theirs verbatim and do not improve it" in STABLE


def test_the_title_is_re_earned_at_the_problem_statement_and_at_the_end():
    """It is set from a first answer, and the problem moves a long way after
    that. A title still drifting late is evidence, not a cosmetic issue."""
    assert "LEAD WITH THE TITLE" in STABLE
    assert "13. Title" in STABLE
    assert "the proposal drifted from its own problem statement" in STABLE


def test_a_confirmed_title_renames_the_project(tmp_path, monkeypatch):
    """One title, shown in the proposal and in the project list. Two would
    diverge the moment either changed."""
    import sqlite3
    db = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db))
    web_app._init_profiles_db()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'Untitled project')")
    con.commit(); con.close()

    web_app._save_proposal(1, {"title": "Contestability in Benefits Appeals"})

    con = sqlite3.connect(db)
    assert con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] \
        == "Contestability in Benefits Appeals"
    con.close()


def test_saving_another_section_does_not_resurrect_an_old_title(tmp_path, monkeypatch):
    """Someone renames the project from the projects page, then the advisor
    saves methodology. The rename has to survive that."""
    import sqlite3
    db = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db))
    web_app._init_profiles_db()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'Untitled project')")
    con.commit(); con.close()

    web_app._save_proposal(1, {"title": "First Title"})
    con = sqlite3.connect(db)
    con.execute("UPDATE projects SET title = 'Their Own Rename' WHERE id = 1")
    con.commit(); con.close()

    web_app._save_proposal(1, {"methodology": "- Interviews."})

    con = sqlite3.connect(db)
    assert con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] \
        == "Their Own Rename"
    con.close()


def test_a_runaway_title_cannot_become_the_projects_name(tmp_path, monkeypatch):
    """If the model misreads the field and writes prose into it, that prose
    would otherwise be the project's name everywhere it is listed."""
    import sqlite3
    db = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db))
    web_app._init_profiles_db()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO profiles (id, name) VALUES (1, 'Jane')")
    con.execute("INSERT INTO projects (id, profile_id, title) VALUES (1, 1, 'Untitled project')")
    con.commit(); con.close()

    web_app._save_proposal(1, {"title": "word " * 400})

    con = sqlite3.connect(db)
    stored = con.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0]
    con.close()
    assert len(stored) <= web_app._TITLE_MAX


def test_the_document_leads_with_the_projects_own_title(tmp_path):
    """It used to lead with "Research Proposal: <name>", which named the author
    and not the work."""
    from docx import Document
    import io
    docx = web_app._build_proposal_docx(
        "Jane Doe", {"title": "Contestability in Benefits Appeals", "abstract": "A."})
    paras = [p.text for p in Document(io.BytesIO(docx)).paragraphs if p.text.strip()]
    assert paras[0] == "Contestability in Benefits Appeals"
    assert paras[1] == "Jane Doe"


def test_an_untitled_proposal_still_renders():
    """Downloads happen mid-conversation, before a title is settled."""
    from docx import Document
    import io
    docx = web_app._build_proposal_docx("Jane Doe", {"abstract": "A."})
    paras = [p.text for p in Document(io.BytesIO(docx)).paragraphs if p.text.strip()]
    assert paras[0] == "Research Proposal: Jane Doe"


def test_importance_and_benefits_is_required_somewhere():
    """Folded into background rather than given its own section, so the
    requirement has to be visible in background's own instructions."""
    assert "IMPORTANCE AND BENEFITS" in SAVE_PROPOSAL["background"]["description"]


def test_references_are_generated_rather_than_written():
    """Bamshad lists References; we build them from the works the novelty
    search actually returned instead of asking the model to recall citations."""
    from docx import Document
    docx = web_app._build_proposal_docx(
        "Jane Doe", {"abstract": "A."},
        references=[{"title": "Contesting risk scores", "authors": ["Lee, A."], "year": 2024}])
    import io
    text = "\n".join(p.text for p in Document(io.BytesIO(docx)).paragraphs)
    assert "References" in text
    assert "Contesting risk scores" in text


# ── Stage deliverables ─────────────────────────────────────────────────────

def test_the_problem_statement_is_three_to_four_paragraphs():
    """Bamshad's stage 1 output is "a 3-4 paragraph clear and concise statement
    of a research problem including properly framed context and research
    objectives". Ours asked for one paragraph until this was checked."""
    described = SAVE_PROPOSAL["problem_statement"]["description"]
    assert "THREE TO FOUR PARAGRAPHS" in described
    assert "a single paragraph is not enough" in described


@pytest.mark.parametrize("element", [
    "background that motivates",       # motivation and real-world impact
    "field of study",                  # context
    "scope",                           # scope
    "how the problem has been addressed before",
    "research objectives",
])
def test_the_problem_statement_covers_each_element_of_the_rubric(element):
    assert element in SAVE_PROPOSAL["problem_statement"]["description"]


@pytest.mark.parametrize("question", [
    "clear and unambiguous",
    "importance, its real-world impact",
    "Is the context clear",
    "what gap remains",
    "specific novel approach",
])
def test_the_five_assessment_questions_are_in_the_prompt(question):
    """The rubric the statement is judged on, stated where the advisor drafts
    it rather than left implicit."""
    assert question in STABLE


def test_stage_four_opens_with_the_two_page_outline():
    """Bamshad's stage 2 deliverable is a two-page outline of seven elements,
    produced before sections are developed. We had no such checkpoint: the
    advisor went straight from the problem statement into section-by-section
    drafting, so the proposal had no whole-shape draft at any point."""
    assert "FIRST, THE OUTLINE" in STABLE
    assert "roughly two pages" in STABLE


@pytest.mark.parametrize("element", [
    "general problem and the motivation",
    "specific research problem in its particular context",
    "What others have done",
    "knowledge or research gap",
    "key research objectives",
    "research questions that would guide",
    "expected results",
])
def test_the_outline_covers_all_seven_elements(element):
    assert element in STABLE


def test_the_outline_is_not_saved_as_proposal_sections():
    """It is a sketch meant to be thrown away. Saving it would fill the panel
    with text that never went through the section-by-section work."""
    assert "DO NOT save proposal sections from the outline" in STABLE


def test_methods_are_mapped_to_each_research_question():
    """Bamshad's methodology has three parts, and the middle one — "specific
    methods related to each research question" — is the one proposals skip. A
    question with no method is unanswerable as written; a method answering no
    question is scope nobody needs."""
    assert "THE METHOD FOR EACH RESEARCH QUESTION OR HYPOTHESIS, mapped one to one" in STABLE
    assert "A method that answers no question is scope" in STABLE


def test_the_data_analysis_approach_is_required_separately():
    assert "the data analysis approach" in STABLE


def test_hypotheses_stay_optional():
    """"Research questions and/or hypotheses" — and this roster runs from
    neuroscience to screenwriting. A hypothesis invented to fill a heading is
    worse than an empty section."""
    described = SAVE_PROPOSAL["hypotheses"]["description"]
    assert "OPTIONAL" in described
    assert "empty section is better" in described
    assert "humanistic" in STABLE


def test_assumptions_and_delimitations_are_kept_apart():
    """They are different things. Assumptions are what must be true;
    delimitations are the boundaries drawn on purpose."""
    described = SAVE_PROPOSAL["assumptions_delimitations"]["description"]
    assert "ASSUMPTIONS" in described and "DELIMITATIONS" in described
    assert "most work" in described


def test_the_plan_of_work_carries_a_schedule_and_deliverables():
    """"Expected outcomes" alone never gave us either."""
    described = SAVE_PROPOSAL["plan_of_work"]["description"]
    assert "schedule" in described and "deliverable" in described


# ── The nineteen lenses ────────────────────────────────────────────────────

# One distinctive phrase per lens. Chosen to be specific enough that a lens
# deleted in a rewrite fails here rather than passing on an incidental word.
LENSES = {
    1:  "CORE PHENOMENON",
    2:  "RESEARCH CLAIM",
    3:  "deeper consequence hiding inside",
    4:  "SCOPE AND BOUNDARIES",
    5:  "MECHANISM",
    6:  "COMPETING EXPLANATIONS",
    7:  "which does the MOST WORK",
    8:  "GENUINE UNCERTAINTY",
    9:  "CAUSAL STRUCTURE",
    10: "OPERATIONALIZATION AND MEASUREMENT",
    11: "UNIT AND LEVEL OF ANALYSIS",
    12: "DISCRIMINATING EVIDENCE",
    13: "FALSIFIABILITY",
    14: "empirically indistinguishable",
    15: "CONTRADICTIONS AND ANOMALIES",
    16: "CONTRIBUTION",
    17: "SKEPTICAL REVIEWER",
    18: "EXPERT-ONLY KNOWLEDGE",
    19: "THE MISSING DIMENSION",
}


@pytest.mark.parametrize("number,phrase", sorted(LENSES.items()))
def test_every_lens_is_present(number, phrase):
    assert phrase in STABLE, f"lens {number} is missing from the prompt"


def test_the_lenses_are_silent():
    """The failure mode they introduce. A researcher should meet a colleague
    who asks unusually good questions, not a rubric applied to them."""
    assert "never spoken aloud" in STABLE
    assert "Never name a lens" in STABLE
    assert "LENS LEAK" in STABLE


def test_the_lenses_still_produce_one_question():
    """The lens list is the strongest pull toward interrogation in the whole
    prompt, so it has to restate the limit itself."""
    assert "never ask about more than one in a turn" in STABLE


def test_the_lenses_do_not_licence_supplying_answers():
    """Competing explanations and discriminating evidence both tempt the model
    to hand over candidate content, which is what the interview rule forbids
    and what breaks the collaborator matching."""
    assert "THE STAGE 1 CONSTRAINT BINDS HERE TOO" in STABLE
    assert 'not "could it be selection or drift?" but "what else could produce that pattern?"' in STABLE


def test_the_feasibility_lens_does_not_reopen_stage_one_methods():
    """Lens 14 asks what data could realistically answer this. Stage 1 forbids
    exactly that question, because asking what data they have while the problem
    is vague reshapes the problem to fit the data. The stage rule wins, and the
    prompt has to say which."""
    assert "SOME LENSES ARE STAGE-GATED, and the stage rules win" in STABLE
    assert '"what would count as evidence" is a Stage 1 question' in STABLE


def test_competing_explanations_are_checked_against_the_literature():
    """Bamshad's own note on that lens. The search is the evidence, not the
    advisor's guess."""
    assert "CHECKED AGAINST THE LITERATURE in Stage 2" in STABLE


def test_expert_only_questions_are_preferred_not_merely_allowed():
    """The lens that most changes what a turn is worth. The prompt already
    banned asking what could be searched; it never stated the positive form."""
    assert "PREFER THESE ABOVE ALL OTHERS" in STABLE
    assert "If you could find out yourself, find out yourself" in STABLE
    assert "unpublished observation" in STABLE or "unpublished observations" in STABLE


def test_the_send_check_enforces_the_expert_only_preference():
    """A rule stated once in a 70KB prompt is a rule that gets skipped. The
    send check is what runs on every message."""
    assert "could only {name} answer".replace("{name}", "Jane") in STABLE


# ── Stage-conditional assembly ─────────────────────────────────────────────

def test_stage_one_does_not_carry_the_proposal_building_rules():
    """The reason this is more than a cost saving. Stage 4 is where the stance
    inverts and putting options on the table becomes correct; Stage 1 is where
    doing that destroys the interview. Rules it must not follow yet are simply
    not in the prompt."""
    p = _stage_prompt("1-2")
    assert "STAGE 4 — BUILD THE PROPOSAL\n" not in p
    assert "BE A COLLABORATOR, NOT AN INTAKE FORM" not in p
    assert "Make concrete suggestions and let them react" not in p


def test_stage_four_does_not_carry_the_stage_one_interview():
    """The mirror. By Stage 4 the problem is settled and saved, and the rules
    that forbid suggesting a direction no longer apply."""
    p = _stage_prompt("4")
    assert "STAGE 1 — SPECIFY THE RESEARCH PROBLEM" not in p
    assert "STAGE 2 — TEST WHETHER IT IS NOVEL" not in p


def test_the_collaborator_search_only_ships_once_there_is_a_proposal():
    """Searching for people is the last thing that happens, and its rules are
    long. Nothing before Stage 4 can act on them."""
    assert "search_faculty" not in _stage_prompt("1-2")
    assert "search_faculty" in _stage_prompt("4")


@pytest.mark.parametrize("stage", ["1-2", "3", "4"])
def test_the_spine_reaches_every_stage(stage):
    """Rules that hold everywhere must not be stage-scoped by accident."""
    p = _stage_prompt(stage)
    assert "NEVER VALIDATE BY DEFAULT" in p       # how you think
    assert "THE LENSES" in p                       # the nineteen lenses
    assert "SEND CHECK" in p                       # runs on every message
    assert "NEVER CAPITULATE" in p                 # tone
    assert "OFFERING CHOICES AS BUTTONS" in p      # referenced from both sides
    assert "save_proposal" in p                    # saving happens throughout


@pytest.mark.parametrize("stage", ["1-2", "3", "4"])
def test_every_stage_still_knows_the_whole_map(stage):
    """A professor can ask "what happens after this?" at any point, and the
    advisor explains where it is going. The stage overview always ships."""
    assert "THE FOUR STAGES" in _stage_prompt(stage)


def test_each_stage_carries_the_next_one_for_a_mid_turn_handover():
    """Stage is derived once, at the top of a turn, but a turn can cross a
    boundary: the advisor saves a novelty claim and the project is in Stage 3
    from that moment while still holding the prompt it started with. Without
    the next stage's text it would improvise the handover."""
    assert "STAGE 3 — WRITE THE PROBLEM STATEMENT" in _stage_prompt("1-2")
    assert "STAGE 4 — BUILD THE PROPOSAL" in _stage_prompt("3")


def test_no_section_references_a_rule_that_is_not_shipped_with_it():
    """Sections cross-refer with "below". A reference whose target was cut is
    an instruction pointing at nothing."""
    for stage in ("1-2", "3", "4"):
        p = _stage_prompt(stage)
        if "the stuck-menu below" in p or "the operational reframe below" in p:
            assert "STAGE 1 — SPECIFY THE RESEARCH PROBLEM" in p
        if "the option block described below" in p:
            assert "OFFERING CHOICES AS BUTTONS" in p


def test_the_assembly_is_meaningfully_smaller_than_sending_everything():
    """If this stops being true the split is pure risk with no return."""
    import advisor_prompt as ap
    full = sum(len(ap._SECTION_TEXT[n]) for n in ap.SECTIONS)
    for stage in ("1-2", "3", "4"):
        sent = len(_stage_prompt(stage))
        assert sent < full * 0.80, f"stage {stage} saves less than 20%"


def test_the_prompt_for_a_stage_is_stable_across_turns():
    """Prompt caching bills a repeated prefix at a tenth of the rate, and only
    survives if the bytes are identical. Two turns in the same stage must
    assemble the same text."""
    assert _stage_prompt("1-2") == _stage_prompt("1-2")


def test_an_unknown_stage_is_refused_rather_than_silently_empty():
    import advisor_prompt as ap
    with pytest.raises(ValueError, match="unknown stage"):
        ap.stable(name="Jane", stage_line="X", stage="99")
    with pytest.raises(ValueError, match="unknown stage"):
        ap.sections_for("99")


# ── Never volunteer an idea ────────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["1-2", "3", "4"])
def test_asking_before_offering_holds_at_every_stage(stage):
    """Reported by the user: it kept suggesting instead of asking. The rule is
    in the spine, so it reaches every stage including Stage 4, where the
    stance inverts but does not become a licence to volunteer."""
    p = _stage_prompt(stage)
    assert "ASK BEFORE YOU OFFER" in p
    assert "You do not volunteer ideas" in p
    assert "Wanting to be asked is not asking" in p


@pytest.mark.parametrize("stage", ["1-2", "3", "4"])
def test_the_prompt_no_longer_prefers_a_recommendation_over_a_question(stage):
    """The rule that caused it. "Prefer a reasoned recommendation over another
    question" shipped in the spine — at EVERY stage — directly contradicting
    "NEVER HAND THEM POSSIBLE ANSWERS" two sections later. Its worked example
    supplied three candidate interpretations and picked one, which is exactly
    the behaviour the interview forbids."""
    p = _stage_prompt(stage)
    assert "Prefer a reasoned recommendation over another question" not in p
    assert "I see three functional interpretations here" not in p


def test_not_knowing_yet_is_treated_as_the_start_of_the_interview():
    """"I don't know" used to trigger automatic ideation. It usually means
    they have not been asked the right question yet."""
    p = _stage_prompt("1-2")
    assert "do NOT start offering directions" in p
    assert "not a request for suggestions" in p


def test_being_stuck_still_requires_asking_first():
    """The one place the old prompt and the new one agreed; now nothing
    contradicts it."""
    p = _stage_prompt("1-2")
    assert "ASK whether they want options rather than producing them" in p
    assert "ASK WHETHER THEY WANT OPTIONS" in p       # the option-block section


def test_stage_four_asks_for_their_method_before_proposing_one():
    """An experienced researcher usually has an approach half-formed. Taking
    theirs seriously beats replacing it, and the Stage 4 inversion lowers the
    bar for offering rather than removing it."""
    p = _stage_prompt("4")
    assert "start by asking how they would approach it" in p
    assert "Ask first, then offer" in p
    assert "lowers the bar for offering; it does not remove it" in p


def test_an_explicit_request_is_still_answered():
    """The whole point is that being asked licenses offering. A prompt that
    refused even then would be useless to somebody who genuinely wants help."""
    p = _stage_prompt("1-2")
    assert "The exception is an explicit request" in p
    assert "what could AI even do here?" in p


def test_synthesis_is_not_a_loophole():
    """"Stop asking and synthesize" was the wording that licensed supplying
    options. Synthesis has to mean summarising THEIR material."""
    p = _stage_prompt("1-2")
    assert "Synthesis is not suggestion" in p
    assert "contains no option you invented" in p


def test_the_send_check_catches_an_unsolicited_offer():
    """A rule stated once in a 15,000-token prompt is a rule that gets
    skipped. The send check runs on every drafted message."""
    p = _stage_prompt("1-2")
    assert "DID YOU OFFER SOMETHING THEY DID NOT ASK FOR?" in p
    assert '"they seemed stuck" is not a licence' in p


def test_the_questions_get_better_rather_than_fewer():
    """The opposite failure: pure question-and-answer ping-pong with nothing
    given back. The fix is better questions, not filling the gap with
    suggestions."""
    p = _stage_prompt("1-2")
    assert "DO NOT INTERROGATE" in p
    assert "never by filling the gap with suggestions" in p
    assert "Reflect before you ask" in p


def test_the_title_must_be_saved_not_merely_announced():
    """Announcing a title in the chat does not name the project — the project
    list reads the saved value. A title only mentioned leaves the entry as
    "Untitled project" while the conversation plainly has a subject."""
    p = _stage_prompt("1-2")
    assert "CALL save_proposal WITH IT IN THE SAME TURN" in p
    assert "Saying the title in the chat is not naming the project" in p


# ── Candidate lists inside questions ───────────────────────────────────────

def test_the_objective_taxonomy_is_applied_silently():
    """Rule B used to say "help them say which kind of aim it is: descriptive,
    evaluative, or interventional" — the prompt instructing a three-item menu
    while another section bans menus. They pick one instead of saying what
    they want."""
    p = _stage_prompt("1-2")
    assert "Work out SILENTLY which kind of aim it is" in p
    assert "is a three-item menu" in p


def test_the_operational_reframe_cannot_carry_a_list():
    """Observed in production: the reframe's own "In other words" opener was
    used to wrap four candidate answers onto a question that was fine until
    that point."""
    p = _stage_prompt("1-2")
    assert "THE REFRAME IS NOT A CONTAINER FOR A LIST" in p
    assert "It adds no nouns they did not say" in p


def test_the_advisor_does_not_announce_which_part_is_weak():
    """"The part that needs the most pressure is X" grades the answer, and the
    sentence that follows it usually enumerates the alternatives."""
    p = _stage_prompt("1-2")
    assert "announcing WHERE the weakness is before asking about it" in p
    assert "the choice of what you asked about already shows what you think is soft" in p


@pytest.mark.parametrize("stage", ["1-2", "3", "4"])
def test_the_list_scan_runs_at_every_stage(stage):
    """It was scoped to "Stage 1 digs only", which is an out — and the message
    that prompted this was a Stage 1 dig anyway."""
    p = _stage_prompt(stage)
    assert "RUN THIS ON EVERY MESSAGE, NOT JUST STAGE 1 DIGS" in p
    assert "two or more comma-separated things that could themselves BE the answer" in p


def test_the_list_scan_shows_the_edit_rather_than_describing_it():
    """A rule the model has to interpret is weaker than one it can copy."""
    p = _stage_prompt("1-2")
    assert 'is sent as "What would you see happening?"' in p


def test_not_knowing_the_answer_counts_as_asking_for_options():
    """The user's own refinement: somebody who replies "I have no idea" or
    "what do you mean?" has been asked and could not answer, which is exactly
    when options help rather than lead."""
    p = _stage_prompt("1-2")
    assert "Answering your question with not-knowing" in p
    assert '"I have no idea"' in p
    assert "can you give an example?" in p


def test_one_licence_to_offer_is_not_a_standing_one():
    """Otherwise a single "I don't know" turns the rest of the conversation
    into a suggestion engine."""
    assert "One licence is not a standing one" in _stage_prompt("1-2")
