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

import os, sys, json
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
                "or expert on a specific research topic or problem. "
                "Extract the core academic topic from their message before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The research topic to search — use specific academic terms, "
                            "not the user's full sentence. Strip out phrases like "
                            "'I am looking for' or 'someone who'. "
                            "Examples: 'machine learning clinical prediction', "
                            "'corporate finance risk valuation', "
                            "'bilingual language acquisition children', "
                            "'environmental policy urban health disparities'"
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
When results come back, present them as a SHORT, NATURAL conversation — not a formatted list.

For each person (pick the 3–5 most relevant), say in 2–3 sentences:
• Who they are and what they actually work on
• WHY specifically they match this user's situation
• Their email (always include it — it's the actionable next step)

Example tone:
"The strongest match is Casey Bennett — he builds AI systems specifically for clinical \
decision support and personalized medicine at DePaul's computing school, which lines up \
directly with what you described. His email is cbennett@depaul.edu."

After presenting results, end with ONE specific follow-up question, such as:
• "Are you looking for someone with industry ties, or more of a pure academic researcher?"
• "Is the ML side or the clinical side more important for your project?"
• "Would someone from the law school add useful perspective here?"

━━━ TONE ━━━
Conversational, warm, and direct. Think of yourself as a knowledgeable colleague helping \
someone navigate the university — not a search engine returning a list."""


# ── Search function (called when Claude uses the tool) ────────────────────────

def run_search(query: str, mode: str = "semantic") -> dict:
    """Execute the faculty search and return structured data for Claude."""
    try:
        clean_q = sm.clean_query(query) or query
        qv      = _model.encode([clean_q], normalize_embeddings=True)[0]

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
            entry = {
                "name":        person["name"],
                "title":       person.get("title", ""),
                "department":  person.get("department") or person.get("college", ""),
                "college":     person.get("college", ""),
                "email":       person.get("email", ""),
                "match_pct":   round(score * 100),
                "why_match":   sm.explain_match(
                    clean_q, person["research_summary"], name=person["name"]
                ),
                "bio_url":     person.get("bio_url", ""),
            }

            # Add best matching publication if available and sufficiently relevant
            if _paper_idx is not None:
                pub = sm.find_best_paper(person.get("id"), qv, _paper_idx)
                if pub:
                    p_title, p_year, p_cited, p_sim = pub
                    if p_sim >= 0.58:
                        entry["relevant_paper"] = {
                            "title":       p_title,
                            "year":        p_year,
                            "cited_by":    p_cited,
                        }
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
    print(f"\n{'─'*60}")
    print(f"DePaul Faculty Advisor  [model: {MODEL}]")
    print(f"{len(_people)} faculty indexed  |  {n_pubs} with publication records")
    print(f"{'─'*60}")
    print("Tell me about your research — I'll help you find the right people.")
    print("(Switch model: export CHATBOT_MODEL=ollama/llama3  or any LiteLLM model)")
    print("Type 'quit' to exit.\n")

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
