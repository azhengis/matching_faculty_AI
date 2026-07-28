"""Unit tests for the advisor's live literature search (OpenAlex).

Network is mocked so these stay fast and offline-safe.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import web_app


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_reconstruct_abstract_orders_words_by_position():
    inv = {"Procrastination": [0], "is": [1], "common": [2]}
    assert web_app._reconstruct_abstract(inv) == "Procrastination is common"
    assert web_app._reconstruct_abstract({}) == ""


def test_search_literature_shapes_openalex_results(monkeypatch):
    payload = {"results": [{
        "title": "Academic procrastination in college students",
        "publication_year": 2010,
        "cited_by_count": 374,
        "authorships": [
            {"author": {"display_name": "Laura A. Rabin"}},
            {"author": {"display_name": "Joshua Fogel"}},
            {"author": {"display_name": "Katherine Nutter-Upham"}},
            {"author": {"display_name": "Fourth Author"}},
        ],
        "abstract_inverted_index": {"Procrastination": [0], "is": [1], "widespread": [2]},
    }]}

    monkeypatch.setattr(web_app, "requests", None, raising=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(200, payload))

    out = web_app._search_literature("student procrastination")
    assert out["result_count"] == 1
    w = out["results"][0]
    assert w["title"] == "Academic procrastination in college students"
    assert w["year"] == 2010 and w["cited_by_count"] == 374
    assert w["authors"] == ["Laura A. Rabin", "Joshua Fogel", "Katherine Nutter-Upham"]  # capped at 3
    assert w["abstract"] == "Procrastination is widespread"


def test_search_literature_empty_query_is_rejected_without_network():
    out = web_app._search_literature("   ")
    assert out["error"] == "empty query" and out["results"] == []


def test_search_literature_reports_http_errors_instead_of_raising(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(500, {}))
    out = web_app._search_literature("anything")
    assert "unavailable" in out["error"] and out["results"] == []


def test_search_literature_reports_network_failure(monkeypatch):
    import requests
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("no network")
    monkeypatch.setattr(requests, "get", boom)
    out = web_app._search_literature("anything")
    assert "failed" in out["error"] and out["results"] == []


def test_search_literature_is_a_registered_advisor_tool():
    names = [t["function"]["name"] for t in web_app._ADVISOR_TOOLS]
    assert "search_literature" in names
