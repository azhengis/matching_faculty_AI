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


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"advisor prompt section missing: {path}. The prompt is assembled from "
            f"files on disk; a missing one would otherwise produce an advisor that "
            f"works but has a hole in it. Check that prompts/ ships with the app."
        ) from exc


def _load_stable_template() -> str:
    """Every section, concatenated, with placeholders still in place."""
    return "".join(_read(_STABLE_DIR / f"{n}.md") for n in SECTIONS)


def _load_volatile_template() -> str:
    return _read(_VOLATILE)


# Read once. The stable half is what prompt caching bills at a tenth of the
# rate, and a cache prefix only survives if the bytes are identical every turn,
# so re-reading per request would be both wasteful and a correctness risk if a
# file were edited mid-conversation. Restart to pick up an edit.
_STABLE_TEMPLATE = _load_stable_template()
_VOLATILE_TEMPLATE = _load_volatile_template()


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


def stable(name: str, stage_line: str) -> str:
    """The instruction half: every rule, and the one line saying which stage."""
    return _fill(_STABLE_TEMPLATE, {"name": name, "stage_line": stage_line})


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
    return placeholders(_STABLE_TEMPLATE)


def volatile_placeholders() -> set:
    return placeholders(_VOLATILE_TEMPLATE)
