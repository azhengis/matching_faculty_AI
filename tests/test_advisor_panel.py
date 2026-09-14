"""The proposal panel: which side it sits on, and hiding it.

The panel moved from the left of the chat to the right, and gained a collapse
control. Both are template-only changes, so these are structural tests — there
is no JS runner here. What they guard is the pair of states that can hide the
panel and must not be confused:

    no `visible`   there is nothing to show yet; not the reader's decision
    `collapsed`    the reader hid it; theirs to undo, and remembered

Conflating them is the bug to prevent. If a newly saved section cleared the
collapse, the panel would reappear every time the advisor wrote anything, which
is precisely what someone who hid it does not want.
"""
from pathlib import Path

import pytest

import web_app

TEMPLATE = (Path(web_app.__file__).parent / "templates" / "advisor.html").read_text()


# ── Which side it is on ────────────────────────────────────────────────────

def test_the_panel_comes_after_the_chat_in_the_document():
    """Right-hand placement is done by DOM order, not a flex `order` override,
    so reading order and tab order match what is on screen."""
    assert TEMPLATE.index('id="chat-wrap"') < TEMPLATE.index('id="proposal-sidepanel"')


def test_the_panel_is_bordered_on_the_side_that_faces_the_chat():
    assert "border-left:1px solid var(--border)" in TEMPLATE
    assert "border-right:1px solid var(--border);background:var(--paper)" not in TEMPLATE


def test_it_animates_in_from_the_right():
    """Sliding in from the left while sitting on the right reads as a glitch."""
    assert "translateX(12px)" in TEMPLATE
    assert "translateX(-12px)" not in TEMPLATE


def test_the_narrow_layout_stacks_the_chat_above_the_panel():
    """column-reverse was correct while the panel came first in the DOM. After
    the move it would put the panel back on top, above the conversation."""
    assert ".advisor-layout{flex-direction:column}" in TEMPLATE
    assert "flex-direction:column-reverse}" not in TEMPLATE


def test_the_narrow_layout_drops_the_vertical_border():
    assert "width:100%;border-left:none;border-top:1px solid var(--border)" in TEMPLATE


# ── The collapse control ───────────────────────────────────────────────────

def test_there_is_a_toggle_and_it_is_outside_the_panel():
    """A close button inside the panel would vanish with it, leaving no way
    back. It lives in the project bar, which is always on screen."""
    assert 'id="pb-toggle-panel"' in TEMPLATE
    bar_start = TEMPLATE.index('class="project-bar"')
    bar_end = TEMPLATE.index('id="loading"')
    assert bar_start < TEMPLATE.index('id="pb-toggle-panel"') < bar_end


def test_the_toggle_is_hidden_until_there_is_a_panel_to_toggle():
    assert 'id="pb-toggle-panel" type="button" style="display:none"' in TEMPLATE
    assert "btn.style.display = 'inline-flex'" in TEMPLATE


def test_the_toggle_reports_its_state_to_assistive_tech():
    assert 'aria-controls="proposal-sidepanel"' in TEMPLATE
    assert "setAttribute('aria-expanded'" in TEMPLATE


def test_the_two_hidden_states_are_distinct_rules():
    """`visible` is whether there is anything to show; `collapsed` is the
    reader's choice. The collapsed rule requires both classes, so it can only
    ever hide a panel that would otherwise be up."""
    assert ".proposal-sidepanel.visible.collapsed{display:none}" in TEMPLATE
    assert ".proposal-sidepanel.visible{display:block" in TEMPLATE


def test_new_content_never_re_opens_a_collapsed_panel():
    """Every place content lands calls showProposalPanel, which re-applies the
    stored preference rather than forcing the panel open."""
    assert TEMPLATE.count("showProposalPanel()") >= 4
    assert "applyPanelState(panelCollapsedPref())" in TEMPLATE


def test_nothing_reveals_the_panel_behind_the_helpers_back():
    """One direct add, inside showProposalPanel itself. Any other would bypass
    the collapse check."""
    assert TEMPLATE.count("classList.add('visible')") == 1


def test_the_preference_survives_a_reload():
    assert "advisor_panel_collapsed" in TEMPLATE
    assert "localStorage.setItem" in TEMPLATE and "localStorage.getItem" in TEMPLATE


def test_storage_failure_does_not_break_the_page():
    """localStorage throws in a private window. A panel that cannot remember
    its state is fine; a page that will not load is not."""
    for snippet in ["try { return localStorage.getItem(PANEL_PREF) === '1'; } catch (e) { return false; }",
                    "try { localStorage.setItem(PANEL_PREF, collapsed ? '1' : '0'); } catch (e) {}"]:
        assert snippet in TEMPLATE


# ── The state machine ──────────────────────────────────────────────────────

class _Panel:
    """Python mirror of the template's three functions, to exercise the
    transitions the structural assertions above cannot."""

    def __init__(self, stored=None):
        self.classes, self.store, self.btn_shown = set(), stored, False

    def _pref(self):
        return self.store == "1"

    def _apply(self, collapsed):
        self.classes.discard("collapsed") if not collapsed else self.classes.add("collapsed")

    def show(self):
        self.classes.add("visible")
        self.btn_shown = True
        self._apply(self._pref())

    def toggle(self):
        collapsed = "collapsed" not in self.classes
        self._apply(collapsed)
        self.store = "1" if collapsed else "0"

    @property
    def on_screen(self):
        return "visible" in self.classes and "collapsed" not in self.classes


def test_the_panel_appears_when_the_first_section_lands():
    p = _Panel()
    assert not p.on_screen
    p.show()
    assert p.on_screen and p.btn_shown


def test_hiding_then_showing_returns_to_where_it_started():
    p = _Panel(); p.show()
    p.toggle(); assert not p.on_screen
    p.toggle(); assert p.on_screen


def test_a_section_saved_while_collapsed_leaves_it_collapsed():
    """The reason the two states are separate. Otherwise the panel springs back
    every time the advisor saves anything."""
    p = _Panel(); p.show(); p.toggle()
    assert not p.on_screen
    p.show()                      # a new section arrives
    assert not p.on_screen


def test_a_collapsed_panel_is_still_collapsed_after_a_reload():
    p = _Panel(); p.show(); p.toggle()
    reloaded = _Panel(stored=p.store)
    reloaded.show()
    assert not reloaded.on_screen
    assert reloaded.btn_shown, "the way back must still be on screen"


def test_an_unset_preference_leaves_the_panel_open():
    p = _Panel(stored=None); p.show()
    assert p.on_screen
