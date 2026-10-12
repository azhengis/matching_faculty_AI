# How the conversation goes — all stages

The advisor walks a researcher through the standard research process, one step
at a time, and builds a proposal as it goes. This is the full flow as it works
now: the stages, what each one needs, how it advances, and what the panel shows.

The guiding frame is the five-step research process the whole thing serves:

1. **Choose the topic** — a broad interest, narrowed by reading toward a niche.
2. **Identify the problem** — the specific issue or gap. *(Stages 1–2)*
3. **Formulate research questions** — exactly what the project will find out.
4. **Create a research design** — the practical plan to answer them. *(Stage 4)*
5. **Write the proposal** — the document, assembled from all of the above.

---

## Getting in

1. **Sign in** with a DePaul email (through Firebase — Google or password).
2. **Profile.** If the directory already knows the email, the profile is
   pre-filled from the scraped bio, interests, and publications. Otherwise the
   profile assistant builds it by asking.
3. **Two doors:**
   - **Explore** — for someone with no problem in mind. An interview that draws
     a direction out of their own work, then hands it to the advisor.
   - **Advisor** — for someone who already has a problem. Goes straight into
     the stages below.

---

## The stages

The current stage is **derived from what is saved**, never from what the bot
remembers — so a reload or a resumed session always lands in the right place.

| Saved state | Stage |
|---|---|
| nothing yet | **Stages 1–2** — specify the problem, test novelty |
| `novelty` saved | **Stage 3** — write the problem statement |
| `problem_statement` saved | **Stage 4** — build the proposal |

### Stage 1 — Specify the research problem

Mostly asking. The researcher already has an idea; the job is making it
specific. The advisor:

- Names the project early (a working title it proposes; the researcher corrects).
- Works out silently whether the problem is **practical** (aimed at change) or
  **theoretical** (aimed at expanding knowledge).
- Narrows from a broad area to a specific gap, using the problem-statement
  tracker's questions (below) as the substance of the interview.
- Draws out 2–4 **research questions** and saves them.

It does **not** suggest directions, offer candidate answers, or ask about
methods here. One focused question per turn.

> **Tracker — Problem statement** *(fills in as you answer)*
> - **Contextualize:** Where/when does it arise? Who does it affect? What has
>   been attempted? *(or, for a theoretical problem: what is known? time/area
>   limited? how is it defined and debated?)*
> - **Why it matters:** What happens if unsolved? Who feels it? Wider relevance?
>   *(or: how it advances understanding? benefits for future research?
>   consequences for society?)*
> - **Aims and objectives:** the overall aim, and the concrete objectives.

> **Tracker — Research questions**
> Shows the drafted question and checks it against six criteria: focused,
> researchable, feasible, specific, complex (not yes/no, not a lookup), and
> relevant — and not built on a value judgment.

### Stage 2 — Test whether it is new

Before anything is written down, the advisor checks that the problem is
actually novel.

- The researcher names the gap **first** (so the search doesn't hand them one).
- The advisor searches the live literature (OpenAlex), reads what comes back,
  and gives a plain verdict: **clearly new**, **partly covered**, or **already
  well covered**.
- If it isn't novel, it offers honest ways forward (a new population, method,
  data, timeframe; or a deliberate replication) rather than sending them away.
- **Gate:** a confirmed novelty claim is saved — *"Nobody has yet ___, and this
  project will."* This unlocks Stage 3.

A "Research landscape" panel shows how close each found paper is to the idea.

### Stage 3 — Write the problem statement

A three-to-four paragraph statement, drafted in the chat and confirmed before
it is saved:

1. Background and real-world impact.
2. The specific problem in context — field, population/setting, scope.
3. How it has been addressed before, and the gap this project takes on.
4. The research objectives that follow.

The sharpened title leads it. **Gate:** saving the problem statement unlocks
Stage 4. It is the anchor everything after must serve.

### Stage 4 — Build the proposal

The stance inverts: now the advisor brings framings, names tensions, and puts
concrete options on the table — but on *how* to study the problem, never *what*
to study.

- **Outline first** — a two-page sketch of the whole proposal, cheap to throw
  away, to catch inconsistencies early.
- Then section by section (saved as each settles, shown in the panel):
  background, objectives, research questions, hypotheses *(optional)*,
  assumptions & delimitations, literature review, **methodology**, the role of
  AI, ethical considerations, expected outcomes, plan of work, conclusions,
  keywords, abstract, and a final title check.
- **Methodology is the research design:** settle the approach first —
  **qualitative** (to understand), **quantitative** (to test), or **mixed** —
  and let the aim decide it; then how data is collected and analysed, and
  whether it is feasible.
- **Collaborators:** once the methodology is set, the advisor finds DePaul
  faculty who supply the specific thing the project is missing, grounded in
  their real publications.
- Before calling it done, it re-reads the whole proposal against itself and
  against four aims: **relevance, context, approach, achievability**.

> **Tracker — Research design** *(active in Stage 4)*
> Approach (qual/quant/mixed) · how data is collected · how it is analysed ·
> feasible with the data, tools, and timeframe.

---

## The panel

Beside the chat, the proposal panel shows:

- **The current stage.**
- **Three foldable trackers** — Problem statement, Research questions, Research
  design — each a checklist that fills in as the conversation answers it. Each
  item has a status circle (green done · gray in progress · white not started)
  and expands to show the captured answer. The section in progress opens by
  default; finished ones fold to a single ✓.
- **The research landscape** (during novelty testing).
- **The proposal sections** as they are saved.
- **Matched collaborators.**
- **References** from the literature search.
- **Download** — plain, MLA, or APA.

The panel can be collapsed entirely, or dragged wider/narrower; the width is
remembered.

---

## Download

The finished proposal downloads as a .docx in **MLA** or **APA** style (or
plain): Times New Roman 12, one-inch margins, double-spaced, page numbers, a
Works Cited / References page, and a professor's title block with the
researcher's department. Open in Word and "Save as PDF" for submission.

---

## What is guaranteed vs. instructed

- **Enforced in code:** the stage order, the hand-edit locks on sections, that
  literature and collaborators come from real searches, and that a turn always
  ends.
- **Instructed in the prompt:** not suggesting directions, not praising, one
  question per turn, keeping the trackers filled. These are followed most of
  the time; they are not guarantees.
