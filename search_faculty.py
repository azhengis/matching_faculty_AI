#!/usr/bin/env python3
"""
search_faculty.py  --  your matching engine, working, on your laptop.
---------------------------------------------------------------------
Type a question like "who studies fish evolution?" and it returns the DePaul
faculty whose research is the closest match BY MEANING (not keywords). This is
the semantic-search heart of your product, running locally and free.

SETUP (one time):
    pip3 install --break-system-packages --user sentence-transformers numpy

    (This is a bigger install than before -- it pulls in the embedding model
     library. Give it a few minutes. The first run also downloads a small
     ~90 MB model, once.)

RUN:
    python3 search_faculty.py

The first run builds embeddings for everyone (takes ~30-60 sec) and caches them,
so every run after that starts instantly. Then just type questions. Type 'quit'
to exit.
"""
import json, os, sys, pickle
import numpy as np

DATA      = "depaul_faculty_enriched.json"
EMB_CACHE = "faculty_embeddings.pkl"
MODEL     = "all-MiniLM-L6-v2"   # small, fast, runs on any laptop, no API
TOP_K     = 5

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    sys.exit("Need the library first. Run:\n"
             "  pip3 install --break-system-packages --user sentence-transformers numpy")


def load_people():
    if not os.path.exists(DATA):
        sys.exit(f"Can't find {DATA}. Run it in the folder that has that file.")
    with open(DATA, encoding="utf-8") as f:
        people = json.load(f)
    # Only people who actually have research/bio text to match on.
    return [p for p in people if (p.get("research_summary") or "").strip()]


def build_text(p):
    """What we embed for each person: name + dept + their research text."""
    return f"{p['name']}. {p.get('department','')}. {p['research_summary']}"


def get_embeddings(people, model):
    # Reuse cached embeddings unless the dataset size changed.
    if os.path.exists(EMB_CACHE):
        with open(EMB_CACHE, "rb") as f:
            cache = pickle.load(f)
        if cache.get("count") == len(people):
            print(f"Loaded cached embeddings for {len(people)} people.\n")
            return cache["emb"]

    print(f"Building embeddings for {len(people)} people (one time)...")
    texts = [build_text(p) for p in people]
    emb = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)
    with open(EMB_CACHE, "wb") as f:
        pickle.dump({"count": len(people), "emb": emb}, f)
    print("Done and cached.\n")
    return emb


def main():
    people = load_people()
    print(f"Matching engine ready: {len(people)} researchers indexed.")
    print("Loading model...")
    model = SentenceTransformer(MODEL)
    emb = get_embeddings(people, model)

    print("Ask me who works on something. Examples:")
    print('  "who studies fish evolution?"   "machine learning for healthcare"')
    print('  "someone doing community-based research with immigrants"')
    print("Type 'quit' to exit.\n")

    while True:
        try:
            q = input("Ask: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"quit", "exit", "q"}:
            break

        qv = model.encode([q], normalize_embeddings=True)[0]
        scores = emb @ qv                      # cosine similarity (vectors are normalized)
        top = np.argsort(scores)[::-1][:TOP_K]

        print()
        for rank, i in enumerate(top, 1):
            p = people[i]
            snippet = " ".join(p["research_summary"].split())[:200]
            dept = p.get("department", "") or p.get("college", "")
            print(f"{rank}. {p['name']}  ({scores[i]*100:.0f}% match)")
            print(f"   {dept}")
            print(f"   {snippet}...")
            print(f"   {p.get('bio_url','')}")
            print()
        print("-" * 60)

    print("\nBye.")


if __name__ == "__main__":
    main()
