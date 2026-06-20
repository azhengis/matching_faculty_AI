#!/usr/bin/env python3
"""
search.py  --  DePaul faculty matcher (full-time only)
------------------------------------------------------
Two modes per query:
  s  Semantic      -- faculty whose research is most similar to your topic
  c  Complementary -- faculty from adjacent research areas (different field,
                      synergistic expertise)

Model: allenai/specter2_base  (trained on 146M scientific papers & citations)

SETUP (one time):
    python3 db_setup.py

RUN:
    python3 search.py
"""
import os, sys, sqlite3, pickle, re
import numpy as np

DB         = "faculty.db"
INDEX      = "faculty_index.pkl"
MODEL      = "allenai/specter2_base"
TOP_K      = 5
K_CLUSTERS = 22

STOPWORDS = {
    # English function words
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "that","this","these","those","it","its","i","my","your","their","our",
    "as","than","so","not","no","more","also","both","each","such","about",
    "into","through","between","during","which","who","what","how","when",
    "he","she","they","we","his","her","there","here","can","am","looking",
    "someone","good","someone","want","need","find","looking","someone",
    # Generic academic/query words — appear in almost every faculty bio
    "research","study","studies","work","works","working","focus","focuses",
    "focused","interest","interests","interested","include","includes","including",
    "area","areas","field","fields","topic","topics","subject","subjects",
    "university","professor","faculty","course","courses","student","students",
    "teach","teaching","taught","year","years","depaul","chicago","department",
    "school","college","program","programs","project","projects","current",
    "new","used","using","based","related","different","number","paper",
    "papers","journal","conference","published","publication","publications",
    "approach","approaches","method","methods","methodology","problem","problems",
    "question","questions","experience","expertise","background","academic",
    "work","works","make","makes","use","uses","provide","provides","develop",
    "developed","developing","address","addresses","explore","explores",
}

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    sys.exit("Run:  pip3 install sentence-transformers numpy")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_faculty():
    if not os.path.exists(DB):
        sys.exit(f"Run db_setup.py first to create {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM faculty WHERE TRIM(research_summary) != ''"
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def build_text(p):
    parts = [p["name"]]
    if p.get("department"):
        parts.append(p["department"])
    parts.append(p["research_summary"])
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Explanation: find the sentence from their bio most relevant to the query
# ---------------------------------------------------------------------------

def clean_query(q):
    """Strip colloquial framing so SPECTER2 gets topic-style text to embed."""
    patterns = [
        r"i(?:'m| am) looking for (?:someone )?(?:who )?(?:is )?(?:good at |working on |specializ(?:es|ing) in )?",
        r"i (?:want|need) (?:to find )?(?:someone )?(?:who )?",
        r"find (?:me )?(?:someone )?(?:who )?",
        r"can you (?:find|recommend|suggest)",
        r"(?:for |in) my research(?: problem)?",
        r"who (?:is |are )?(?:good at |working on )?",
        r"\b(?:good at|interested in|focusing on)\b",
    ]
    cleaned = q
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" ,.")


def query_keywords(query):
    words = re.findall(r"[a-z]+", clean_query(query).lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def first_sentence(research_summary):
    """Return the first meaningful sentence — best overview of what someone does."""
    sentences = [s.strip() for s in re.split(r"[.!?\n]", research_summary) if len(s.strip()) > 60]
    if sentences:
        return sentences[0]
    # Bio is all short fragments (keyword lists) — join the first few
    fragments = [s.strip() for s in re.split(r"[.!?\n]", research_summary) if len(s.strip()) > 5]
    return "; ".join(fragments[:4]) if fragments else research_summary[:200]


def best_sentence(query, research_summary):
    """Return the single sentence from research_summary most relevant to query.

    Scores sentences by how many meaningful query keywords they contain,
    ignoring generic words that appear in every academic bio.
    """
    keywords = query_keywords(query)
    sentences = [s.strip() for s in re.split(r"[.!?\n]", research_summary) if len(s.strip()) > 30]
    if not sentences:
        return research_summary[:200]

    # If no meaningful keywords survive filtering, show the first sentence
    if not keywords:
        return sentences[0]

    def score(s):
        words = set(re.findall(r"[a-z]+", s.lower()))
        return len(keywords & words)

    scored = [(score(s), s) for s in sentences]
    best_score, best = max(scored, key=lambda x: x[0])

    # No keyword overlap at all → show whichever sentence is shortest and most
    # specific-looking (avoid long boilerplate sentences)
    if best_score == 0:
        best = min(sentences, key=len) if len(sentences) > 1 else sentences[0]

    return best.strip()


# ---------------------------------------------------------------------------
# K-means (numpy only)
# ---------------------------------------------------------------------------

def kmeans(X, k, n_iter=80, seed=42):
    rng = np.random.default_rng(seed)
    centroids = X[rng.choice(len(X), k, replace=False)].copy()
    labels = np.zeros(len(X), dtype=int)
    for _ in range(n_iter):
        sims   = X @ centroids.T
        labels = np.argmax(sims, axis=1)
        for i in range(k):
            members = X[labels == i]
            if len(members) == 0:
                centroids[i] = X[rng.integers(len(X))]
            else:
                c    = members.mean(axis=0)
                norm = np.linalg.norm(c)
                centroids[i] = c / norm if norm > 1e-8 else c
    return labels, centroids


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def get_index(people, model):
    if os.path.exists(INDEX):
        with open(INDEX, "rb") as f:
            cache = pickle.load(f)
        if cache.get("count") == len(people) and cache.get("model") == MODEL:
            print(f"Loaded cached index ({len(people)} faculty, {K_CLUSTERS} clusters).\n")
            return cache["emb"], cache["labels"], cache["centroids"]

    print(f"Building SPECTER2 embeddings for {len(people)} faculty (one-time, ~1-2 min)...")
    emb = model.encode(
        [build_text(p) for p in people],
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    print(f"Clustering into {K_CLUSTERS} research-topic groups...")
    labels, centroids = kmeans(emb, K_CLUSTERS)

    with open(INDEX, "wb") as f:
        pickle.dump({"count": len(people), "model": MODEL,
                     "emb": emb, "labels": labels, "centroids": centroids}, f)
    print("Index built and cached.\n")
    return emb, labels, centroids


# ---------------------------------------------------------------------------
# Hybrid scoring: SPECTER2 semantic + keyword presence
# ---------------------------------------------------------------------------

def kw_presence_score(query, text):
    """Fraction of meaningful query keywords found anywhere in the text."""
    keywords = query_keywords(query)
    if not keywords:
        return 0.5   # neutral when query has no meaningful keywords
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return hits / len(keywords)


def alpha_for_query(query):
    """Shorter queries rely more on keywords; longer queries trust SPECTER2 more."""
    n_keywords = len(query_keywords(query))
    if n_keywords <= 1:
        return 0.45   # almost 50/50 — single word, SPECTER2 unreliable alone
    if n_keywords <= 3:
        return 0.65   # moderate blend
    return 0.80       # long descriptive query → mostly trust SPECTER2


def hybrid_scores(query, qv, emb, people):
    semantic_sims = emb @ qv
    alpha = alpha_for_query(query)
    scores = np.array([
        alpha * float(semantic_sims[i]) + (1 - alpha) * kw_presence_score(query, people[i]["research_summary"])
        for i in range(len(people))
    ])
    return scores


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def semantic(query, qv, emb, people):
    scores = hybrid_scores(query, qv, emb, people)
    top    = np.argsort(scores)[::-1][:TOP_K]
    return [(people[i], float(scores[i]), None) for i in top]


def complementary(query, qv, emb, labels, people):
    scores = hybrid_scores(query, qv, emb, people)

    # Exclude the clusters the top semantic matches belong to
    top_semantic_idx  = np.argsort(scores)[::-1][:TOP_K * 2]
    semantic_clusters = {int(labels[i]) for i in top_semantic_idx}

    candidates = [
        (people[i], float(scores[i]), int(labels[i]))
        for i in range(len(people))
        if labels[i] not in semantic_clusters
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:TOP_K]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def show(results, query, mode):
    label = "complementary" if mode == "c" else "semantic"
    print(f"\nTop {TOP_K} {label} matches:\n")

    for rank, (p, score, _) in enumerate(results, 1):
        dept      = p.get("department") or p.get("college", "")
        title     = p.get("title", "")
        email     = p.get("email", "")
        bio_url   = p.get("bio_url", "")
        if mode == "c":
            # Complementary = different field; show what they DO, not what overlaps
            why_label = "What they bring"
            reason    = first_sentence(p["research_summary"])
        else:
            why_label = "Why they match"
            reason    = best_sentence(query, p["research_summary"])

        print(f"{rank}. {p['name']}  —  {score*100:.0f}% similarity")
        print(f"   {title}  |  {dept}")
        if email:
            print(f"   {email}")
        print(f"   {why_label}: \"{reason}\"")
        print(f"   {bio_url}")
        print()

    print("-" * 65)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    people = load_faculty()
    print(f"Loading SPECTER2 (academic embedding model)...")
    model  = SentenceTransformer(MODEL)
    emb, labels, centroids = get_index(people, model)

    print(f"Ready — {len(people)} full-time faculty indexed.")
    print("Modes:  s = semantic (similar research)   c = complementary")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            q = input("Query: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"quit", "exit", "q"}:
            break

        raw_mode = input("Mode (s / c) [default s]: ").strip().lower() or "s"
        mode     = "c" if raw_mode.startswith("c") else "s"

        topic = clean_query(q) or q   # strip colloquial framing before encoding
        qv    = model.encode([topic], normalize_embeddings=True)[0]

        results = complementary(q, qv, emb, labels, people) if mode == "c" else semantic(q, qv, emb, people)
        show(results, q, mode)

    print("\nBye.")


if __name__ == "__main__":
    main()
