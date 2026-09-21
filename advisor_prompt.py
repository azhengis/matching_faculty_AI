"""Assembles the advisor's system prompt from the files in prompts/.

The prompt used to be one 72KB f-string inside web_app.py. It stopped being
editable somewhere around 50KB: sections drifted into contradicting each other,
the stage rules and the lens rules disagreed about when feasibility could be
raised, and an indentation slip inside it took the advisor down for every
project past Stage 2 without anything failing loudly. None of that is unusual
for a document that long living inside a function.

So the instructions are now ordinary text files that anyone on the team can
edit without touching Python, and this module puts them back together.

    prompts/advisor/*.md    the instruction half, one file per section
    prompts/advisor_volatile.md   the live proposal and profile

TWO RULES GOVERN THE SPLIT.

First, ORDER IS FIXED BY SECTIONS, not by the filesystem. Alphabetical filename
order would work until somebody added `tone_extra.md`, and the failure would be
a silently reordered prompt rather than an error. Adding a section means adding
it to SECTIONS below, deliberately.

Second, A MISSING FILE IS FATAL. This is the important one. If a section fails
to ship — left out of the Docker image, lost in a merge — the natural failure
is not a crash but a working advisor with a hole in it: no send check, or no
novelty stage, and nobody notices for weeks. So the whole set is verified at
import, and a missing file stops the process at startup instead.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "prompts"
_STABLE_DIR = _ROOT / "advisor"
_VOLATILE = _ROOT / "advisor_volatile.md"

# The instruction half, in the order it is sent to the model. The numeric
# prefixes are a reading aid; this list is what actually decides the order.
SECTIONS = [
    "00_role",
    "01_how_you_think",
    "02_lenses",
    "03_stages_and_opening",
    "04_stage1_specify",
    "05_stage2_novelty",
    "06_stage3_statement",
    "07_stage4_proposal",
    "08_stage4_stance",
    "09_option_blocks",
    "10_saving",
    "11_collaborators",
    "12_tone",
    "13_send_check",
]

# ── Which sections a given stage actually needs ──────────────────────────────
#
# Sending all fourteen every turn meant a project in Stage 1 carried the whole
# Stage 4 proposal-building apparatus — 4,500 tokens of section-by-section
# drafting instructions it will not touch for an hour. Worse than the cost: the
# advisor could read forward into rules that do not apply yet, and Stage 1 is
# exactly where reading ahead does damage, because the Stage 4 stance is the
# one that permits putting options on the table.
#
# SPINE goes out every turn. These are the rules that hold everywhere: who it
# is, how it thinks, the lenses, the stage map, how choices are offered, how
# sections are saved, tone, and the send check.
SPINE = [
    "00_role", "01_how_you_think", "02_lenses", "03_stages_and_opening",
    "09_option_blocks", "10_saving", "12_tone", "13_send_check",
]

# Then the stage's own instructions, PLUS THE NEXT STAGE'S. The lookahead is
# not padding. Stage is derived once, at the top of a turn, but a turn can
# cross a boundary: the advisor saves a novelty claim and the project is in
# Stage 3 from that moment, still holding the prompt it started with. Without
# the next stage's text it would have to improvise the handover, which is the
# one moment the interview is most likely to lose its shape.
STAGE_SECTIONS = {
    "1-2": ["04_stage1_specify", "05_stage2_novelty", "06_stage3_statement"],
    "3":   ["06_stage3_statement", "07_stage4_proposal", "08_stage4_stance"],
    "4":   ["07_stage4_proposal", "08_stage4_stance", "11_collaborators"],
}

STAGES = tuple(STAGE_SECTIONS)


def sections_for(stage: str) -> list:
    """The section names sent for `stage`, in document order.

    Order comes from SECTIONS, never from the order they are listed above, so
    a section moved in the reading order moves in every stage at once.
    """
    if stage not in STAGE_SECTIONS:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    wanted = set(SPINE) | set(STAGE_SECTIONS[stage])
    return [s for s in SECTIONS if s in wanted]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"advisor prompt section missing: {path}. The prompt is assembled from "
            f"files on disk; a missing one would otherwise produce an advisor that "
            f"works but has a hole in it. Check that prompts/ ships with the app."
        ) from exc


def _load_sections() -> dict:
    """Every section's text, keyed by name, read once at import.

    Loading ALL of them even though a turn sends a subset is deliberate: the
    missing-file check has to cover the whole set, or a section that only
    Stage 4 uses could be absent for weeks before anyone reached Stage 4.
    """
    return {n: _read(_STABLE_DIR / f"{n}.md") for n in SECTIONS}


def _load_volatile_template() -> str:
    return _read(_VOLATILE)


# Read once. The stable half is what prompt caching bills at a tenth of the
# rate, and a cache prefix only survives if the bytes are identical every turn,
# so re-reading per request would be both wasteful and a correctness risk if a
# file were edited mid-conversation. Restart to pick up an edit.
_SECTION_TEXT = _load_sections()
_VOLATILE_TEMPLATE = _load_volatile_template()

# One assembled template per stage, built once. Each is byte-identical for
# every turn spent in that stage, which is what lets the provider cache it.
# The prefix changes when the stage does — three times over a whole project,
# against a ~25% smaller prompt on every turn in between.
_STABLE_TEMPLATES = {
    st: "".join(_SECTION_TEXT[n] for n in sections_for(st)) for st in STAGES
}


def _fill(template: str, values: dict) -> str:
    """Substitute {placeholders}.

    str.format is deliberately not used. The prompt is full of literal braces
    the moment somebody writes a JSON example or a set, and format would raise
    on those — or worse, silently consume them. Plain replacement only touches
    the names we know about.
    """
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def stable(name: str, stage_line: str, stage: str) -> str:
    """The instruction half for one stage, plus the line saying which it is."""
    if stage not in _STABLE_TEMPLATES:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
    return _fill(_STABLE_TEMPLATES[stage],
                 {"name": name, "stage_line": stage_line})


def volatile(**values) -> str:
    """The data half: the live proposal, profile, publications, documents."""
    return _fill(_VOLATILE_TEMPLATE, values)


def placeholders(template: str) -> set:
    """Every {placeholder} in a template. Used by the tests to prove that what
    the files ask for and what web_app supplies are the same set — an unfilled
    placeholder would reach the model as a literal '{gaps}'."""
    import re
    return set(re.findall(r"\{(\w+)\}", template))


def stable_placeholders() -> set:
    """Across every stage, so a placeholder only used by one is not missed."""
    out = set()
    for text in _STABLE_TEMPLATES.values():
        out |= placeholders(text)
    return out


def volatile_placeholders() -> set:
    return placeholders(_VOLATILE_TEMPLATE)
