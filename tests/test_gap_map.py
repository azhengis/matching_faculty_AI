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


def test_coords_are_normalised_and_shaped(monkeypatch):
    # 1 query + 4 works = 5 vectors spread across the space.
    vecs = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0.5, 0.5, 1]]
    monkeypatch.setitem(web_app._st, "model", _FakeModel(vecs))

    gm = web_app._compute_gap_map("my project", _works(4))
    assert gm is not None
    assert set(gm) == {"query", "project", "works"}
    assert gm["query"] == "my project"
    assert len(gm["works"]) == 4

    pts = [(gm["project"]["x"], gm["project"]["y"])] + [(w["x"], w["y"]) for w in gm["works"]]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    # each axis min-max normalised into [0, 1]
    assert min(xs) == 0.0 and max(xs) == 1.0
    assert min(ys) == 0.0 and max(ys) == 1.0
    assert all(0.0 <= v <= 1.0 for v in xs + ys)
    # metadata carried through
    assert gm["works"][2]["cited_by_count"] == 20
    assert gm["works"][1]["year"] == 2021


def test_untitled_works_are_dropped(monkeypatch):
    monkeypatch.setitem(web_app._st, "model", _FakeModel([[i, 0, 0] for i in range(6)]))
    works = _works(4) + [{"title": "  ", "abstract": "y", "cited_by_count": 5}]
    gm = web_app._compute_gap_map("q", works)
    assert len(gm["works"]) == 4   # the blank-title work is excluded
