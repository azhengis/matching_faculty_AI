#!/usr/bin/env python3
"""The reviewed improve loop: assess the advisor, draft ONE lesson, prove it,
and propose it for human approval. It NEVER commits or deploys.

This is the "draft + prove, you approve" design, teaching through the lessons
file (prompts/advisor/99_lessons.md):

  1. ASSESS   run the eval scenarios against the CURRENT advisor (BASE), judge
              every advisor turn, collect the rule breaches and a baseline score.
  2. CLUSTER  group the breaches into recurring patterns, worst first.
  3. DRAFT    ask an improver model to write ONE short, concrete lesson that
              fixes the top pattern, in the lessons-file style.
  4. PROVE    if a candidate advisor is available (CANDIDATE_BASE — a second
              instance running WITH the drafted lesson applied), re-assess it and
              compare. The lesson passes only if the score improves and no
              scenario regresses. Without CANDIDATE_BASE this step is skipped and
              the proposal is marked "unproven — needs a candidate run".
  5. PROPOSE  write evals/proposals/<ts>.md: the pattern, the evidence (the
              failing turns), the drafted lesson, the exact 99_lessons.md diff,
              and before/after scores. A human reads it and, if good, applies the
              diff and deploys. Nothing here edits the live lessons file.

Proving locally (recommended): boot a local app with OpenAI as the advisor and
the candidate lesson applied, then point CANDIDATE_BASE at it. The litellm
startup-fetch hang is avoided by LITELLM_LOCAL_MODEL_COST_MAP (set below).

  # baseline instance (current lessons):
  CHATBOT_MODEL=openai/gpt-4o LITELLM_LOCAL_MODEL_COST_MAP=True \
    uvicorn web_app:app --port 8101 &
  # candidate instance (after applying the drafted lesson to 99_lessons.md):
  CHATBOT_MODEL=openai/gpt-4o LITELLM_LOCAL_MODEL_COST_MAP=True \
    uvicorn web_app:app --port 8102 &
  BASE=http://127.0.0.1:8101 CANDIDATE_BASE=http://127.0.0.1:8102 \
    OPENAI_API_KEY=... python evals/improve.py

Usage (assess + draft + propose only, against any advisor):
  BASE=http://127.0.0.1:8101 OPENAI_API_KEY=... python evals/improve.py
"""
import os
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # or litellm hangs on its cost-map fetch

import json, time, pathlib, importlib.util, datetime, difflib, collections, re
import requests

REPO = pathlib.Path(__file__).resolve().parents[1]
LESSONS_FILE = REPO / "prompts" / "advisor" / "99_lessons.md"
PROPOSALS = REPO / "evals" / "proposals"

# Reuse the harness: professor agent, scenarios, judge, mechanical checks.
_spec = importlib.util.spec_from_file_location("conv", REPO / "evals" / "conversations.py")
conv = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(conv)

BASE = os.environ.get("BASE", "http://127.0.0.1:8101")
CANDIDATE_BASE = os.environ.get("CANDIDATE_BASE")
EMAIL = os.environ.get("EVAL_EMAIL", "improver@depaul.edu")
PASSWORD = os.environ.get("EVAL_PASSWORD", "improver-pw-123")
IMPROVER_MODEL = os.environ.get("IMPROVER_MODEL", "gpt-4o")
# Keep each assessment cheap: short scripted scenarios by default, not full
# 70-turn proposals. The behavioural rules break (or hold) in the first turns.
USE_PERSONAS = os.environ.get("IMPROVE_PERSONAS", "0") != "0"


def session(base):
    s = requests.Session()
    r = s.post(f"{base}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        s.post(f"{base}/api/auth/signup", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    return s


def assess(base, label):
    """Run the scenarios against `base`, judge each, return (scenarios, mean_score).

    scenarios: [{name, pairs, verdict}], verdict is the judge's dict."""
    print(f"[assess:{label}] running against {base}", flush=True)
    s = session(base)
    out = []
    for sc in conv.SCENARIOS:
        pid = s.post(f"{base}/api/projects", json={"start_blank": True}, timeout=60).json()["project_id"]
        pairs = []
        for i, turn in enumerate(sc["turns"]):
            r = s.post(f"{base}/api/advisor/chat", json={"message": turn, "project_id": pid}, timeout=300)
            reply = (r.json().get("reply") or "").strip() if r.ok else f"[HTTP {r.status_code}]"
            shown = "(kickoff)" if i == 0 else turn
            pairs.append((shown, reply))
        try:
            verdict = conv.judge_scenario(sc["name"], sc["why"], pairs)
        except Exception as e:
            verdict = {"error": str(e), "findings": [], "score": None}
        out.append({"name": sc["name"], "why": sc["why"], "pairs": pairs, "verdict": verdict})
        sc_score = verdict.get("score")
        print(f"  {sc['name']}: judge {sc_score}/5, {len(verdict.get('findings', []))} finding(s)", flush=True)
    scores = [s_["verdict"].get("score") for s_ in out if isinstance(s_["verdict"].get("score"), (int, float))]
    mean = round(sum(scores) / len(scores), 2) if scores else None
    return out, mean


def cluster(scenarios):
    """Rank recurring rule breaches across all scenarios, worst first."""
    buckets = collections.defaultdict(list)
    for sc in scenarios:
        for f in sc["verdict"].get("findings", []):
            buckets[f.get("rule", "unknown")].append({**f, "scenario": sc["name"]})
    sev = {"high": 3, "medium": 2, "low": 1}
    ranked = sorted(buckets.items(),
                    key=lambda kv: (len(kv[1]), sum(sev.get(f.get("severity"), 1) for f in kv[1])),
                    reverse=True)
    return ranked


def existing_lessons():
    return LESSONS_FILE.read_text()


IMPROVER_SYSTEM = (
    "You improve a research-advisor chatbot by writing ONE new 'lesson' — a short, "
    "concrete behavioural correction that will be appended to its prompt and read "
    "every turn. You are given a recurring rule breach, the exact offending advisor "
    "turns, and the lessons already in place. Write a single lesson of one to three "
    "sentences that would prevent this breach, phrased as a direct instruction to "
    "the advisor, specific enough to act on and general enough to cover the pattern "
    "(not the one example). Do NOT restate a lesson already present. Do NOT contradict "
    "the rules implied by the breach. Return ONLY the lesson text, no preamble, no "
    "bullet, no quotes."
)


def draft_lesson(rule, findings):
    evidence = "\n".join(
        f"- [{f['scenario']}] {f.get('issue','')} :: “{str(f.get('quote',''))[:200]}”"
        for f in findings[:6])
    user = (f"Recurring breach of rule: {rule}\n\n"
            f"Offending advisor turns:\n{evidence}\n\n"
            f"Lessons already in place:\n{existing_lessons()}")
    r = requests.post(conv.OPENAI_CHAT_URL,
                      headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                               "Content-Type": "application/json"},
                      json={"model": IMPROVER_MODEL, "max_tokens": 200, "temperature": 0.4,
                            "messages": [{"role": "system", "content": IMPROVER_SYSTEM},
                                         {"role": "user", "content": user}]},
                      timeout=60)
    r.raise_for_status()
    return (r.json()["choices"][0]["message"]["content"] or "").strip()


def apply_lesson(text):
    """Return what 99_lessons.md WOULD look like with this lesson appended."""
    cur = existing_lessons()
    body = cur.replace("(No lessons recorded yet.)", "").rstrip()
    n = len([ln for ln in cur.splitlines() if re.match(r"^\d+\.\s", ln.strip())]) + 1
    return body + f"\n\n{n}. {text}\n"


def diff(old, new):
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        "99_lessons.md (current)", "99_lessons.md (proposed)"))


def main():
    PROPOSALS.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")

    base_scenarios, base_mean = assess(BASE, "baseline")
    ranked = cluster(base_scenarios)
    if not ranked:
        print("No rule breaches found — nothing to improve. The advisor is clean on these scenarios.")
        (PROPOSALS / f"{ts}-clean.md").write_text(
            f"# Improve run {ts}\n\nBaseline mean judge score: {base_mean}/5.\n"
            f"No rule breaches found across {len(base_scenarios)} scenarios. No lesson proposed.\n")
        return

    top_rule, findings = ranked[0]
    print(f"\nTop recurring breach: '{top_rule}' ({len(findings)} occurrence(s))", flush=True)
    lesson = draft_lesson(top_rule, findings)
    print(f"Drafted lesson:\n  {lesson}\n", flush=True)

    proposed = apply_lesson(lesson)
    lesson_diff = diff(existing_lessons(), proposed)

    # PROVE: only if a candidate instance (with the lesson applied) is provided.
    proof = "UNPROVEN — no CANDIDATE_BASE given. Boot a candidate advisor with this lesson applied and re-run to prove it (see the module docstring)."
    verdict_line = "needs a candidate run"
    if CANDIDATE_BASE:
        cand_scenarios, cand_mean = assess(CANDIDATE_BASE, "candidate")
        regressed = []
        base_by = {s_["name"]: s_["verdict"].get("score") for s_ in base_scenarios}
        for s_ in cand_scenarios:
            b, c = base_by.get(s_["name"]), s_["verdict"].get("score")
            if isinstance(b, (int, float)) and isinstance(c, (int, float)) and c < b:
                regressed.append(f"{s_['name']} {b}->{c}")
        improved = (isinstance(base_mean, (int, float)) and isinstance(cand_mean, (int, float))
                    and cand_mean > base_mean)
        passed = improved and not regressed
        verdict_line = "PASS — apply it" if passed else "FAIL — do not apply"
        proof = (f"baseline mean {base_mean}/5 -> candidate mean {cand_mean}/5. "
                 f"regressions: {regressed or 'none'}. Verdict: {verdict_line}.")
        print(f"\nPROOF: {proof}", flush=True)

    evidence = "\n".join(f"- **[{f['scenario']}]** {f.get('issue','')}\n  > “{str(f.get('quote',''))[:240]}”\n"
                         f"  suggested earlier by judge: {f.get('fix','')}" for f in findings[:6])
    report = [
        f"# Improve proposal — {ts}", "",
        f"**Baseline mean judge score:** {base_mean}/5 across {len(base_scenarios)} scenarios.", "",
        f"**Top recurring breach:** `{top_rule}` — {len(findings)} occurrence(s).", "",
        "## Evidence (failing advisor turns)", "", evidence, "",
        "## Proposed lesson", "", f"> {lesson}", "",
        "## Proof", "", proof, "",
        f"## Verdict: {verdict_line}", "",
        "## Diff to apply (prompts/advisor/99_lessons.md)", "", "```diff", lesson_diff, "```", "",
        "_Apply this diff and deploy only after review. This tool changed nothing._",
    ]
    path = PROPOSALS / f"{ts}-proposal.md"
    path.write_text("\n".join(report))
    print(f"\nProposal written: {path}", flush=True)


if __name__ == "__main__":
    main()
