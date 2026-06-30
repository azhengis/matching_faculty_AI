#!/usr/bin/env python3
"""
chatbot.py
----------
Conversational DePaul faculty advisor — works with any LLM provider.

LiteLLM routes to the provider you configure; SPECTER2 does the matching.

SETUP:
    pip3 install --break-system-packages --user litellm

    Then set your provider's key + model:

    # OpenAI
    export OPENAI_API_KEY=sk-...
    export CHATBOT_MODEL=gpt-4o-mini          # or gpt-4o

    # Anthropic / Claude
    export ANTHROPIC_API_KEY=sk-ant-...
    export CHATBOT_MODEL=claude-haiku-4-5-20251001

    # Ollama (local, no key needed)
    export CHATBOT_MODEL=ollama/llama3        # or ollama/mistral, etc.

    # Gemini
    export GEMINI_API_KEY=...
    export CHATBOT_MODEL=gemini/gemini-1.5-flash

    Any model supported by LiteLLM works:
    https://docs.litellm.ai/docs/providers

RUN:
    python3 chatbot.py
"""

import os, sys, json, re
import numpy as np

# ── LiteLLM (universal LLM router) ───────────────────────────────────────────
try:
    import litellm
    litellm.suppress_debug_info = True
except ImportError:
    sys.exit(
        "Install LiteLLM first:\n"
        "  pip3 install --break-system-packages --user litellm"
    )

# ── Search engine ─────────────────────────────────────────────────────────────
# search.py defines everything at module level, main() is guarded by __name__
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import search as sm
except ImportError as e:
    sys.exit(f"Missing dependency: {e}\nRun: pip3 install sentence-transformers numpy")

# Global search state — loaded once at startup
_people    = None
_model     = None
_emb       = None
_labels    = None
_paper_idx = None

MODEL = os.environ.get("CHATBOT_MODEL", "gpt-4o-mini")
TOP_K        = 5


# ── Tool definition ───────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_faculty",
            "description": (
                "Search DePaul University's full-time faculty database by research topic. "
                "Use this when the user is looking for a faculty collaborator, advisor, "
                "or expert on a specific research topic or problem.\n\n"
                "IMPORTANT — before calling this tool, translate the user's description "
                "into specific academic/scientific terminology. The search engine matches "
                "against faculty research bios which use domain vocabulary, not lay language.\n\n"
                "Examples of how to translate:\n"
                "  'memory loss in elderly patients' → "
                "'cognitive decline dementia Alzheimer neurodegeneration geriatric aging'\n"
                "  'heart disease risk factors' → "
                "'cardiovascular disease epidemiology risk factors clinical outcomes'\n"
                "  'kids learning problems' → "
                "'learning disabilities cognitive development pediatric education intervention'\n"
                "  'AI that explains itself' → "
                "'explainability interpretability transparent machine learning'\n"
                "  'finding cancer early' → "
                "'cancer screening biomarker early detection oncology diagnosis'\n\n"
                "Use 4-8 technical terms that would appear in a researcher's publications or bio. "
                "Expand abbreviations and translate colloquial phrases to academic equivalents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Academic/scientific translation of the user's research description. "
                            "Use 4-8 domain-specific terms that researchers in this field would "
                            "use in their publications. Do NOT use the user's lay phrasing — "
                            "translate it first. Strip connective phrases like "
                            "'I am looking for' or 'someone who works on'."
                        )
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["semantic", "complementary"],
                        "description": (
                            "semantic = faculty whose research directly matches the topic (default). "
                            "complementary = faculty from an adjacent field who could bring a "
                            "different perspective or skill set (e.g. a statistician for a "
                            "biology project, or a lawyer for a tech ethics project)."
                        )
                    }
                },
                "required": ["query"]
            }
        }
    }
]


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a friendly and knowledgeable research advisor at DePaul University. \
Your job is to help researchers, students, and external collaborators find the right DePaul \
faculty member to work with.

You have access to a real-time faculty search tool (search_faculty) that searches DePaul's \
faculty database using AI-powered semantic matching.

━━━ WHEN TO SEARCH ━━━
Call search_faculty when the user:
• Describes a research problem, thesis topic, project, or question
• Asks to find a faculty member, advisor, mentor, or collaborator
• Refines a previous result ("someone more applied", "what about the law school?", "anyone in economics?")
• Mentions a method + domain ("I use NLP for social science research")

━━━ WHEN NOT TO SEARCH ━━━
Do NOT call search_faculty when the user:
• Asks general questions (about DePaul, deadlines, funding processes, how research works)
• Sends greetings, thanks, or small talk
• Asks a follow-up about a person already shown ("what's Dr. X's email?", "tell me more about #2")
• Asks something clearly off-topic (travel, food, weather, etc.)

━━━ BEFORE SEARCHING ━━━
If the topic is very vague (e.g. just "science", "business", "I need help"), ask ONE focused \
clarifying question before searching. One question only — don't ask multiple at once.

Examples of good clarifying questions:
• "What's the core research problem you're working on?"
• "Are you looking for a methodologist (stats/ML) or a domain expert in [area]?"
• "Is this for a specific application area, like healthcare or education?"

━━━ HOW TO PRESENT RESULTS ━━━
When results come back, check the match_tier field for each result.
If all results are "Possible" or "Weak" (none are "Strong" or "Good"), say clearly
upfront: "I didn't find any DePaul faculty who are strong matches for this — it may not
be a current research specialty here." Then briefly mention the closest results anyway
in case one is still useful.

When there are good matches, present them naturally — not a formatted list.

For each person (pick the 3–5 most relevant), cover in 2–4 sentences:
• Who they are and what they actually work on (use bio_summary, not just why_match)
• WHY specifically they match this user's situation
• Any relevant publications — mention title + context ("she published on X in 2023")
• If courses-only (no bio): mention what they teach and that it signals their expertise
• Their email (always include — it's the actionable next step)

Example tone:
"The strongest match is Casey Bennett — he builds AI systems specifically for clinical \
decision support and has published on machine learning for personalized medicine. \
His email is cbennett@depaul.edu."

━━━ OPTION BLOCKS ━━━
After EVERY response that shows faculty results, end with this exact block:

"What would you like to do next?
  [1] [specific refinement based on what was just discussed]
  [2] [find someone complementary / from a different field]
  [3] Tell me more about [name of the top result]
  [4] Start a completely new search"

Make options [1] and [2] specific to the current query — not generic.
Examples for a healthcare AI query:
  [1] Focus more on the clinical/hospital implementation side
  [2] Find someone from public health or nursing who could complement this

When the user picks a number, handle it:
• [1] or [2]: refine the search accordingly and show new results
• [3]: give a fuller profile of that person (bio, publications, contact, what to say in an email)
• [4]: reset and ask what they need

━━━ TONE ━━━
Conversational, warm, and direct. Think of yourself as a knowledgeable colleague helping \
someone navigate the university — not a search engine returning a list."""


# ── Search function (called when Claude uses the tool) ────────────────────────

def run_search(query: str, mode: str = "semantic") -> dict:
    """Execute the faculty search and return structured data for Claude."""
    try:
        clean_q  = sm.clean_query(query) or query
        expanded = sm.expand_query_with_llm(clean_q)
        qv       = _model.encode([expanded], normalize_embeddings=True)[0]
        clean_q  = expanded   # use academic expansion for both SPECTER2 and keyword scoring

        if mode == "complementary":
            scores       = sm.hybrid_scores(clean_q, qv, _emb, _people)
            top_sem_idx  = np.argsort(scores)[::-1][:TOP_K * 2]
            excluded     = {int(_labels[i]) for i in top_sem_idx}
            candidates   = [
                (_people[i], float(scores[i]), int(_labels[i]))
                for i in range(len(_people))
                if _labels[i] not in excluded
            ]
            candidates.sort(key=lambda x: x[1], reverse=True)
            results = sm.diversity_filter(candidates)
        else:
            scores     = sm.hybrid_scores(clean_q, qv, _emb, _people)
            top        = np.argsort(scores)[::-1][:sm.POOL_SIZE]
            candidates = [(_people[i], float(scores[i]), None) for i in top]
            results    = sm.diversity_filter(candidates)

        output = []
        for person, score, _ in results:
            summary = person.get("research_summary", "")

            # Bio summary: up to 3 non-biographical sentences for richer context
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", summary)
                         if len(s.strip()) > 40]
            non_bio   = [s for s in sentences
                         if not sm._is_bio_opener(s, person.get("name", ""))]
            bio_summary = " ".join(non_bio[:3])[:500] if non_bio else summary[:400]

            # Courses snippet for courses-only faculty
            courses_snippet = None
            if summary.lstrip().startswith("Courses taught:"):
                block = re.sub(r"^Courses taught:\s*", "", summary, flags=re.IGNORECASE)
                if "\n\n" in block:
                    block = block.split("\n\n")[0]
                courses_snippet = block[:300]

            entry = {
                "name":            person["name"],
                "title":           person.get("title", ""),
                "department":      person.get("department") or person.get("college", ""),
                "college":         person.get("college", ""),
                "email":           person.get("email", ""),
                "match_tier":      sm._score_tier(score),
                "match_pct":       round(score * 100),
                "why_match":       sm.explain_match(
                    clean_q, summary, name=person["name"]
                ),
                "bio_summary":     bio_summary,
                "bio_url":         person.get("bio_url", ""),
            }
            if courses_snippet:
                entry["courses_taught"] = courses_snippet

            # Add up to 2 relevant publications
            if _paper_idx is not None:
                pubs = sm.find_top_papers(person.get("id"), qv, _paper_idx, n=2, min_sim=0.58)
                if pubs:
                    entry["relevant_papers"] = [
                        {"title": t, "year": y, "cited_by": c}
                        for t, y, c, _ in pubs
                    ]
            output.append(entry)

        return {
            "query_used":   clean_q,
            "mode":         mode,
            "result_count": len(output),
            "results":      output,
        }

    except Exception as e:
        return {"error": str(e), "results": []}


# ── Conversation loop ─────────────────────────────────────────────────────────

def chat(history: list, user_message: str) -> str:
    """
    Send one user message to the configured LLM, handle any tool calls,
    and return the final text response.
    """
    history.append({"role": "user", "content": user_message})

    # System prompt goes at the front of the messages array
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    while True:
        response = litellm.completion(
            model      = MODEL,
            max_tokens = 1024,
            tools      = TOOLS,
            messages   = messages,
        )

        msg           = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Store assistant turn in history (and messages for next loop iteration)
        assistant_entry = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        history.append(assistant_entry)
        messages.append(assistant_entry)

        if finish_reason != "tool_calls":
            return msg.content or ""

        # Execute each tool call and feed results back
        for tc in msg.tool_calls:
            tool_input = json.loads(tc.function.arguments)
            result     = run_search(
                query = tool_input.get("query", ""),
                mode  = tool_input.get("mode", "semantic"),
            )
            tool_entry = {
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      json.dumps(result),
            }
            history.append(tool_entry)
            messages.append(tool_entry)
        # Loop back — GPT will now format the results into a response


def main():
    global _people, _model, _emb, _labels, _paper_idx

    print("Loading faculty search index...")
    _people = sm.load_faculty()

    print("Loading SPECTER2 model...")
    _model = sm.load_model()

    _emb, _labels, _ = sm.get_index(_people, _model)
    _paper_idx        = sm.get_paper_index(_people, _model)

    n_pubs = len(_paper_idx["by_faculty"]) if _paper_idx else 0
    print(f"\n{'━'*60}")
    print(f"  DePaul Faculty Advisor  [LLM: {MODEL}]")
    print(f"  {len(_people)} faculty indexed  |  {n_pubs} with publication records")
    print(f"{'━'*60}")
    print("""
Hi! I help you find the right DePaul faculty to collaborate with.

What brings you here today?

  [1] I have a research problem and need a collaborator or co-investigator
  [2] I'm looking for a thesis or dissertation advisor
  [3] I want to explore who at DePaul works on a specific topic
  [4] I'm from outside DePaul and looking for a research partnership

Or just describe what you need in your own words.
Type 'quit' to exit.
""")

    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAdvisor: Good luck with your research!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q", "bye", "goodbye"}:
            print("\nAdvisor: Good luck with your research!")
            break

        try:
            reply = chat(history, user_input)
            print(f"\nAdvisor: {reply}\n")
        except Exception as e:
            print(f"\n[Error: {e}]\n")


if __name__ == "__main__":
    main()
