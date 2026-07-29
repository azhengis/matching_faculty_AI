"""Unit tests for the research-gap map computation.

The SPECTER2 model is faked so these stay fast and offline — the logic under
test is the PCA projection and normalisation, not the embeddings.
"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_app


class _FakeModel:
    """Returns fixed, well-separated vectors so PCA has a stable 2D structure."""
    def __init__(self, vectors):
        self._vectors = vectors

    def encode(self, texts, normalize_embeddings=True):
        return np.array(self._vectors[:len(texts)], dtype=float)


def _works(n):
    return [{"title": f"Paper {i}", "abstract": "x", "year": 2020 + i,
             "cited_by_count": 10 * i, "authors": ["A"]} for i in range(n)]


def test_returns_none_with_too_few_works(monkeypatch):
    monkeypatch.setitem(web_app._st, "model", _FakeModel([[1, 0, 0]] * 3))
    assert web_app._compute_gap_map("q", _works(2)) is None


def test_returns_none_when_model_missing(monkeypatch):
    monkeypatch.setitem(web_app._st, "model", None)
    assert web_app._compute_gap_map("q", _works(5)) is None


def test_similarity_computed_and_sorted_closest_first(monkeypatch):
    # project = [1,0,0]; works at decreasing cosine to it.
    project = [1.0, 0.0, 0.0]
    w_close = [1.0, 0.0, 0.0]        # cosine 1.0
    w_mid   = [0.6, 0.8, 0.0]        # cosine 0.6
    w_far   = [0.0, 1.0, 0.0]        # cosine 0.0
    # _works(3) are Paper 0,1,2 → pair them with far, close, mid so ordering must sort.
    monkeypatch.setitem(web_app._st, "model", _FakeModel([project, w_far, w_close, w_mid]))

    gm = web_app._compute_gap_map("my project", _works(3))
    assert set(gm) == {"query", "works"}
    assert gm["query"] == "my project"

    sims = [w["similarity"] for w in gm["works"]]
    assert sims == sorted(sims, reverse=True)          # closest first
    assert sims[0] == 1.0 and sims[-1] == 0.0
    assert all(0.0 <= s <= 1.0 for s in sims)
    # the closest work is the one whose vector matched the project (Paper 1)
    assert gm["works"][0]["title"] == "Paper 1"


def test_negative_cosine_is_clamped(monkeypatch):
    project = [1.0, 0.0, 0.0]
    works_vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]  # last is opposite
    monkeypatch.setitem(web_app._st, "model", _FakeModel([project] + works_vecs))
    gm = web_app._compute_gap_map("q", _works(3))
    assert all(0.0 <= w["similarity"] <= 1.0 for w in gm["works"])


def test_untitled_works_are_dropped(monkeypatch):
    monkeypatch.setitem(web_app._st, "model", _FakeModel([[i, 0, 0] for i in range(6)]))
    works = _works(4) + [{"title": "  ", "abstract": "y", "cited_by_count": 5}]
    gm = web_app._compute_gap_map("q", works)
    assert len(gm["works"]) == 4   # the blank-title work is excluded
