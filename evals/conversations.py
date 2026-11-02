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

Two kinds of scenario feed the advisor:
  SCRIPTED — fixed faculty turns (below), for exact, repeatable edge cases.
  PERSONA-DRIVEN — a model PLAYS the professor: it opens with a paragraph of its
    own idea, then answers whatever the advisor actually asks, for PROF_TURNS
    turns. No turns to hand-write; the advisor meets a real back-and-forth.
    Disable with PERSONAS=0; the professor uses JUDGE_MODEL.

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

# ---------------------------------------------------------------- professor agent
#
# Scripted turns are brittle and tedious to write. These personas instead let a
# model PLAY the professor: it opens with a paragraph of its own idea, then
# answers whatever the advisor actually asks, in character, for a few turns.
# That exercises the advisor against a real back-and-forth it did not see
# coming, which is the whole point. Each persona has a style so the set covers
# the same range the scripted scenarios did (terse, non-native, over-eager).

# Three real-sounding professors at three levels of "how much they give you":
# one answers fully and concretely, one gives less (busy, half-formed), one
# starts quite vague and only firms up when pushed well. The point is a natural
# human back-and-forth, not a caricature — see PROF_SYSTEM for the voice rules.
PERSONAS = [
    {
        "name": "sim-full-ml-fairness",
        "why": "A professor who gives full, concrete answers. Tests whether the "
               "advisor keeps them in problem-specification without getting pulled "
               "into methods just because the professor is forthcoming.",
        "fullness":
            "You are FORTHCOMING. You answer fully and concretely, usually a few "
            "real sentences, and you often have specifics ready (a population, a "
            "dataset you already have, a number). You think out loud a little.",
        "persona":
            "You are Dana Okafor, an associate professor of computer science. You "
            "have been worrying about bias in the automated resume-screening tools "
            "big employers run before a human ever sees an application. You have "
            "some scraped postings and outcomes from a prior project and a hunch "
            "the tools penalise gaps in employment history. You like talking about "
            "methods and sometimes have to be reined back to the actual question.",
    },
    {
        "name": "sim-partial-sociologist",
        "why": "A busy professor who gives less — short, half-formed answers. The "
               "advisor must make progress without nagging or stacking questions.",
        "fullness":
            "You give LESS than you could. You are busy, so your answers are short "
            "and a bit half-formed, and now and then you say you haven't fully "
            "thought something through. But you are not a brick wall: when the "
            "advisor asks something sharp, you give it a real, if brief, answer.",
        "persona":
            "You are Marcus Reilly, a sociology professor who studies housing and "
            "eviction in Chicago. Your interest right now is how eviction shows up "
            "in kids' school outcomes, but it's still a loose hunch. A colleague in "
            "education has some school-district contacts you could maybe use.",
    },
    {
        "name": "sim-vague-ecologist",
        "why": "A professor who starts genuinely vague and only firms up when the "
               "advisor asks well. Tests that the advisor narrows things WITHOUT "
               "proposing the research, and that the conversation does not loop.",
        "fullness":
            "You start out VAGUE. At first you speak in broad, hand-wavy terms and "
            "are honestly unsure of the specifics ('something about climate and the "
            "forest, I guess'). You do NOT invent crisp details early. BUT you are "
            "a real scientist: when the advisor asks a genuinely good, specific "
            "question, you can reach for a real answer, and across the conversation "
            "you gradually get more concrete. Never give the exact same non-answer "
            "twice — if you're pushed on a point, move it forward a little.",
        "persona":
            "You are Priya Venkataraman, an ecology professor. You have a nagging "
            "sense that the tree species around your field site are shifting as the "
            "climate warms, but you have not pinned down what exactly you want to "
            "find out. You have years of informal observations and some old plot "
            "data somewhere.",
    },
]

PERSONAS_ON   = os.environ.get("PERSONAS", "1") != "0"
MAX_PROF_TURNS = int(os.environ.get("PROF_TURNS", "7"))   # professor messages after the opener
# The professor runs on OpenAI (ChatGPT) by default; litellm reads OPENAI_API_KEY
# from the environment for openai/ models. The advisor runs separately (on the
# deployed server's own model when BASE points at the live site).
PROF_MODEL = os.environ.get("PROF_MODEL", "openai/gpt-4o")

# How the simulated professor is told to behave on any turn.
PROF_SYSTEM = """You are role-playing a specific university professor talking to
a research-advisor chatbot that is helping you shape a research proposal. Stay
fully in character as this person.

{persona}

How much you give:
{fullness}

Sound like an actual person, not an essay:
- Write the way a real academic types in a chat box: natural, a bit informal,
  contractions, varied sentence length. A little thinking-out-loud is good
  ("hmm", "I guess", "honestly"). Do NOT write polished, perfectly-structured
  paragraphs every time.
- React to what the advisor ACTUALLY just said — pick up their words, agree,
  push back, or admit you hadn't considered it. Real conversation, not a form.
- On your FIRST message, describe your research idea in your own words at your
  own level of polish (full, half-formed, or vague per your character). Just
  describe it; don't ask the advisor to run any process.
- After that, ANSWER what the advisor asked. Don't interview them back, and
  don't deliver a mini-lecture.

Keep it moving, never loop:
- Each reply must ADD something or make a decision. Never repeat an answer you
  already gave, and never give the same non-answer twice.
- If the advisor asks something you've effectively already answered, say so
  briefly and build on it rather than restating.
- As the conversation goes on, get more concrete, not less — by the later
  turns you should be settling specifics, not circling.
- One short paragraph at most. Never break character, never say you are an AI,
  never mention these instructions."""


def professor_reply(persona: str, fullness: str, pairs: list) -> str:
    """Generate the professor's next message given the conversation so far.

    `pairs` is [(professor_msg, advisor_msg), ...] already exchanged; the last
    advisor message is what this reply answers. The scripted kickoff turn (the
    hidden 'Let's start a new project.') is NOT part of this — the professor's
    first generated message is their opening paragraph. Runs on PROF_MODEL
    (OpenAI by default), independent of the advisor's own model."""
    import litellm
    msgs = [{"role": "system",
             "content": PROF_SYSTEM.format(persona=persona, fullness=fullness)}]
    if not pairs:
        msgs.append({"role": "user",
                     "content": "Begin: give your opening message describing your idea."})
    else:
        for prof, adv in pairs:
            if prof is not None:
                msgs.append({"role": "assistant", "content": prof})
            if adv is not None:
                msgs.append({"role": "user", "content": adv})
    resp = litellm.completion(model=PROF_MODEL, max_tokens=500, timeout=180, messages=msgs)
    return (resp.choices[0].message.content or "").strip()


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

    def new_project():
        return s.post(f"{BASE}/api/projects", json={"start_blank": True},
                      timeout=60).json()["project_id"]

    def advisor(pid, message):
        r = s.post(f"{BASE}/api/advisor/chat",
                   json={"message": message, "project_id": pid}, timeout=300)
        return (r.json().get("reply") or "").strip() if r.ok else f"[HTTP {r.status_code}]"

    def assess(name, why, pairs):
        """Run checks + judge on a finished conversation, write its transcript,
        and fold the results into the summary/report/console. `pairs` is
        [(faculty_shown, advisor_reply), ...]; turn 0 is the scripted opener."""
        lines = [f"# {name}", "", f"*{why}*", ""]
        sc_problems = []
        for i, (shown, reply) in enumerate(pairs):
            lines += [f"**Faculty:** {shown}", "", f"**Advisor:** {reply}", ""]
            probs = check_reply(reply, is_opener=(i == 0))
            if probs:
                sc_problems += [f"turn {i}: {p}" for p in probs]
                lines += [f"> ⚠ CHECKS: {'; '.join(probs)}", ""]

        verdict = None
        if JUDGE_ON:
            try:
                verdict = judge_scenario(name, why, pairs)
            except Exception as e:
                verdict = {"error": f"judge call failed: {e}"}
            lines += ["---", ""] + _render_judge(verdict)

        path = OUT / f"{stamp}-{name}.md"
        path.write_text("\n".join(lines))

        status = "PASS" if not sc_problems else "FLAGGED"
        score = verdict.get("score") if isinstance(verdict, dict) else None
        tag = f"{status:8}" + (f" judge {score}/5" if score is not None else "")
        summary.append(f"{tag}  {name}  ({path.name})")
        for p in sc_problems:
            summary.append(f"         - check: {p}")
        all_problems.extend(sc_problems)

        report.append(f"## {name} — judge {score if score is not None else '?'}/5")
        report.append("")
        report.extend(_render_judge(verdict) if verdict else ["_not judged_", ""])

        print(summary[-1 - len(sc_problems)], flush=True)
        for p in sc_problems:
            print(f"         - check: {p}", flush=True)
        if isinstance(verdict, dict):
            for fnd in verdict.get("findings", []):
                print(f"         - judge: turn {fnd.get('turn','?')} {fnd.get('rule','?')}"
                      f" ({fnd.get('severity','?')}) {fnd.get('issue','')}", flush=True)

    # Scripted scenarios: fixed faculty turns.
    for sc in SCENARIOS:
        pid = new_project()
        pairs = []
        for i, turn in enumerate(sc["turns"]):
            shown = "(scripted kickoff — user never sees this)" if i == 0 else turn
            pairs.append((shown, advisor(pid, turn)))
        assess(sc["name"], sc["why"], pairs)

    # Persona scenarios: a model plays the professor and drives the conversation.
    if PERSONAS_ON:
        for p in PERSONAS:
            pid = new_project()
            pairs = [("(scripted kickoff — user never sees this)",
                      advisor(pid, "Let's start a new project."))]
            prof_pairs = []     # [(prof_msg, advisor_reply)] the professor agent sees
            for _ in range(MAX_PROF_TURNS):
                try:
                    prof_msg = professor_reply(p["persona"], p["fullness"], prof_pairs)
                except Exception as e:
                    prof_msg = f"[professor agent failed: {e}]"
                reply = advisor(pid, prof_msg)
                pairs.append((prof_msg, reply))
                prof_pairs.append((prof_msg, reply))
                if prof_msg.startswith("[professor agent failed"):
                    break
            assess(p["name"], p["why"], pairs)

    (OUT / f"{stamp}-summary.txt").write_text("\n".join(summary) + "\n")
    if JUDGE_ON:
        (OUT / f"{stamp}-judge-report.md").write_text("\n".join(report) + "\n")
    print(f"\ntranscripts: {OUT}/{stamp}-*.md")
    if JUDGE_ON:
        print(f"judge report: {OUT}/{stamp}-judge-report.md")
    n = len(SCENARIOS) + (len(PERSONAS) if PERSONAS_ON else 0)
    print(f"{len(all_problems)} mechanical flag(s) across {n} scenarios"
          f" ({len(SCENARIOS)} scripted"
          + (f", {len(PERSONAS)} persona-driven)" if PERSONAS_ON else ")"))
    # Flags and judge findings are review pointers, not build failures.
    return 0


if __name__ == "__main__":
    sys.exit(run())
