"""Tests for the publication signal in hybrid_scores.

A faculty member's papers should raise their match for a topic they publish on,
grounded per-paper so a prolific author isn't credited for a query just because
its words appear scattered across unrelated titles.
"""
import sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import search as sm


def test_best_paper_coverage_is_per_paper_not_a_title_bag():
    kw = {"graph", "neural", "networks"}
    # One paper containing all three → full coverage.
    assert sm._best_paper_coverage(["deep graph neural networks for x"], kw) == 1.0
    # Three separate papers each with one word → best single paper is 1/3, NOT 1.0.
    assert sm._best_paper_coverage(
        ["social networks and health", "graph theory basics", "neural correlates of y"], kw
    ) == 1 / 3
    assert sm._best_paper_coverage([], kw) == 0.0
    assert sm._best_paper_coverage(["anything"], set()) == 0.0


def _fixture():
    # Two faculty with the SAME (weak) bio similarity, but only the second has a
    # paper actually about the query.
    people = [
        {"id": 1, "name": "No Papers", "title": "", "summary_source": "research",
         "research_summary": "studies medieval history"},
        {"id": 2, "name": "Has Paper", "title": "", "summary_source": "research",
         "research_summary": "studies medieval history"},
    ]
    emb = np.array([[1.0, 0.0], [1.0, 0.0]])   # identical bio vectors
    qv  = np.array([0.2, 0.0])                  # weak, equal bio similarity for both
    paper_idx = {
        "by_faculty": {2: [0]},
        "meta": [(2, "graph neural networks for medical imaging", 2021, 5)],
    }
    return people, emb, qv, paper_idx


def test_paper_match_raises_score_over_identical_bio():
    people, emb, qv, paper_idx = _fixture()
    q = "graph neural networks"
    without = sm.hybrid_scores(q, qv, emb, people)
    withp   = sm.hybrid_scores(q, qv, emb, people, paper_idx=paper_idx)

    # Faculty 1 (no on-topic paper) is unchanged; faculty 2 is boosted above them.
    assert withp[0] == without[0]
    assert withp[1] > without[1]
    assert withp[1] > withp[0]


def test_paper_idx_none_is_backward_compatible():
    people, emb, qv, paper_idx = _fixture()
    q = "graph neural networks"
    a = sm.hybrid_scores(q, qv, emb, people)
    b = sm.hybrid_scores(q, qv, emb, people, paper_idx=None)
    assert np.allclose(a, b)


def test_offtopic_papers_do_not_boost():
    """A paper that shares no query terms must not raise the score."""
    people, emb, qv, paper_idx = _fixture()
    paper_idx["meta"] = [(2, "a history of medieval trade routes", 2019, 3)]
    q = "graph neural networks"
    without = sm.hybrid_scores(q, qv, emb, people)
    withp   = sm.hybrid_scores(q, qv, emb, people, paper_idx=paper_idx)
    assert np.allclose(without, withp)
