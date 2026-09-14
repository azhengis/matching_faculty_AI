"""The advisor prompt, assembled from files instead of one 72KB f-string.

Moving text out of code trades one failure mode for another. The old one was
that nobody could edit the prompt safely; the new one is that a section can go
missing and leave an advisor that still works, just without a send check, or
without the novelty stage. That failure is silent and would survive for weeks,
so these tests are mostly about making it loud.
"""
from pathlib import Path

import pytest

import advisor_prompt
import web_app

ROOT = Path(advisor_prompt.__file__).resolve().parent
STABLE_DIR = ROOT / "prompts" / "advisor"


# ── Every section is present and accounted for ─────────────────────────────

@pytest.mark.parametrize("section", advisor_prompt.SECTIONS)
def test_every_declared_section_exists_on_disk(section):
    assert (STABLE_DIR / f"{section}.md").is_file()


def test_no_section_file_is_left_out_of_the_order():
    """A file added to the directory but not to SECTIONS is silently never
    sent. The prompt would be missing whatever it contained, and nothing would
    fail — which is the whole reason SECTIONS is explicit."""
    on_disk = {p.stem for p in STABLE_DIR.glob("*.md")}
    assert on_disk == set(advisor_prompt.SECTIONS), (
        f"unordered: {sorted(on_disk - set(advisor_prompt.SECTIONS))}, "
        f"missing: {sorted(set(advisor_prompt.SECTIONS) - on_disk)}")


def test_no_section_is_empty():
    empty = [s for s in advisor_prompt.SECTIONS
             if not (STABLE_DIR / f"{s}.md").read_text().strip()]
    assert empty == [], f"empty prompt sections: {empty}"


def test_a_missing_section_is_fatal_rather_than_silently_dropped(tmp_path, monkeypatch):
    """The important one. A prompt with a hole in it still answers, which is
    why this has to raise instead of degrade."""
    monkeypatch.setattr(advisor_prompt, "_STABLE_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="advisor prompt section missing"):
        advisor_prompt._load_stable_template()


def test_the_sections_are_sent_in_the_declared_order():
    """Filename order and SECTIONS order agree today. If someone renames a file
    so alphabetical order diverges, SECTIONS is what wins, and this records
    that the two are deliberately kept in step."""
    assert advisor_prompt.SECTIONS == sorted(advisor_prompt.SECTIONS)

    built = advisor_prompt.stable(name="Jane", stage_line="X")
    positions = []
    for section in advisor_prompt.SECTIONS:
        text = (STABLE_DIR / f"{section}.md").read_text()
        head = text.replace("{name}", "Jane").replace("{stage_line}", "X").strip().splitlines()[0]
        positions.append(built.index(head))
    assert positions == sorted(positions), "sections are assembled out of order"


# ── Placeholders ───────────────────────────────────────────────────────────

def test_the_stable_half_asks_for_exactly_what_web_app_supplies():
    """An unfilled placeholder reaches the model as the literal text
    '{stage_line}', which it will cheerfully ignore."""
    assert advisor_prompt.stable_placeholders() == {"name", "stage_line"}


def test_the_volatile_half_asks_for_exactly_what_web_app_supplies():
    assert advisor_prompt.volatile_placeholders() == {
        "name", "project_title", "proposal_state", "gaps", "bio",
        "activities", "project", "paper_lines", "document_lines", "link_lines"}


def test_nothing_unfilled_survives_into_a_built_prompt():
    stable, volatile = web_app._advisor_system_prompt(
        {"name": "Jane Doe", "proposal": {"problem_statement": "P."}})
    import re
    # {name} and friends are gone; braces in prose (a set, a JSON example) are
    # fine, so only placeholder-shaped leftovers count.
    for half, text in (("stable", stable), ("volatile", volatile)):
        leftovers = set(re.findall(r"\{(\w+)\}", text))
        assert leftovers == set(), f"unfilled placeholders in {half}: {leftovers}"


def test_the_researchers_name_reaches_the_prompt():
    stable = advisor_prompt.stable(name="Ada Lovelace", stage_line="X")
    assert "Ada Lovelace" in stable
    assert "{name}" not in stable


def test_braces_in_a_researchers_name_do_not_break_assembly():
    """str.format would raise on this; plain replacement does not care."""
    stable = advisor_prompt.stable(name="Jane {Doe}", stage_line="X")
    assert "Jane {Doe}" in stable


# ── Wiring ─────────────────────────────────────────────────────────────────

def test_web_app_no_longer_carries_the_prompt_inline():
    """The point of the split. A 72KB string back in the module means someone
    edited the wrong copy and the files on disk are now dead text."""
    source = Path(web_app.__file__).read_text()
    assert "━━━ SEND CHECK" not in source
    assert "advisor_prompt.stable(" in source


def test_the_assembled_prompt_is_the_one_that_gets_sent():
    """Guards against the loader being correct but unused."""
    stable, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": {}})
    for section in advisor_prompt.SECTIONS:
        head = (STABLE_DIR / f"{section}.md").read_text()
        head = head.replace("{name}", "Jane").strip().splitlines()[0]
        assert head in stable, f"{section} never reaches the sent prompt"


def test_the_templates_are_read_once():
    """The stable half is billed at a tenth of the rate only while its bytes
    are identical every turn. Re-reading per request would also mean a mid-
    conversation edit silently invalidating the cache."""
    a, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": {}})
    b, _ = web_app._advisor_system_prompt({"name": "Jane", "proposal": {}})
    assert a == b
