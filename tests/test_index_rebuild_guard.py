"""The server must never rebuild an embedding index.

Rebuilding needs the full SPECTER2 via torch (~1GB) and OOM-kills the
container mid-startup — which is exactly how a Railway deploy died: the index
on the volume had drifted from the faculty text (a few profile edits since it
was baked), get_index decided to rebuild, and the process was killed.

Rebuilding is a BUILD-TIME job, gated behind INDEX_REBUILD_OK, which only the
Dockerfile sets. At runtime the server uses the index it has, stale or not,
because a slightly stale index is a far better outcome than an app that will
not start.
"""
import importlib
import os
import pickle
import tempfile

import numpy as np
import pytest

import search


def _reload(rebuild_ok):
    if rebuild_ok:
        os.environ["INDEX_REBUILD_OK"] = "1"
    else:
        os.environ.pop("INDEX_REBUILD_OK", None)
    importlib.reload(search)
    return search


class _ExplodingModel:
    def encode(self, *a, **k):
        raise AssertionError("rebuild attempted at runtime")


def _write_index(path, *, fingerprint, count=5, model=None):
    pickle.dump({"count": count, "model": model or search.MODEL,
                 "fingerprint": fingerprint, "emb": np.zeros((count, 768), "float32"),
                 "labels": np.zeros(count, int), "centroids": np.zeros((1, 768))},
                open(path, "wb"))


def _people(n=5):
    return [{"id": i, "name": f"P{i}", "research_summary": f"text {i}"} for i in range(n)]


def test_runtime_uses_a_drifted_index_instead_of_rebuilding():
    """The exact deploy failure: fingerprint drift must not trigger a rebuild."""
    sm = _reload(rebuild_ok=False)
    d = tempfile.mkdtemp(); sm.INDEX = os.path.join(d, "f.pkl")
    _write_index(sm.INDEX, fingerprint="STALE")       # does not match live text
    emb, labels, cents = sm.get_index(_people(), _ExplodingModel())
    assert emb.shape == (5, 768)                       # used it; never called encode


def test_runtime_raises_clearly_when_there_is_no_index_at_all():
    """Better a legible error than a silent OOM. In production the index is
    always seeded, so this is the last-resort guard."""
    sm = _reload(rebuild_ok=False)
    d = tempfile.mkdtemp(); sm.INDEX = os.path.join(d, "missing.pkl")
    with pytest.raises(RuntimeError, match="rebuilding is disabled at runtime"):
        sm.get_index(_people(), None)


def test_the_build_is_allowed_to_rebuild():
    """With the flag on (the Dockerfile sets it), a missing index triggers a
    real build rather than the runtime refusal."""
    sm = _reload(rebuild_ok=True)
    assert sm._REBUILD_OK is True
    d = tempfile.mkdtemp(); sm.INDEX = os.path.join(d, "none.pkl")
    reached = {"build": False}
    class _M:
        def encode(self, *a, **k):
            reached["build"] = True
            return np.zeros((1, 768), "float32")
    try:
        sm.get_index(_people(1), _M())
    except Exception:
        pass   # clustering may fail with 1 point; we only assert the path was reached
    assert reached["build"] is True


def test_the_paper_index_degrades_to_none_rather_than_rebuilding():
    """Paper evidence is optional; without a usable index the search just runs
    without it, never a rebuild."""
    sm = _reload(rebuild_ok=False)
    assert sm._REBUILD_OK is False
    # Covered structurally: the runtime branch returns the cache or None and
    # never calls encode. (Full exercise needs a papers DB; the source guard
    # is asserted below.)
    import inspect
    src = inspect.getsource(sm.get_paper_index)
    assert "if not _REBUILD_OK" in src
    assert "without per-paper evidence" in src


def test_only_the_builder_stage_enables_rebuild():
    from pathlib import Path
    df = (Path(search.__file__).parent / "Dockerfile").read_text()
    # Set inline on the build step, never as a persistent ENV that could leak
    # into the runtime stage.
    assert "INDEX_REBUILD_OK=1 python -c" in df
    assert "ENV INDEX_REBUILD_OK" not in df


def teardown_module(module):
    os.environ.pop("INDEX_REBUILD_OK", None)
    importlib.reload(search)
