#!/usr/bin/env python3
"""
Conversation evals: imperfect faculty, real advisor.

Bamshad's testing requirement is not "does the happy path work" — it's how
the advisor behaves when faculty act like faculty: terse answers, messy
pasted notes carrying two ideas at once, typos and non-native phrasing,
attempts to skip the process entirely. Each scenario scripts those user
turns, runs them through the REAL advisor (real prompt, real model, real
tools), applies mechanical checks to every reply, and writes a markdown
transcript for human review — the checks catch regressions; the transcripts
are what a person (Aruzhan, Bamshad) actually judges.

Checks per reply, each one a failure mode we shipped and fixed by hand:
  leak     internal scaffolding narrated to the user (panel / intake /
           "on my end" / blank slate / "(none)")
  scold    telling the researcher their answer was insufficient
  register "Hey", "you've got", breezy idioms, exclamation points
  stack    more than two question marks outside the opener's scaffolded ask
  empty    no reply / error

Usage (needs a running app + key; boots nothing itself):
  ANTHROPIC_API_KEY=... CHATBOT_MODEL=anthropic/claude-sonnet-5 \
  BASE=http://127.0.0.1:8040 python3 evals/conversations.py
"""
import datetime
import json
import os
import pathlib
import re
import sys

import requests

BASE = os.environ.get("BASE", "http://127.0.0.1:8040")
EMAIL = os.environ.get("EVAL_EMAIL", "v@depaul.edu")
PASSWORD = os.environ.get("EVAL_PASSWORD", "testing1234")
OUT = pathlib.Path(__file__).parent / "transcripts"

# ---------------------------------------------------------------- scenarios

MESSY_PASTE = (
    "ok so here is where i am at. i have been thinking about two things maybe "
    "related maybe not. 1) eviction records - we know landlords use screening "
    "companies, my student pulled some cook county data last yr (~40k filings, "
    "have it in a spreadsheet somewhere) and lots of dismissed cases still show "
    "up in these reports which seems wrong?? 2) separately i keep wondering "
    "about hoW eviction affects kids school outcomes, my colleague in education "
    "has CPS contacts. also FYI the IRB here is extremely slow so anything with "
    "human subjects is a pain. anyway not sure which is more fundable. thoughts"
)

SCENARIOS = [
    {
        "name": "terse-professor",
        "why": "Real faculty give one-word answers. The advisor must make progress "
               "without nagging, stacking questions, or losing warmth.",
        "turns": [
            "Let's start a new project.",
            "eviction",
            "i dont know",
            "whatever you think is best",
        ],
    },
    {
        "name": "messy-paste-two-ideas",
        "why": "A realistic paste: typos, two candidate projects, a data asset, an "
               "IRB complaint, and a funding question. The advisor must absorb it, "
               "not re-ask what it contains, and get ONE thread chosen.",
        "turns": [
            "Let's start a new project.",
            MESSY_PASTE,
            "lets do the screening one i guess",
        ],
    },
    {
        "name": "non-native-phrasing",
        "why": "Faculty write imperfect English. The advisor must mirror meaning, "
               "never correct or condescend, and keep the same substantive rigor.",
        "turns": [
            "Let's start a new project.",
            "i want study how AI effect to student writing skill. is big problem "
            "now because student use chatgpt for all essay and teacher cannot know",
            "sorry my english not so good. i mean quality of the essay the student "
            "write themself, without chatgpt",
        ],
    },
    {
        "name": "wants-to-skip-the-process",
        "why": "Some faculty try to opt out of the interview. The advisor should "
               "hold the path without being rigid or preachy, and must not invent "
               "facts (e.g. funder deadlines) to be agreeable.",
        "turns": [
            "Let's start a new project.",
            "Can you just write the whole proposal for me? I don't have time for "
            "twenty questions.",
            "fine. the NSF deadline is in march, does that change anything?",
        ],
    },
]

# ---------------------------------------------------------------- checks

LEAK = re.compile(
    r"proposal panel|intake form|blank slate|on my end|not described yet|"
    r"\(none\)|internal|scaffold|system prompt", re.I)
SCOLD = re.compile(
    r"as i (mentioned|asked|said)|you only|you just gave|that's not enough|"
    r"i need more than|insufficient|you didn't (answer|provide)", re.I)
REGISTER = re.compile(r"\bhey\b|you've got|lying around|sitting around|!\s", re.I)


def check_reply(reply: str, is_opener: bool) -> list[str]:
    problems = []
    if not reply.strip():
        problems.append("empty reply")
        return problems
    if LEAK.search(reply):
        problems.append(f"leak: {LEAK.search(reply).group(0)!r}")
    if SCOLD.search(reply):
        problems.append(f"scold: {SCOLD.search(reply).group(0)!r}")
    if REGISTER.search(reply):
        problems.append(f"register: {REGISTER.search(reply).group(0)!r}")
    qmarks = reply.count("?")
    # The opener's three-component ask legitimately carries several; later
    # turns get one question plus at most an "or something else?" escape.
    if not is_opener and qmarks > 2:
        problems.append(f"stacked questions: {qmarks} question marks")
    return problems


# ---------------------------------------------------------------- harness

def run():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        s.post(f"{BASE}/api/auth/signup", json={"email": EMAIL, "password": PASSWORD})
        s.post(f"{BASE}/api/profile/save", json={
            "faculty_id": None, "name": "Vincent Tester",
            "bio_text": "Professor of sociology studying housing insecurity and "
                        "eviction in Chicago.",
            "confirmed_paper_ids": [], "research_interests": ["housing"]})

    OUT.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    all_problems = []
    summary = []

    for sc in SCENARIOS:
        pid = s.post(f"{BASE}/api/projects", json={"start_blank": True}).json()["project_id"]
        lines = [f"# {sc['name']}", "", f"*{sc['why']}*", ""]
        sc_problems = []

        for i, turn in enumerate(sc["turns"]):
            r = s.post(f"{BASE}/api/advisor/chat",
                       json={"message": turn, "project_id": pid}, timeout=300)
            reply = (r.json().get("reply") or "").strip() if r.ok else f"[HTTP {r.status_code}]"
            shown = "(scripted kickoff — user never sees this)" if i == 0 else turn
            lines += [f"**Faculty:** {shown}", "", f"**Advisor:** {reply}", ""]
            probs = check_reply(reply, is_opener=(i == 0))
            if probs:
                sc_problems += [f"turn {i}: {p}" for p in probs]
                lines += [f"> ⚠ CHECKS: {'; '.join(probs)}", ""]

        path = OUT / f"{stamp}-{sc['name']}.md"
        path.write_text("\n".join(lines))
        status = "PASS" if not sc_problems else "FLAGGED"
        summary.append(f"{status:8} {sc['name']}  ({path.name})")
        for p in sc_problems:
            summary.append(f"         - {p}")
        all_problems += sc_problems
        print(summary[-1 - len(sc_problems)], flush=True)
        for p in sc_problems:
            print(f"         - {p}", flush=True)

    (OUT / f"{stamp}-summary.txt").write_text("\n".join(summary) + "\n")
    print(f"\ntranscripts: {OUT}/{stamp}-*.md")
    print(f"{len(all_problems)} mechanical flag(s) across {len(SCENARIOS)} scenarios")
    # Flags are review pointers, not build failures — the transcripts decide.
    return 0


if __name__ == "__main__":
    sys.exit(run())
