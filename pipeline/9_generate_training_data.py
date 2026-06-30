#!/usr/bin/env python3
"""
9_generate_training_data.py
---------------------------
Generate synthetic (query, faculty_bio) training pairs for fine-tuning SPECTER2.

For each faculty member, the LLM generates 5 different search queries that a
graduate student or external researcher might use to find that person. These
become positive training pairs: (query, faculty_bio). During training,
other faculty in the same batch act as negatives (MultipleNegativesRankingLoss).

Why synthetic data works:
  The LLM understands that an Alzheimer's researcher might be found via queries
  like "early dementia detection", "cognitive aging biomarker", or "amyloid
  plaque neuroscience". It generates diverse phrasings — lay terms, technical
  terms, specific and broad — covering the full range of how users search.

Output:
  data/training_pairs.json  — list of {query, bio, faculty_id, faculty_name}

Run after setting CHATBOT_MODEL + API key:
  export OPENAI_API_KEY=sk-...
  export CHATBOT_MODEL=gpt-4o-mini
  python3 pipeline/9_generate_training_data.py
"""
import os, sys, json, time, sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

DB      = os.path.join(_ROOT, "faculty.db")
OUT     = os.path.join(_ROOT, "data", "training_pairs.json")
QUERIES_PER_FACULTY = 5
PAUSE   = 0.3   # seconds between LLM calls

PROMPT_TEMPLATE = """\
You are helping build a faculty search system. Given this DePaul University \
faculty research bio, write {n} different search queries that a graduate student \
or external researcher might type when looking for this person as a collaborator.

Rules:
- Vary phrasing: include both lay descriptions and technical terms
- Make each query realistic — 4-10 words, like a real search
- Do NOT use the faculty member's name in queries
- Output ONLY the queries, one per line, no numbering or explanation

Bio:
{bio}
"""


def generate_queries(bio: str, model: str, n: int = QUERIES_PER_FACULTY) -> list[str]:
    import litellm
    litellm.suppress_debug_info = True
    try:
        resp = litellm.completion(
            model=model,
            max_tokens=200,
            temperature=0.7,
            messages=[{
                "role": "user",
                "content": PROMPT_TEMPLATE.format(n=n, bio=bio[:600]),
            }],
        )
        text = resp.choices[0].message.content.strip()
        queries = [ln.strip("•-– \t") for ln in text.splitlines() if ln.strip()]
        return [q for q in queries if 3 < len(q) < 200][:n]
    except Exception as e:
        print(f"    [LLM error: {e}]")
        return []


def main():
    model = os.environ.get("CHATBOT_MODEL", "")
    if not model:
        sys.exit(
            "Set CHATBOT_MODEL and an API key first.\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "  export CHATBOT_MODEL=gpt-4o-mini"
        )

    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT id, name, research_summary, classes_taught FROM faculty
        WHERE TRIM(COALESCE(research_summary,'')) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
        ORDER BY name
    """).fetchall()
    con.close()
    print(f"Faculty to process: {len(rows)}")

    # Resume: load existing pairs so we can skip already-done faculty
    existing = []
    done_ids = set()
    if os.path.exists(OUT):
        with open(OUT) as f:
            existing = json.load(f)
        done_ids = {p["faculty_id"] for p in existing}
        print(f"Resuming — {len(done_ids)} faculty already done, {len(existing)} pairs so far")

    pairs = list(existing)
    todo  = [r for r in rows if r[0] not in done_ids]
    print(f"To generate: {len(todo)} faculty × {QUERIES_PER_FACULTY} queries "
          f"≈ {len(todo) * QUERIES_PER_FACULTY} new pairs\n")

    for idx, (fac_id, name, summary, courses) in enumerate(todo, 1):
        bio = (summary or courses or "").strip()[:600]
        if not bio or len(bio) < 40:
            print(f"[{idx:3d}/{len(todo)}] {name} — skipped (bio too short)")
            continue

        print(f"[{idx:3d}/{len(todo)}] {name} ...", end=" ", flush=True)
        queries = generate_queries(bio, model)
        if not queries:
            print("no queries generated")
            continue

        for q in queries:
            pairs.append({
                "query":        q,
                "bio":          bio,
                "faculty_id":   fac_id,
                "faculty_name": name,
            })

        print(f"{len(queries)} queries  [{queries[0][:50]}...]")
        time.sleep(PAUSE)

        # Save incrementally every 20 faculty
        if idx % 20 == 0:
            with open(OUT, "w") as f:
                json.dump(pairs, f, indent=2)

    with open(OUT, "w") as f:
        json.dump(pairs, f, indent=2)

    print(f"\nDone — {len(pairs)} training pairs saved to {OUT}")
    print("Next step:")
    print("  python3 pipeline/10_finetune_specter2.py")


if __name__ == "__main__":
    main()
