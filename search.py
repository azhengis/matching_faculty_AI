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
import os, sys, sqlite3, pickle, re
import numpy as np

DB          = "faculty.db"
INDEX       = "faculty_index.pkl"
PAPER_INDEX = "paper_index.pkl"
MODEL       = "allenai/specter2"   # adapter version; changed from _base → cache rebuilds
TOP_K      = 5
POOL_SIZE  = 30        # fetch this many candidates before diversity filtering
K_CLUSTERS = 35        # finer clusters than before
MIN_SCORE  = {"s": 0.50, "c": 0.30}   # below this, warn user result may be weak

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
SYNONYMS = {
    # Abbreviations (3-char minimum passes the keyword length filter)
    "nlp":            ["natural language", "computational linguistics", "text mining"],
    "hci":            ["human-computer", "human computer", "user interface", "usability"],
    "ehr":            ["health record", "electronic health", "clinical informatics"],
    "emr":            ["health record", "electronic health", "clinical informatics"],
    "iot":            ["internet of things", "embedded system", "sensor network"],
    "llm":            ["language model", "large language", "foundation model"],
    "xai":            ["explainability", "interpretability", "explainable ai"],
    # Cyber / security
    "cybersecurity":  ["information security", "network security", "cyber security", "computer security"],
    "intrusion":      ["anomaly detection", "threat detection", "network monitoring"],
    "malware":        ["virus", "ransomware", "threat analysis", "malicious code"],
    # Healthcare
    "healthcare":     ["clinical", "health informatics", "biomedical", "patient care"],
    "clinical":       ["healthcare", "patient", "biomedical", "health informatics"],
    # ML / AI concepts
    "fairness":       ["bias", "algorithmic fairness", "discrimination"],
    "bias":           ["fairness", "algorithmic fairness", "discrimination"],
    "explainability": ["interpretability", "explainable", "transparency"],
    "interpretability": ["explainability", "explainable", "transparency"],
    "generative":     ["language model", "diffusion model", "foundation model"],
    "federated":      ["distributed learning", "privacy-preserving", "decentralized"],
    "adversarial":    ["robustness", "red team", "attack defense"],
    "privacy":        ["differential privacy", "anonymization", "data protection"],
    "multimodal":     ["vision language", "cross-modal", "image text"],
    # Environment / society
    "sustainability": ["sustainable", "environmental", "ecology", "climate"],
    "climate":        ["sustainability", "environmental science", "ecology"],
    "equity":         ["social justice", "inclusion", "marginalized", "underserved"],
    "diversity":      ["equity", "inclusion", "social justice", "representation"],
    "accessibility":  ["disability", "universal design", "inclusive design"],
    # Cognitive science / neuroscience / aging
    "memory":         ["cognitive", "dementia", "alzheimer", "neurodegeneration", "cognition"],
    "cognitive":      ["memory", "dementia", "alzheimer", "neurodegeneration", "neuroscience"],
    "dementia":       ["alzheimer", "memory", "cognitive decline", "neurodegeneration"],
    "aging":          ["gerontology", "geriatric", "elderly", "older adults"],
    "elderly":        ["aging", "gerontology", "geriatric", "older adults"],
    "neurological":   ["neuroscience", "neural", "brain", "cognitive", "nervous system"],
    "neuroscience":   ["neurology", "brain", "cognitive", "neural", "neurological"],
    "psychiatric":    ["mental health", "psychology", "behavioral", "neuroscience"],
    # General medical / clinical
    "diagnosis":      ["screening", "detection", "assessment", "evaluation", "diagnostic"],
    "biomarker":      ["screening", "early detection", "diagnostic marker", "biomarkers"],
    "treatment":      ["therapy", "intervention", "clinical trial", "therapeutics"],
    "patient":        ["clinical", "healthcare", "medical", "hospital"],
}

# Maps multi-word query concepts to synonym phrases checked in faculty text.
# If the phrase is detected in the query AND a synonym appears in the text,
# all meaningful words of that phrase are credited as matched (at 70% weight).
PHRASE_SYNONYMS = {
    "transfer learning":      ["domain adaptation", "fine-tuning", "pretrained", "pretraining"],
    "deep learning":          ["neural network", "convolutional network", "transformer model"],
    "machine learning":       ["statistical learning", "predictive modeling", "supervised learning"],
    "natural language":       ["nlp", "text processing", "computational linguistics"],
    "computer vision":        ["image recognition", "visual recognition", "object detection"],
    "reinforcement learning": ["reward signal", "policy optimization", "q-learning"],
    "large language model":   ["llm", "generative ai", "foundation model"],
    "social justice":         ["equity", "inclusion", "marginalized", "racial justice"],
    "climate change":         ["sustainability", "ecology", "global warming", "carbon emission"],
    "human computer":         ["hci", "usability", "user experience", "user interface"],
    "information security":   ["cybersecurity", "network security", "intrusion detection"],
    "public health":          ["epidemiology", "population health", "community health"],
    "data science":           ["machine learning", "statistical analysis", "data mining"],
    "software engineering":   ["software development", "software design", "software architecture"],
    "quantum computing":      ["quantum algorithm", "qubit", "quantum information"],
    "augmented reality":      ["mixed reality", "spatial computing", "extended reality"],
    "virtual reality":        ["immersive experience", "spatial computing", "extended reality"],
    # Cognitive / neurological / aging
    "memory loss":            ["cognitive decline", "dementia", "alzheimer", "cognitive impairment", "neurodegeneration"],
    "cognitive decline":      ["dementia", "alzheimer", "memory loss", "cognitive impairment", "neurodegeneration"],
    "early signs":            ["biomarker", "screening", "early detection", "early diagnosis", "prodromal"],
    "early detection":        ["screening", "biomarker", "early signs", "early diagnosis"],
    "brain disease":          ["neurodegeneration", "neurology", "cognitive decline", "dementia"],
    "mental health":          ["psychiatry", "psychology", "behavioral health", "wellbeing"],
    "patient care":           ["clinical", "healthcare delivery", "medical treatment", "nursing"],
    "older adults":           ["aging", "gerontology", "geriatric", "elderly"],
}

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

    Prefers the proximity-adapter version (allenai/specter2) for better
    retrieval accuracy. Falls back to the base model if the 'adapters'
    library is not installed.

        pip3 install adapters          # to get the improved model
    """
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
        words    = set(re.findall(r"[a-z]+", s.lower()))
        exact    = len(keywords & words)
        syn_hits = sum(
            0.75 for kw in keywords
            if kw not in words and any(syn in s.lower() for syn in SYNONYMS.get(kw, []))
        )
        return exact + syn_hits

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
    keywords = query_keywords(query)
    if not keywords:
        return 0.5
    text_lower  = text.lower()
    query_lower = clean_query(query).lower()

    satisfied = {}  # keyword → score (1.0 exact, 0.75 synonym, 0.70 phrase synonym)

    # Pass 1 — exact substring match
    for kw in keywords:
        if kw in text_lower:
            satisfied[kw] = 1.0

    # Pass 2 — single-word synonym: "nlp" → "natural language", "cybersecurity" → "information security"
    for kw in keywords:
        if kw not in satisfied:
            for syn in SYNONYMS.get(kw, []):
                if syn in text_lower:
                    satisfied[kw] = 0.75
                    break

    # Pass 3 — phrase-level synonym: if "transfer learning" is in the query and
    # "domain adaptation" is in the text, credit both "transfer" and "learning"
    for phrase, synonyms in PHRASE_SYNONYMS.items():
        if phrase in query_lower and any(syn in text_lower for syn in synonyms):
            for word in query_keywords(phrase):
                if word in keywords and word not in satisfied:
                    satisfied[word] = 0.70

    return min(sum(satisfied.values()) / len(keywords), 1.0)


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
    if specter_sim is not None and specter_sim >= 0.87:
        return 1.0  # SPECTER2 is very confident — trust it over keyword gate
    n_keywords = len(query_keywords(query))
    if n_keywords >= 3 and kw == 0.0:
        return 0.50
    if n_keywords >= 5 and kw < 0.15:   # only 1 of 7+ keywords matched
        return 0.60
    if n_keywords >= 4 and kw < 0.26:   # only 1 of 4+ keywords matched
        return 0.75
    return 1.0


def hybrid_scores(query, qv, emb, people):
    sims  = emb @ qv
    scores = []
    for i in range(len(people)):
        src = people[i].get("summary_source", "research")
        a   = alpha_for_query(query, src)
        kw  = kw_presence_score(query, people[i]["research_summary"])
        raw = a * float(sims[i]) + (1 - a) * kw
        scores.append(recency_penalty(people[i]) * raw * zero_kw_penalty(query, kw, float(sims[i])))
    return np.array(scores)


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

def semantic(query, qv, emb, people):
    scores = hybrid_scores(query, qv, emb, people)
    top    = np.argsort(scores)[::-1][:POOL_SIZE]
    candidates = [(people[i], float(scores[i]), None) for i in top]
    return diversity_filter(candidates)


def complementary(query, qv, emb, labels, people, n_skip=2):
    """n_skip controls how different: 1=adjacent, 2=moderate, 3=very different."""
    scores           = hybrid_scores(query, qv, emb, people)
    top_semantic_idx = np.argsort(scores)[::-1][:TOP_K * n_skip]
    excluded         = {int(labels[i]) for i in top_semantic_idx}

    candidates = [
        (people[i], float(scores[i]), int(labels[i]))
        for i in range(len(people))
        if labels[i] not in excluded
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return diversity_filter(candidates)


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
            reason    = first_sentence(p["research_summary"], name=p.get("name",""))
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
    last_results = []
    last_emb     = emb
    last_people  = people
    last_qv      = None

    while True:
        try:
            q = input("Query (topic or faculty name): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"quit", "exit", "q"}:
            break

        # --- check if this is a name lookup ---
        named_person, named_vec = find_by_name(q, people, emb)
        if named_person:
            print(f"\n  Found: {named_person['name']} ({named_person.get('title','')}, {named_person.get('department','')})")
            print("  Showing faculty with similar research...\n")
            qv    = named_vec
            query = named_person["research_summary"][:300]
        else:
            topic = clean_query(q) or q
            qv    = model.encode([topic], normalize_embeddings=True)[0]
            query = q
        last_qv = qv

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
            results  = complementary(query, qv, f_emb, f_labels, f_people, n_skip=n_skip)
        else:
            results = semantic(query, qv, f_emb, f_people)

        show(results, query, mode, qv=qv, paper_idx=paper_idx)
        last_results = results
        last_people  = f_people
        last_emb     = f_emb

        # --- feedback refinement ---
        refine = input("Refine: pick result number to find more like them (or Enter to skip): ").strip()
        if refine.isdigit() and 1 <= int(refine) <= len(last_results):
            pick      = last_results[int(refine) - 1][0]
            pick_idx  = last_people.index(pick)
            refined_qv = last_emb[pick_idx]
            print(f"\n  Finding more like {pick['name']}...\n")
            ref_results = semantic(pick["research_summary"], refined_qv, last_emb, last_people)
            ref_results = [(p, s, c) for p, s, c in ref_results if p["name"] != pick["name"]][:TOP_K]
            show(ref_results, pick["research_summary"], "s", qv=refined_qv, paper_idx=paper_idx)

        print()

    print("\nBye.")


if __name__ == "__main__":
    main()
