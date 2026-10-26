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

Two layers of assessment:
  MECHANICAL checks, each a failure mode we shipped and fixed by hand:
    leak     internal scaffolding narrated to the user (panel / intake /
             "on my end" / blank slate / "(none)")
    scold    telling the researcher their answer was insufficient
    register "Hey", "you've got", breezy idioms, exclamation points
    stack    more than two question marks outside the opener's scaffolded ask
    empty    no reply / error
  A JUDGE — a second model that reads each scenario and grades the advisor's
    turns against the behavioural rules (one question, no menu of answers, no
    proposing, memory, register, brevity, stage order). It writes a per-turn
    report with the offending quote and a suggested fix. It is a review aid, not
    a gate: it is itself a model and can be wrong, so a human still reads the
    transcripts. Disable with JUDGE=0; pick its model with JUDGE_MODEL.

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


# ---------------------------------------------------------------- the judge
#
# The mechanical checks above catch only crude, pattern-matchable faults. The
# judge is a second model reading each scenario and grading the advisor's turns
# against the behavioural rules we enforce in the prompt and in code — the ones
# that are about meaning, not spelling: proposing instead of interviewing,
# re-asking something already answered, handing over a menu of answers. It is a
# review aid, not a gate: it is itself a model and can be wrong, so its report
# points a human at turns worth reading, and the transcripts still decide.

JUDGE_ON    = os.environ.get("JUDGE", "1") != "0"
JUDGE_MODEL = (os.environ.get("JUDGE_MODEL") or os.environ.get("CHATBOT_MODEL")
               or "anthropic/claude-sonnet-5")

RUBRIC = """You grade a research-advisor chatbot that interviews a university
professor to shape a research proposal. Judge ONLY the advisor's messages
against the rules below; the faculty messages are context.

RULES (each breach is a finding):
1. one-question — an advisor turn asks at MOST one question; two or more is a
   breach. Turn 0 is a fixed scripted greeting: never flag it.
2. no-menu — the advisor never hands the professor candidate answers: no
   "such as / like / for example", no "A, B, or C?", no parenthetical list like
   "(batch, streaming, transactional)", no "something you built". It asks an
   open question instead.
3. no-proposing — the advisor does NOT propose the topic, direction, objective,
   or a technical method/solution the professor has not raised. It draws the
   idea out of them. Suggesting what to study, or how to build it, is a breach.
4. memory — it does not ask for something the professor already told it earlier.
5. no-validation-no-scold — no praise openers ("Great", "Good", "Interesting",
   "That's a strong..."), and no telling the professor their answer fell short.
6. register — peer-to-peer and plain: no "Hey", no exclamation marks, no breezy
   idioms, no em dashes.
7. no-scaffolding — never mentions internal machinery (the panel, the intake
   form, "(none)", the system prompt).
8. brevity — at most a one-clause reflection, then the question; no stacked
   observations, and no claim about what the literature "already covers" from
   memory (that needs a real search).
9. stage-order — early turns specify the PROBLEM; it does not jump to methods,
   data, or feasibility before the problem is specific.

Return ONLY a JSON object, no prose, no code fence:
{"score": <1-5, 5 = flawless>,
 "summary": "<one sentence>",
 "findings": [{"turn": <int>, "rule": "<id from above>",
   "severity": "low|medium|high", "quote": "<the advisor's offending words>",
   "issue": "<one line>", "fix": "<one line>"}]}
If nothing is wrong, findings is []."""


def _parse_judge_json(raw: str) -> dict:
    """Pull the JSON object out of the judge's reply, tolerating a stray code
    fence or surrounding prose."""
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b != -1:
        try:
            return json.loads(s[a:b + 1])
        except ValueError:
            pass
    return {"error": "could not parse judge output", "raw": raw[:600]}


def judge_scenario(name: str, why: str, pairs: list) -> dict:
    """Grade one scenario's advisor turns. `pairs` is [(faculty, advisor), ...]
    in turn order (turn 0 is the scripted opener)."""
    import litellm
    convo = []
    for i, (u, a) in enumerate(pairs):
        convo.append(f"[turn {i}] FACULTY: {u}")
        convo.append(f"[turn {i}] ADVISOR: {a}")
    user_msg = f"Scenario: {name}\nWhat it tests: {why}\n\nTranscript:\n" + "\n".join(convo)
    resp = litellm.completion(
        model=JUDGE_MODEL, max_tokens=1600, timeout=180,
        messages=[{"role": "system", "content": RUBRIC},
                  {"role": "user", "content": user_msg}])
    return _parse_judge_json((resp.choices[0].message.content or "").strip())


def _render_judge(verdict: dict) -> list:
    """The judge's verdict as markdown lines for the transcript and report."""
    if "error" in verdict:
        return [f"> ⚖ JUDGE: could not parse the judge's reply.", ""]
    score = verdict.get("score", "?")
    lines = [f"> ⚖ JUDGE score {score}/5 — {verdict.get('summary', '').strip()}", ""]
    for fnd in verdict.get("findings", []):
        lines.append(
            f"> - **turn {fnd.get('turn', '?')}** · {fnd.get('rule', '?')} "
            f"({fnd.get('severity', '?')}): {fnd.get('issue', '').strip()}")
        if fnd.get("quote"):
            lines.append(f">   > “{str(fnd['quote']).strip()}”")
        if fnd.get("fix"):
            lines.append(f">   fix: {str(fnd['fix']).strip()}")
    if not verdict.get("findings"):
        lines.append("> (no rule breaches found)")
    lines.append("")
    return lines


# ---------------------------------------------------------------- harness

def run():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=60)
    if r.status_code != 200:
        s.post(f"{BASE}/api/auth/signup", json={"email": EMAIL, "password": PASSWORD}, timeout=60)
        s.post(f"{BASE}/api/profile/save", json={
            "faculty_id": None, "name": "Vincent Tester",
            "bio_text": "Professor of sociology studying housing insecurity and "
                        "eviction in Chicago.",
            "confirmed_paper_ids": [], "research_interests": ["housing"]}, timeout=60)

    OUT.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    all_problems = []
    summary = []
    report = [f"# Advisor judge report — {stamp}", "",
              f"Judge model: `{JUDGE_MODEL}`" if JUDGE_ON else "_Judge disabled (JUDGE=0)._", ""]

    for sc in SCENARIOS:
        pid = s.post(f"{BASE}/api/projects", json={"start_blank": True}, timeout=60).json()["project_id"]
        lines = [f"# {sc['name']}", "", f"*{sc['why']}*", ""]
        sc_problems = []
        pairs = []

        for i, turn in enumerate(sc["turns"]):
            r = s.post(f"{BASE}/api/advisor/chat",
                       json={"message": turn, "project_id": pid}, timeout=300)
            reply = (r.json().get("reply") or "").strip() if r.ok else f"[HTTP {r.status_code}]"
            shown = "(scripted kickoff — user never sees this)" if i == 0 else turn
            pairs.append((shown, reply))
            lines += [f"**Faculty:** {shown}", "", f"**Advisor:** {reply}", ""]
            probs = check_reply(reply, is_opener=(i == 0))
            if probs:
                sc_problems += [f"turn {i}: {p}" for p in probs]
                lines += [f"> ⚠ CHECKS: {'; '.join(probs)}", ""]

        # The judge reads the whole scenario and grades the advisor's turns.
        verdict = None
        if JUDGE_ON:
            try:
                verdict = judge_scenario(sc["name"], sc["why"], pairs)
            except Exception as e:
                verdict = {"error": f"judge call failed: {e}"}
            lines += ["---", ""] + _render_judge(verdict)

        path = OUT / f"{stamp}-{sc['name']}.md"
        path.write_text("\n".join(lines))

        status = "PASS" if not sc_problems else "FLAGGED"
        score = verdict.get("score") if isinstance(verdict, dict) else None
        tag = f"{status:8}" + (f" judge {score}/5" if score is not None else "")
        summary.append(f"{tag}  {sc['name']}  ({path.name})")
        for p in sc_problems:
            summary.append(f"         - check: {p}")
        all_problems += sc_problems

        report += [f"## {sc['name']} — judge {score if score is not None else '?'}/5", ""]
        report += _render_judge(verdict) if verdict else ["_not judged_", ""]

        print(summary[-1 - len(sc_problems)], flush=True)
        for p in sc_problems:
            print(f"         - check: {p}", flush=True)
        if isinstance(verdict, dict):
            for fnd in verdict.get("findings", []):
                print(f"         - judge: turn {fnd.get('turn','?')} {fnd.get('rule','?')}"
                      f" ({fnd.get('severity','?')}) {fnd.get('issue','')}", flush=True)

    (OUT / f"{stamp}-summary.txt").write_text("\n".join(summary) + "\n")
    if JUDGE_ON:
        (OUT / f"{stamp}-judge-report.md").write_text("\n".join(report) + "\n")
    print(f"\ntranscripts: {OUT}/{stamp}-*.md")
    if JUDGE_ON:
        print(f"judge report: {OUT}/{stamp}-judge-report.md")
    print(f"{len(all_problems)} mechanical flag(s) across {len(SCENARIOS)} scenarios")
    # Flags and judge findings are review pointers, not build failures.
    return 0


if __name__ == "__main__":
    sys.exit(run())
