#!/usr/bin/env python3
"""
search.py  --  DePaul faculty matcher (full-time only)
------------------------------------------------------
Two modes:
  s  Semantic      -- most similar research
  c  Complementary -- adjacent field, different domain

Extra features:
  - Search by faculty name  ("Casey Bennett")
  - Filter by college / department
  - Result diversity  (max 2 per department)
  - Recency penalty for Emeritus titles
  - Minimum score threshold
  - Feedback refinement after results

Model: allenai/specter2_base

SETUP (one time):
    python3 db_setup.py

RUN:
    python3 search.py
"""
import os, sys, sqlite3, pickle, re, json
import numpy as np

DB          = "faculty.db"
INDEX       = "faculty_index.pkl"
PAPER_INDEX = "paper_index.pkl"
MODEL       = "allenai/specter2"   # adapter version; changed from _base → cache rebuilds
TOP_K            = 5
POOL_SIZE        = 30    # kept for complementary mode
POOL_SIZE_STAGE1 = 25    # SPECTER2 + keywords → cross-encoder
POOL_SIZE_STAGE2 = 7     # cross-encoder → LLM reranker
POOL_SIZE_COMP   = 10    # complementary candidates sent to LLM (no cross-encoder step)
K_CLUSTERS       = 35
MIN_SCORE        = {"s": 0.50, "c": 0.30}

# Cross-encoder lazy-load state (stage 2)
_cross_encoder       = None
_cross_encoder_tried = False

STOPWORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","is","are","was","were","be","been","being","have","has",
    "had","do","does","did","will","would","could","should","may","might",
    "that","this","these","those","it","its","i","my","your","their","our",
    "as","than","so","not","no","more","also","both","each","such","about",
    "into","through","between","during","which","who","what","how","when",
    "he","she","they","we","his","her","there","here","can","am",
    "looking","someone","good","want","need","find","person","people","someone",
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
    "make","makes","use","uses","provide","provides","develop","developed",
    "developing","address","addresses","explore","explores",
}

# Maps individual query keywords to equivalent terms that may appear in faculty text.
# Handles abbreviations (user types "nlp", bio says "natural language processing")
# and vocabulary mismatches (user says "cybersecurity", bio says "information security").
def expand_query_with_llm(query: str) -> dict:
    """Translate a lay-term query into structured academic vocabulary.

    Returns a dict:
      academic_jargon: a phrase of 6-10 academic terms for SPECTER2 encoding
      keywords:        a list of discrete terms for exact keyword matching

    Splitting these lets SPECTER2 get a dense semantic phrase while the keyword
    scorer gets a clean list of high-signal individual terms.
    Falls back to the original query if no LLM is configured.
    """
    fallback = {"academic_jargon": query, "keywords": list(query_keywords(query))}
    model = os.environ.get("CHATBOT_MODEL", "")
    if not model:
        return fallback
    try:
        import litellm
        litellm.suppress_debug_info = True
        resp = litellm.completion(
            model=model,
            max_tokens=150,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": (
                    "Translate this search query into academic vocabulary a researcher would use.\n"
                    "Respond with JSON only — no prose, no markdown:\n"
                    '{"academic_jargon": "<6-10 academic terms as a phrase>", '
                    '"keywords": ["<term1>", "<term2>", "<term3>", ...]}\n\n'
                    f"Query: {query}"
                ),
            }],
        )
        content = resp.choices[0].message.content.strip()
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            data = json.loads(m.group())
            if isinstance(data.get("academic_jargon"), str) and isinstance(data.get("keywords"), list):
                data["academic_jargon"] = data["academic_jargon"].strip() or query
                data["keywords"] = [str(k).lower().strip() for k in data["keywords"] if k]
                return data
    except Exception:
        pass
    return fallback

class _SPECTER2Adapter:
    """SPECTER2 base + proximity adapter — better retrieval than base alone.
    Same .encode() interface as sentence_transformers.SentenceTransformer.
    Uses CLS-token pooling as recommended in the SPECTER2 paper.
    """

    def __init__(self):
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer
        import torch
        print("  Downloading/loading base weights (first run only)...")
        self._tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
        self._mdl = AutoAdapterModel.from_pretrained("allenai/specter2_base")
        print("  Loading proximity adapter...")
        self._mdl.load_adapter("allenai/specter2", source="hf",
                                load_as="specter2", set_active=True)
        self._mdl.eval()
        self._torch = torch

    def encode(self, sentences, normalize_embeddings=True,
               show_progress_bar=False, batch_size=32):
        import numpy as np
        all_embs = []
        batches  = range(0, len(sentences), batch_size)
        if show_progress_bar:
            try:
                from tqdm import tqdm
                batches = tqdm(batches, desc="Encoding",
                               total=(len(sentences) + batch_size - 1) // batch_size)
            except ImportError:
                pass
        for i in batches:
            batch = sentences[i : i + batch_size]
            enc   = self._tok(batch, padding=True, truncation=True,
                              return_tensors="pt", max_length=512)
            with self._torch.no_grad():
                out = self._mdl(**enc)
            emb = out.last_hidden_state[:, 0, :].detach().cpu().numpy()  # CLS token
            if normalize_embeddings:
                norms = np.linalg.norm(emb, axis=1, keepdims=True)
                emb   = emb / np.maximum(norms, 1e-8)
            all_embs.append(emb)
        return np.vstack(all_embs) if all_embs else np.empty((0, 768))


def load_model():
    """Return the best available SPECTER2 model.

    Priority order:
      1. Fine-tuned DePaul model (FINETUNED_MODEL env var set after running
         pipeline/10_finetune_specter2.py)
      2. SPECTER2 + proximity adapter  (best off-the-shelf accuracy)
      3. SPECTER2 base  (fallback if 'adapters' library not installed)
    """
    finetuned_path = os.environ.get("FINETUNED_MODEL", "")
    if finetuned_path and os.path.isdir(finetuned_path):
        from sentence_transformers import SentenceTransformer
        print(f"  Using fine-tuned DePaul model: {finetuned_path}")
        return SentenceTransformer(finetuned_path)

    try:
        import adapters  # noqa: F401 — only checking availability
        print("  Using SPECTER2 + proximity adapter  (best accuracy)")
        return _SPECTER2Adapter()
    except ImportError:
        print("  Using specter2_base  (install 'adapters' for improved accuracy)")
    except Exception as e:
        print(f"  Adapter load failed ({e}), falling back to base model")
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("allenai/specter2_base")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

FRAGMENT_STARTERS = re.compile(
    r"^(are|is|include|includes|focus|focuses|have|has|span|spans|center|centers|"
    r"cover|covers|examine|examines|explore|explores|involve|involves|"
    r"range|ranges|consist|consists)\b",
    re.IGNORECASE,
)

def fix_summary(text):
    text = text.strip()
    if not text:
        return text
    if text[0].islower() or FRAGMENT_STARTERS.match(text):
        text = "Research interests " + text[0].lower() + text[1:]
    return text


def is_biographical(summary, name):
    """Return True if summary is just a bio paragraph, not a research description.
    Detected when the summary opens with the person's own name (scraper fallback)."""
    if not summary or not name:
        return False
    parts = name.strip().split()
    first, last = parts[0].lower(), parts[-1].lower()
    opening = summary.strip()[:80].lower()
    return opening.startswith(first) or opening.startswith(last) or opening.startswith("dr. " + last)


def clean_courses(text):
    """Strip website footer boilerplate from classes_taught text."""
    text = re.sub(r"DePaul University.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\(?\d{3}\)?\s*\d{3}[-.\s]\d{4}", "", text)   # phone numbers
    text = re.sub(r"\b1\s*E\.?\s*Jackson.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_faculty():
    if not os.path.exists(DB):
        sys.exit(f"Run db_setup.py first to create {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # Load faculty who have a research summary OR courses taught
    rows = con.execute("""
        SELECT * FROM faculty
        WHERE TRIM(research_summary) != ''
           OR TRIM(COALESCE(classes_taught,'')) != ''
    """).fetchall()

    # Load paper titles per faculty for keyword boosting at query time
    paper_title_rows = con.execute(
        "SELECT faculty_id, GROUP_CONCAT(title, ' | ') as titles FROM papers GROUP BY faculty_id"
    ).fetchall()
    paper_titles_by_id = {r["faculty_id"]: r["titles"] for r in paper_title_rows}
    con.close()

    people = [dict(r) for r in rows]
    for p in people:
        p["pub_titles"] = paper_titles_by_id.get(p["id"], "")

    for p in people:
        summary = fix_summary(p.get("research_summary") or "")
        courses = clean_courses(p.get("classes_taught") or "")

        if is_biographical(summary, p["name"]) and courses:
            summary = f"Courses taught: {courses}\n\n{summary}"
            p["summary_source"] = "courses"
        elif not summary and courses:
            summary = f"Courses taught: {courses}"
            p["summary_source"] = "courses"
        else:
            p["summary_source"] = "research"

        p["research_summary"] = summary

    # Drop anyone who still has nothing useful
    return [p for p in people if p["research_summary"].strip()]


def build_text(p):
    parts = [p["name"]]
    if p.get("department"):
        parts.append(p["department"])
    parts.append(p["research_summary"])
    pubs = (p.get("publications_text") or "").strip()
    if pubs:
        parts.append(pubs[:600])
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def clean_query(q):
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


# ---------------------------------------------------------------------------
# Explanation helpers
# ---------------------------------------------------------------------------

# Verbs that signal the sentence is describing real research activity,
# not just a biographical identifier ("is an associate professor at...")
_RESEARCH_VERBS = re.compile(
    r"\b(studies|investigates|examine[sd]|explore[sd]|develop[sd]|focuses|focus|"
    r"analyzes|analys|designs|creates|address|applies|integrates|specializes|"
    r"researches|published|publishes|applies|pioneer|lead[s]?|direct[s]?|"
    r"is focused|are focused|has (?:worked|published|developed|studied)|"
    r"have (?:worked|published|developed|studied))\b",
    re.IGNORECASE,
)

def _has_research_verb(s):
    return bool(_RESEARCH_VERBS.search(s))


def _split_sentences(text):
    """Split on sentence-ending punctuation OR blank lines, return non-empty chunks."""
    parts = re.split(r"(?<=[.!?])\s{1,3}(?=[A-Z])|\n{1,}", text)
    return [p.strip() for p in parts if p.strip()]


def _is_bio_opener(s, name):
    """True if sentence just introduces the person by name — not useful as a match reason."""
    if not name:
        return False
    parts = name.strip().split()
    first, last = parts[0].lower(), parts[-1].lower()
    opening = s.strip()[:80].lower()
    return (opening.startswith(first) or opening.startswith(last)
            or opening.startswith("dr. " + last)
            or re.match(r"^(he|she|they) (is|are|was|were)\b", opening))


def explain_match(query, research_summary, name=""):
    """
    Return the sentence (or short passage) from research_summary that best
    explains WHY this faculty member matches the query.

    Improvements over the old best_sentence():
      - Skips biographical openers (sentences that just name the person)
      - Prefers sentences with research-activity verbs over noun-phrase lists
      - For courses-based summaries, collects the matching course names
      - Expands short topic fragments into a natural phrase
    """
    keywords = query_keywords(query)

    # --- Special case: courses-based summary ---
    if research_summary.lstrip().startswith("Courses taught:"):
        # Extract course list (everything before the first blank line / bio section)
        course_block = re.sub(r"^Courses taught:\s*", "", research_summary, flags=re.IGNORECASE)
        if "\n\n" in course_block:
            course_block = course_block.split("\n\n")[0]
        courses = [c.strip() for c in re.split(r"[\n,;]", course_block) if c.strip()]

        if keywords:
            matching = [c for c in courses
                        if any(kw in c.lower() for kw in keywords)]
            display  = matching[:4] if matching else courses[:3]
        else:
            display = courses[:3]

        if display:
            return "Teaches: " + ", ".join(display)

    # --- Normal path ---
    sentences = _split_sentences(research_summary)
    # Filter to uppercase-starting, minimum 10 chars
    sentences = [s for s in sentences if s and s[0].isupper() and len(s) > 10]
    if not sentences:
        return research_summary[:220]

    # Separate out biographical openers — keep them only as last resort
    non_bio   = [s for s in sentences if not _is_bio_opener(s, name)]
    pool      = non_bio if non_bio else sentences

    if not keywords:
        # Return first sentence with a research verb; else first non-bio sentence
        for s in pool:
            if _has_research_verb(s) and len(s) > 35:
                return s[:220]
        return pool[0][:220]

    # Sentences that are mainly publication venue lists — e.g. "published in the
    # Journal of Banking and Finance, the Journal of Accounting..." — score
    # high on keywords like "finance" but tell the user nothing about research.
    _PUB_LIST_RE = re.compile(r"journal of|proceedings of|published in the|\bieee \b|\bacm \b", re.IGNORECASE)

    def count_hits(s):
        words = set(re.findall(r"[a-z]+", s.lower()))
        exact = len(keywords & words)
        return float(exact)

    def score(s):
        hits        = count_hits(s)
        verb_bonus  = 0.4 if _has_research_verb(s) else 0.0
        len_bonus   = min(len(s) / 250, 0.5)
        pub_penalty = -1.5 if _PUB_LIST_RE.search(s) else 0.0
        return hits + verb_bonus + len_bonus + pub_penalty

    ranked = sorted(pool, key=score, reverse=True)
    best   = ranked[0]
    best_kw_hits = count_hits(best)

    # If best is a short noun-phrase (no verb, < 55 chars), try to expand
    if len(best) < 55 and not _has_research_verb(best):
        # Try next-best sentence that has a verb and at least partial keyword hit
        for s in ranked[1:5]:
            if _has_research_verb(s) and len(s) > 35:
                if len(keywords & set(re.findall(r"[a-z]+", s.lower()))) >= max(1, best_kw_hits - 1):
                    best = s
                    break
        # Still short? Wrap it naturally
        if len(best) < 55:
            best = "Their work focuses on " + best[0].lower() + best[1:]

    # If no keywords hit at all, fall back to first non-bio sentence with a verb
    if best_kw_hits == 0:
        for s in pool:
            if _has_research_verb(s) and len(s) > 35:
                return s[:220]
        return pool[0][:220]

    return best[:220]


def first_sentence(research_summary, name=""):
    """Used for complementary mode — give a general overview of what this person does."""
    sentences = _split_sentences(research_summary)
    sentences = [s for s in sentences if s and s[0].isupper() and len(s) > 30]
    non_bio   = [s for s in sentences if not _is_bio_opener(s, name)]
    pool      = non_bio if non_bio else sentences
    if not pool:
        return research_summary[:200]
    # Prefer first sentence with a research verb
    for s in pool:
        if _has_research_verb(s):
            return s[:220]
    return pool[0][:220]


# ---------------------------------------------------------------------------
# K-means (numpy only)
# ---------------------------------------------------------------------------

def kmeans(X, k, n_iter=80, seed=42):
    rng = np.random.default_rng(seed)
    centroids = X[rng.choice(len(X), k, replace=False)].copy()
    labels    = np.zeros(len(X), dtype=int)
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

    print(f"Building SPECTER2 embeddings for {len(people)} faculty (one-time ~1-2 min)...")
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


def get_paper_index(people, model):
    """Load or build per-paper SPECTER2 embeddings from the papers table."""
    if not os.path.exists(DB):
        return None

    con = sqlite3.connect(DB)
    papers_rows = con.execute(
        "SELECT faculty_id, title, abstract, year, cited_by_count FROM papers ORDER BY faculty_id, cited_by_count DESC"
    ).fetchall()
    con.close()

    if not papers_rows:
        return None  # fetch_papers.py hasn't been run yet

    # Build a lookup: faculty_id -> db row "id"
    fac_id_to_idx = {p["id"]: i for i, p in enumerate(people)}

    # Filter to papers whose faculty are in our searchable people list
    fac_ids_in_index = {p["id"] for p in people}
    papers_rows = [r for r in papers_rows if r[0] in fac_ids_in_index]

    if not papers_rows:
        return None

    # Check cache
    if os.path.exists(PAPER_INDEX):
        with open(PAPER_INDEX, "rb") as f:
            cache = pickle.load(f)
        if cache.get("n_papers") == len(papers_rows) and cache.get("model") == MODEL:
            print(f"Loaded paper index ({len(papers_rows)} papers for {len(cache['by_faculty'])} faculty).\n")
            return cache

    print(f"Building paper embeddings ({len(papers_rows)} papers)...")
    texts = [
        f"{r[1]}. {r[2][:600]}" if r[2] else r[1]   # title + abstract (truncated)
        for r in papers_rows
    ]
    embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    # Group paper indices by faculty_id
    by_faculty = {}
    meta = []
    for global_idx, r in enumerate(papers_rows):
        fac_id = r[0]
        if fac_id not in by_faculty:
            by_faculty[fac_id] = []
        by_faculty[fac_id].append(global_idx)
        meta.append((fac_id, r[1], r[3], r[4]))  # (faculty_id, title, year, cited_by_count)

    cache = {
        "n_papers":  len(papers_rows),
        "model":     MODEL,
        "embs":      embs,
        "by_faculty": by_faculty,
        "meta":      meta,
    }
    with open(PAPER_INDEX, "wb") as f:
        pickle.dump(cache, f)
    print(f"Paper index built ({len(by_faculty)} faculty with publications).\n")
    return cache


def find_best_paper(faculty_id, qv, paper_idx):
    """Return (title, year, cited_by_count, similarity) for the best-matching paper."""
    top = find_top_papers(faculty_id, qv, paper_idx, n=1, min_sim=0.0)
    return tuple(top[0]) if top else None


def find_top_papers(faculty_id, qv, paper_idx, n=2, min_sim=0.55):
    """Return up to n papers sorted by SPECTER2 similarity to the query.
    Each result is (title, year, cited_by_count, similarity).
    """
    if paper_idx is None or faculty_id not in paper_idx["by_faculty"]:
        return []
    indices = paper_idx["by_faculty"][faculty_id]
    embs    = paper_idx["embs"][indices]
    sims    = embs @ qv
    order   = np.argsort(sims)[::-1]
    results = []
    for i in order:
        sim = float(sims[i])
        if sim < min_sim or len(results) >= n:
            break
        _, title, year, cited = paper_idx["meta"][indices[i]]
        results.append((title, year, cited, sim))
    return results


# ---------------------------------------------------------------------------
# Search by name
# ---------------------------------------------------------------------------

def find_by_name(query, people, emb):
    """If query matches a faculty name, return (person, their_embedding)."""
    q = query.lower().strip()
    for i, p in enumerate(people):
        if q == p["name"].lower():
            return p, emb[i]
    # Partial: every word in query appears in the name
    q_words = set(q.split())
    if len(q_words) >= 2:
        for i, p in enumerate(people):
            name_words = set(p["name"].lower().split())
            if q_words <= name_words:
                return p, emb[i]
    return None, None


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def apply_filters(people, emb, college_filter=None, dept_filter=None):
    if not college_filter and not dept_filter:
        return people, emb
    indices = [
        i for i, p in enumerate(people)
        if (not college_filter or college_filter in (p.get("college") or "").lower())
        and (not dept_filter   or dept_filter   in (p.get("department") or "").lower())
    ]
    if not indices:
        print("  No faculty match those filters — ignoring filters.\n")
        return people, emb
    print(f"  Filter applied: {len(indices)} faculty match.\n")
    return [people[i] for i in indices], emb[np.array(indices)]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def recency_penalty(p):
    """Penalise Emeritus faculty — their research may be decades old."""
    if "emerit" in (p.get("title") or "").lower():
        return 0.88
    return 1.0


def kw_presence_score(query, text):
    """Exact-match keyword overlap between query terms and faculty text.

    Synonym expansion is handled upstream by expand_query_with_llm(), so the
    query arriving here already uses academic vocabulary. This function is a
    literal sanity check: if the LLM-expanded query terms appear nowhere in
    the faculty bio, SPECTER2 must be finding a spurious connection.
    """
    keywords = query_keywords(query)
    if not keywords:
        return 0.5
    text_lower = text.lower()
    hits = sum(1.0 for kw in keywords if kw in text_lower)
    return hits / len(keywords)


def alpha_for_query(query, source="research"):
    n = len(query_keywords(query))
    if source == "courses":
        # Courses-based summaries get slightly lower SPECTER2 weight than research
        # summaries because course lists are less semantically dense than prose bios.
        # Raised from 0.25/0.45/0.60 — course *names* ("Machine Learning", "Computer
        # Vision") are genuinely informative and shouldn't be heavily discounted.
        if n <= 1: return 0.38
        if n <= 3: return 0.58
        return 0.72
    if n <= 1: return 0.45
    if n <= 3: return 0.65
    return 0.80


def zero_kw_penalty(query, kw, specter_sim=None):
    """
    When a query has 3+ distinct keywords and a result matches few of them,
    SPECTER2 is likely finding a spurious semantic connection via shared
    domain vocabulary.

    Exception: when SPECTER2 similarity is very high (>= 0.87), trust it
    even without strong keyword overlap. At that similarity level the model
    is almost certainly right — it's encoding a genuine topic match that the
    lay query terms don't expose. This handles experts who publish in academic
    vocabulary (e.g. 'Alzheimer's disease neurodegeneration') when the query
    uses lay terms ('memory loss in elderly').

    Penalty tiers:
      - 0 keyword hits on a 3+ keyword query → 0.50
      - <15% keyword hits on a 5+ keyword query → 0.60  (e.g. 1/7 = 0.14)
      - <26% keyword hits on a 4+ keyword query → 0.75  (e.g. 1/4 = 0.25)
    """
    if specter_sim is not None and specter_sim >= 0.85:
        return 1.0  # SPECTER2 is very confident — trust it over keyword gate
    n_keywords = len(query_keywords(query))
    if n_keywords >= 3 and kw == 0.0:
        return 0.50
    if n_keywords >= 5 and kw < 0.15:   # only 1 of 7+ keywords matched
        return 0.60
    if n_keywords >= 4 and kw < 0.26:   # only 1 of 4+ keywords matched
        return 0.75
    return 1.0


def hybrid_scores(query, qv, emb, people, kw_list=None):
    """Score all faculty against the query.

    kw_list: list of discrete keywords from LLM expansion (stage 1 structured output).
             When provided, used for keyword overlap instead of tokenising query string.
             This keeps SPECTER2 encoding (jargon phrase) and keyword matching (term list) separate.
    """
    sims = emb @ qv

    # Build keyword set once — prefer LLM list, fall back to tokenising query
    if kw_list:
        kw_set = {k.lower().strip() for k in kw_list if k.strip()}
    else:
        kw_set = query_keywords(query)
    n_kw = len(kw_set)

    scores = []
    for i in range(len(people)):
        src        = people[i].get("summary_source", "research")
        a          = alpha_for_query(query, src)
        text_lower = people[i]["research_summary"].lower()
        hits       = sum(1.0 for kw in kw_set if kw in text_lower) if kw_set else 0
        kw         = (hits / n_kw) if n_kw > 0 else 0.5
        raw        = a * float(sims[i]) + (1 - a) * kw
        scores.append(recency_penalty(people[i]) * raw * zero_kw_penalty(query, kw, float(sims[i])))
    return np.array(scores)


# ---------------------------------------------------------------------------
# Stage 2 — Cross-encoder reranking (local, free, ~80ms for 25 pairs)
# ---------------------------------------------------------------------------

def load_cross_encoder():
    global _cross_encoder, _cross_encoder_tried
    if _cross_encoder_tried:
        return _cross_encoder
    _cross_encoder_tried = True
    try:
        from sentence_transformers import CrossEncoder
        print("  Loading cross-encoder (stage 2, one-time ~30s download)...")
        # MiniLM-L-12 is ~120MB: fast, good quality, no GPU needed
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2", max_length=512)
        print("  Cross-encoder ready.")
    except Exception as e:
        print(f"  Cross-encoder unavailable ({e}) — stage 2 skipped")
        _cross_encoder = None
    return _cross_encoder


def cross_rerank(query, candidates, top_n=POOL_SIZE_STAGE2):
    """Stage 2: cross-encoder re-scores (query, bio) pairs together.

    Unlike SPECTER2 (which encodes query and bio independently), the cross-encoder
    sees both simultaneously so its attention can cross between them — better at
    catching mismatches like 'biomedical image analysis' vs 'Alzheimer's research'.
    """
    ce = load_cross_encoder()
    if ce is None or not candidates:
        return candidates[:top_n]
    pairs  = [(query, p["research_summary"][:512]) for p, _, _ in candidates]
    scores = ce.predict(pairs)
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return [c for c, _ in ranked[:top_n]]


# ---------------------------------------------------------------------------
# Stage 3 — LLM reranking (Claude Haiku, ~500ms, ~$0.001 per query)
# ---------------------------------------------------------------------------

def llm_rerank(original_query, candidates, expansion, mode="semantic"):
    """LLM reranking for both semantic and complementary modes.

    mode="semantic":      score direct relevance; penalise adjacent fields.
    mode="complementary": score interdisciplinary value; exclude non-researchers.
    """
    model_name = os.environ.get("CHATBOT_MODEL", "")
    if not model_name or not candidates:
        return candidates[:TOP_K]

    try:
        import litellm
        litellm.suppress_debug_info = True

        academic_query = expansion.get("academic_jargon", original_query) if isinstance(expansion, dict) else original_query

        bios = []
        for i, (p, _, _) in enumerate(candidates):
            bios.append(
                f"[{i+1}] {p['name']} | {p.get('department', '')}\n"
                f"{p['research_summary'][:380]}"
            )
        bio_block = "\n---\n".join(bios)

        if mode == "complementary":
            prompt = (
                f"A researcher is exploring: \"{original_query}\"\n"
                f"Academic framing: \"{academic_query}\"\n\n"
                f"These professors work in DIFFERENT fields. Score each (0-10) on how "
                f"valuable they would be as a COMPLEMENTARY collaborator — someone who brings "
                f"a different methodology, dataset, or perspective that enriches this research.\n"
                f"Scoring: 0-2 = no research role or zero connection, 3-5 = very weak link, "
                f"6-8 = meaningful complementary angle, 9-10 = strong cross-disciplinary fit.\n"
                f"Give score 0 to anyone who is clearly an administrator, fundraiser, or "
                f"staff with no active research.\n\n"
                f"{bio_block}\n\n"
                f"Respond with a JSON array only, no prose:\n"
                f'[{{"id": 1, "score": 7, "reason": "one sentence on complementary value"}}, ...]'
            )
        else:
            prompt = (
                f"A user searched for: \"{original_query}\"\n"
                f"Academic interpretation: \"{academic_query}\"\n\n"
                f"Rate each professor's relevance to this specific query (0-10).\n"
                f"Scoring: 0-3 = unrelated field, 4-6 = adjacent/tangential, 7-10 = direct match.\n"
                f"Be strict: adjacent fields (e.g. biomedical imaging vs. disease research) score 3-5, not 7+.\n\n"
                f"{bio_block}\n\n"
                f"Respond with a JSON array only, no prose:\n"
                f'[{{"id": 1, "score": 8, "reason": "one sentence why"}}, ...]'
            )

        print(f"  [LLM rerank: scoring {len(candidates)} {mode} candidates...]")
        resp = litellm.completion(
            model=model_name,
            max_tokens=900,   # 10 candidates × ~50 tokens each + overhead
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.choices[0].message.content.strip()

        # Strip markdown code-fences (```json ... ```) before parsing
        content_clean = re.sub(r'```(?:json)?\s*', '', content).strip()
        m = re.search(r'\[.*\]', content_clean, re.DOTALL)
        if not m:
            print(f"  [LLM rerank: no JSON array found in response — skipping]")
            return candidates[:TOP_K]

        try:
            ratings = json.loads(m.group())
        except json.JSONDecodeError as e:
            print(f"  [LLM rerank: JSON parse error ({e}) — skipping]")
            return candidates[:TOP_K]
        scored  = []
        for r in ratings:
            idx = int(r.get("id", 0)) - 1
            if not (0 <= idx < len(candidates)):
                continue
            p, orig_score, label = candidates[idx]
            llm_score = max(0.0, min(10.0, float(r.get("score", 0)))) / 10.0
            blended   = 0.60 * llm_score + 0.40 * orig_score
            reason    = str(r.get("reason", "")).strip()
            scored.append((p, blended, label, reason))

        scored.sort(key=lambda x: x[1], reverse=True)

        result = []
        for p, blended, label, reason in scored[:TOP_K]:
            p = dict(p)
            if reason:
                p["_llm_reason"] = reason
            result.append((p, blended, label))
        return result

    except Exception as e:
        print(f"  [LLM rerank failed: {e}]")
        return candidates[:TOP_K]


# ---------------------------------------------------------------------------
# Diversity filter
# ---------------------------------------------------------------------------

def diversity_filter(candidates):
    """Return top TOP_K with a soft per-department cap.

    Default cap is 2 per department (prevents one field monopolising results
    for broad queries). For a strong, specific query — where multiple faculty
    from the same department all score well — the cap rises to 3, so a user
    explicitly looking for a CS or nursing collaborator gets more options.

    'Strong match' threshold: score >= 0.65 (above the weak-match warning line).
    """
    dept_count = {}
    out = []
    for p, score, label in candidates:
        dept = (p.get("department") or p.get("college") or "Unknown")
        cap  = 3 if score >= 0.65 else 2
        if dept_count.get(dept, 0) < cap:
            out.append((p, score, label))
            dept_count[dept] = dept_count.get(dept, 0) + 1
        if len(out) >= TOP_K:
            break
    return out


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------

def semantic(query, qv, emb, people, expansion=None):
    """Three-stage hybrid cascade.

    Stage 1 — SPECTER2 + keywords  →  top 25 candidates  (fast, in-memory)
    Stage 2 — Cross-encoder        →  top 7  candidates  (local, ~80ms)
    Stage 3 — LLM reranking        →  final 5            (~500ms, ~$0.001)

    expansion: dict from expand_query_with_llm() with keys academic_jargon / keywords.
               When None (e.g. refine-by-name mode), stages 1-2 still run; stage 3 skipped.
    """
    kw_list = expansion.get("keywords") if isinstance(expansion, dict) else None

    # Stage 1
    scores     = hybrid_scores(query, qv, emb, people, kw_list=kw_list)
    top        = np.argsort(scores)[::-1][:POOL_SIZE_STAGE1]
    stage1     = [(people[i], float(scores[i]), None) for i in top]

    # Stage 2 — cross-encoder (uses academic_jargon phrase for richer signal)
    ce_query   = expansion.get("academic_jargon", query) if isinstance(expansion, dict) else query
    stage2     = cross_rerank(ce_query, stage1, top_n=POOL_SIZE_STAGE2)

    # Stage 3 — LLM reranking (only when expansion is available, i.e. real user query)
    if isinstance(expansion, dict) and os.environ.get("CHATBOT_MODEL"):
        stage3 = llm_rerank(query, stage2, expansion)
    else:
        stage3 = stage2[:TOP_K]

    return diversity_filter(stage3)


def complementary(query, qv, emb, labels, people, n_skip=2, expansion=None):
    """Find faculty from different research clusters who could complement the query topic.

    n_skip controls how different: 1=adjacent, 2=moderate, 3=very different.

    Pipeline:
      Stage 1 — exclude the top semantic clusters, rank remaining by hybrid score
      Stage 3 — LLM scores complementary value and eliminates non-researchers
      (No cross-encoder: its relevance metric is wrong here — high score = direct
       match, which is exactly what complementary mode excludes.)
    """
    kw_list = expansion.get("keywords") if isinstance(expansion, dict) else None

    scores           = hybrid_scores(query, qv, emb, people, kw_list=kw_list)
    top_semantic_idx = np.argsort(scores)[::-1][:TOP_K * n_skip]
    excluded         = {int(labels[i]) for i in top_semantic_idx}

    candidates = [
        (people[i], float(scores[i]), int(labels[i]))
        for i in range(len(people))
        if labels[i] not in excluded
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    pool = candidates[:POOL_SIZE_COMP]

    if isinstance(expansion, dict) and os.environ.get("CHATBOT_MODEL"):
        pool = llm_rerank(query, pool, expansion, mode="complementary")

    return diversity_filter(pool)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _score_tier(score):
    """Translate a raw hybrid score into a human-readable quality label.

    Replaces the raw percentage which was misleading as an absolute number —
    two results at 87% and 72% can both be excellent matches; what matters
    is whether the score clears the meaningful thresholds.
    """
    if score >= 0.80: return "Strong"
    if score >= 0.65: return "Good"
    if score >= 0.50: return "Possible"
    return "Weak"


def show(results, query, mode, qv=None, paper_idx=None):
    label = "complementary" if mode == "c" else "semantic"
    threshold = MIN_SCORE[mode]
    print(f"\nTop {len(results)} {label} matches:\n")
    if results and results[0][1] < 0.65:
        print("Note: No strong faculty matches found — this topic may not be a current")
        print("      DePaul research specialty. Results below are closest available.\n")

    for rank, (p, score, _) in enumerate(results, 1):
        dept    = p.get("department") or p.get("college", "")
        title   = p.get("title", "")
        email   = p.get("email", "")
        bio_url = p.get("bio_url", "")

        if mode == "c":
            why_label = "What they bring"
            reason    = (p.get("_llm_reason")
                         or first_sentence(p["research_summary"], name=p.get("name","")))
        elif p.get("_llm_reason"):
            # Stage 3 LLM provided a direct explanation — use it
            why_label = "Why they match"
            reason    = p["_llm_reason"]
        else:
            why_label = "Why they match"
            reason    = explain_match(query, p["research_summary"], name=p.get("name",""))

        tier = _score_tier(score)
        flag = "  ⚠" if score < threshold else ""
        print(f"{rank}. {p['name']}  —  {tier}{flag}  ({score*100:.0f})")
        print(f"   {title}  |  {dept}")
        if email:
            print(f"   {email}")
        print(f"   {why_label}: \"{reason}\"")

        # Second context line — extra evidence beyond the one-sentence match reason
        summary = p.get("research_summary", "")
        if summary.lstrip().startswith("Courses taught:"):
            # For courses-based faculty, the match reason already shows courses —
            # add a brief note so users know this person is matched via teaching, not a bio
            print(f"   (matched via courses taught — no research bio available)")
        else:
            # Try to find a second non-overlapping sentence from the bio
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", summary) if len(s.strip()) > 40]
            non_bio   = [s for s in sentences if not _is_bio_opener(s, p.get("name", ""))]
            # Pick a sentence that isn't the same as reason (different content)
            for s in non_bio:
                if s[:60] not in reason[:60] and s != reason:
                    print(f"   Also: \"{s[:180]}\"")
                    break

        # Show up to 2 relevant publications
        if qv is not None and paper_idx is not None:
            pubs = find_top_papers(p.get("id"), qv, paper_idx, n=2, min_sim=0.58)
            for i, (pub_title, pub_year, pub_cited, pub_sim) in enumerate(pubs):
                year_str  = f" ({pub_year})" if pub_year else ""
                cited_str = f", cited {pub_cited}×" if pub_cited else ""
                marker    = "  ★" if pub_sim >= 0.72 else ""
                label_str = "Relevant publication" if i == 0 else "Also published"
                print(f"   {label_str}: \"{pub_title}\"{year_str}{cited_str}{marker}")

        print(f"   {bio_url}")
        print()

    print("-" * 65)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    people = load_faculty()
    print("Loading SPECTER2...")
    model = load_model()
    emb, labels, centroids = get_index(people, model)
    paper_idx = get_paper_index(people, model)

    pub_note = (
        f"{len(paper_idx['by_faculty'])} faculty with publication records"
        if paper_idx else "no publication records yet (run fetch_papers.py)"
    )
    print(f"Ready — {len(people)} full-time faculty indexed  |  {pub_note}")
    print("Tips:  search by topic OR by faculty name")
    print("       add filters when prompted  (college / department)")
    print("       refine results by picking a result number after search")
    print("Type 'quit' to exit.\n")

    # Keep last results so user can refine
    last_results   = []
    last_emb       = emb
    last_people    = people
    last_qv        = None
    last_expansion = None

    while True:
        try:
            q = input("Query (topic or faculty name): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"quit", "exit", "q"}:
            break

        # --- check if this is a name lookup ---
        expansion    = None   # populated below in the else branch
        named_person, named_vec = find_by_name(q, people, emb)
        if named_person:
            print(f"\n  Found: {named_person['name']} ({named_person.get('title','')}, {named_person.get('department','')})")
            print("  Showing faculty with similar research...\n")
            qv    = named_vec
            query = named_person["research_summary"][:300]
        else:
            topic     = clean_query(q) or q
            expansion = expand_query_with_llm(topic)
            academic  = expansion["academic_jargon"]
            if academic != topic:
                kw_preview = ", ".join(expansion["keywords"][:6])
                print(f"  [expanded → {academic}]")
                if kw_preview:
                    print(f"  [keywords → {kw_preview}]")
            qv    = model.encode([academic], normalize_embeddings=True)[0]
            query = academic   # used by explain_match / keyword scoring fallback
        last_qv        = qv
        last_expansion = expansion if not named_person else None

        # --- mode ---
        raw_mode = input("Mode  s=semantic  c=complementary  [s]: ").strip().lower() or "s"
        mode     = "c" if raw_mode.startswith("c") else "s"

        # --- complementary: how different? ---
        n_skip = 2
        if mode == "c":
            raw_diff = input("How different?  1=adjacent  2=moderate  3=very different  [2]: ").strip()
            n_skip   = int(raw_diff) if raw_diff in {"1","2","3"} else 2

        # --- optional filters ---
        college_f = input("Filter by college? (partial name or Enter to skip): ").strip().lower() or None
        dept_f    = input("Filter by department? (partial name or Enter to skip): ").strip().lower() or None

        f_people, f_emb = apply_filters(people, emb, college_f, dept_f)

        # --- run search ---
        if mode == "c":
            f_labels = labels[np.array([people.index(p) for p in f_people])] if f_people is not people else labels
            results  = complementary(query, qv, f_emb, f_labels, f_people, n_skip=n_skip, expansion=expansion)
        else:
            results = semantic(query, qv, f_emb, f_people, expansion=expansion)

        show(results, query, mode, qv=qv, paper_idx=paper_idx)
        last_results   = results
        last_people    = f_people
        last_emb       = f_emb
        last_expansion = expansion

        # --- feedback refinement ---
        refine = input("Refine: pick result number to find more like them (or Enter to skip): ").strip()
        if refine.isdigit() and 1 <= int(refine) <= len(last_results):
            pick      = last_results[int(refine) - 1][0]
            pick_idx  = last_people.index(pick)
            refined_qv = last_emb[pick_idx]
            print(f"\n  Finding more like {pick['name']}...\n")
            ref_results = semantic(pick["research_summary"], refined_qv, last_emb, last_people, expansion=None)
            ref_results = [(p, s, c) for p, s, c in ref_results if p["name"] != pick["name"]][:TOP_K]
            show(ref_results, pick["research_summary"], "s", qv=refined_qv, paper_idx=paper_idx)

        print()

    print("\nBye.")


if __name__ == "__main__":
    main()
