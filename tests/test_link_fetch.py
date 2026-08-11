"""Reading pages the researcher links to, without becoming an SSRF proxy.

Fetching a user-supplied URL server-side is a request-forgery primitive: paste
http://169.254.169.254/ and the app would read cloud credentials on your
behalf. These tests pin the guards. They do no real network I/O except through
a stubbed requests module.
"""
import types

import web_app


def test_rejects_non_http_schemes():
    assert web_app._fetch_link_text("file:///etc/passwd") == ""
    assert web_app._fetch_link_text("ftp://example.com/x") == ""
    assert web_app._fetch_link_text("javascript:alert(1)") == ""


def test_rejects_loopback_and_private_and_metadata_addresses(monkeypatch):
    """The addresses that make SSRF worth doing."""
    for host in ("127.0.0.1", "localhost", "10.0.0.5", "192.168.1.1",
                 "169.254.169.254", "[::1]"):
        assert web_app._fetch_link_text(f"http://{host}/") == "", host


def test_public_hostname_resolving_to_private_ip_is_rejected(monkeypatch):
    """The bypass a hostname-string blocklist would miss: a public name whose
    DNS points inside. The check runs on the resolved address."""
    monkeypatch.setattr(web_app, "_is_public_address", lambda host: False)
    assert web_app._fetch_link_text("https://totally-normal.example.com/") == ""


def _stub_requests(monkeypatch, *, status=200, ctype="text/html", body=b"",
                   final_url="https://example.com/page"):
    class _Resp:
        def __init__(self):
            self.status_code = status
            self.headers = {"content-type": ctype}
            self.url = final_url
            self.raw = types.SimpleNamespace(read=lambda n, decode_content=True: body)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    stub = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    monkeypatch.setitem(__import__("sys").modules, "requests", stub)
    monkeypatch.setattr(web_app, "_is_public_address", lambda host: True)


def test_extracts_readable_text_and_drops_markup(monkeypatch):
    html = (b"<html><head><style>.x{color:red}</style><script>evil()</script></head>"
            b"<body><nav>Menu</nav><h1>Jane Doe</h1>"
            b"<p>I study eviction records in Cook County.</p>"
            b"<footer>Copyright</footer></body></html>")
    _stub_requests(monkeypatch, body=html)
    out = web_app._fetch_link_text("https://example.com/page")
    assert "Jane Doe" in out and "eviction records" in out
    assert "evil()" not in out and "color:red" not in out
    assert "Menu" not in out and "Copyright" not in out


def test_non_html_content_is_skipped(monkeypatch):
    _stub_requests(monkeypatch, ctype="application/pdf", body=b"%PDF-1.7 ...")
    assert web_app._fetch_link_text("https://example.com/cv.pdf") == ""


def test_non_200_is_not_treated_as_content(monkeypatch):
    _stub_requests(monkeypatch, status=404, body=b"<html><body>Not found</body></html>")
    assert web_app._fetch_link_text("https://example.com/missing") == ""


def test_redirect_landing_on_a_private_address_is_rejected(monkeypatch):
    """First hop public, final hop internal: the check must run after
    redirects, not only before them."""
    _stub_requests(monkeypatch, body=b"<html><body>secret</body></html>",
                   final_url="http://169.254.169.254/latest/meta-data/")
    calls = {"n": 0}
    def fake_public(host):
        calls["n"] += 1
        return calls["n"] == 1        # public before, private after
    monkeypatch.setattr(web_app, "_is_public_address", fake_public)
    assert web_app._fetch_link_text("https://example.com/redirector") == ""


def test_output_is_capped(monkeypatch):
    _stub_requests(monkeypatch, body=b"<html><body>" + b"word " * 50000 + b"</body></html>")
    out = web_app._fetch_link_text("https://example.com/huge")
    assert 0 < len(out) <= web_app.MAX_DOCUMENT_CHARS


def test_a_failure_returns_empty_rather_than_raising(monkeypatch):
    """A dead link must still save as a bookmark, not break the request."""
    def boom(*a, **k): raise RuntimeError("network down")
    monkeypatch.setitem(__import__("sys").modules, "requests",
                        types.SimpleNamespace(get=boom))
    monkeypatch.setattr(web_app, "_is_public_address", lambda host: True)
    assert web_app._fetch_link_text("https://example.com/") == ""
