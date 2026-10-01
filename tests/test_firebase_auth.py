"""Firebase sign-in: verify the token, then issue one of our own sessions.

Firebase owns the credential; the backend only confirms the ID token with
Google and maps it to a local account. These tests mock the one network call
(the Identity Toolkit accounts:lookup) so the verification logic, the
find-or-create, and the allow/deny rules are all exercised without a real
project.

The load-bearing property, the reason this was adopted at all: an unverified
email cannot get in. That is the impersonation hole — signing up as
someone@depaul.edu you do not control — and email verification is what closes
it.
"""
import asyncio
import json
import sqlite3
import types

import pytest

import web_app
import firebase_auth


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _FakeRequest:
    def __init__(self, body=None, cookies=None):
        self._body = body or {}
        self.cookies = cookies or {}

    async def json(self):
        return self._body


@pytest.fixture
def configured(monkeypatch):
    """A project that is set up, with verification required and no domain lock."""
    monkeypatch.setattr(firebase_auth, "PROJECT_ID", "demo-project")
    monkeypatch.setattr(firebase_auth, "API_KEY", "public-web-key")
    monkeypatch.setattr(firebase_auth, "ALLOWED_DOMAIN", "")
    monkeypatch.setattr(firebase_auth, "REQUIRE_VERIFIED", True)


def _google_returns(monkeypatch, *, status=200, users=None):
    """Stub the accounts:lookup call."""
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["key"] = (params or {}).get("key")
        captured["token"] = (json or {}).get("idToken")
        return types.SimpleNamespace(
            status_code=status,
            json=lambda: {"users": users} if users is not None else {})
    monkeypatch.setattr(firebase_auth.requests, "post", fake_post)
    return captured


def _account(email="jane@depaul.edu", verified=True, uid="fb-uid-1"):
    return {"email": email, "emailVerified": verified, "localId": uid}


# ── The flag ───────────────────────────────────────────────────────────────

def test_not_configured_means_firebase_is_simply_off(monkeypatch):
    monkeypatch.setattr(firebase_auth, "PROJECT_ID", "")
    monkeypatch.setattr(firebase_auth, "API_KEY", "")
    assert firebase_auth.is_configured() is False
    assert firebase_auth.public_config() == {"enabled": False}


def test_public_config_exposes_only_non_secret_fields(configured):
    cfg = firebase_auth.public_config()
    assert cfg["enabled"] is True
    assert cfg["projectId"] == "demo-project"
    assert cfg["authDomain"] == "demo-project.firebaseapp.com"
    # The web api key is public by design (it ships in the frontend), but there
    # must be no service-account or private material anywhere in here.
    assert set(cfg) == {"enabled", "apiKey", "authDomain", "projectId", "allowedDomain"}


# ── Verifying the token ─────────────────────────────────────────────────────

def test_a_valid_verified_token_returns_the_account(configured, monkeypatch):
    captured = _google_returns(monkeypatch, users=[_account()])
    identity = firebase_auth.verify_id_token("good-token")
    assert identity == {"email": "jane@depaul.edu", "uid": "fb-uid-1", "email_verified": True}
    assert captured["token"] == "good-token"
    assert captured["key"] == "public-web-key"       # the key authenticates the call


def test_an_unverified_email_is_refused(configured, monkeypatch):
    """The whole point. An account that has not confirmed its address is
    exactly the impersonation case."""
    _google_returns(monkeypatch, users=[_account(verified=False)])
    with pytest.raises(firebase_auth.FirebaseAuthError, match="verify your email"):
        firebase_auth.verify_id_token("unverified-token")


def test_verification_can_be_waived_for_testing(configured, monkeypatch):
    monkeypatch.setattr(firebase_auth, "REQUIRE_VERIFIED", False)
    _google_returns(monkeypatch, users=[_account(verified=False)])
    assert firebase_auth.verify_id_token("t")["email"] == "jane@depaul.edu"


def test_a_domain_lock_rejects_outsiders(configured, monkeypatch):
    monkeypatch.setattr(firebase_auth, "ALLOWED_DOMAIN", "depaul.edu")
    _google_returns(monkeypatch, users=[_account(email="stranger@gmail.com")])
    with pytest.raises(firebase_auth.FirebaseAuthError, match="depaul.edu addresses"):
        firebase_auth.verify_id_token("outsider-token")


def test_a_domain_lock_still_admits_the_right_domain(configured, monkeypatch):
    monkeypatch.setattr(firebase_auth, "ALLOWED_DOMAIN", "depaul.edu")
    _google_returns(monkeypatch, users=[_account(email="prof@depaul.edu")])
    assert firebase_auth.verify_id_token("t")["email"] == "prof@depaul.edu"


def test_google_rejecting_the_token_is_a_clean_error(configured, monkeypatch):
    """An expired or forged token comes back as a 400 from Google."""
    _google_returns(monkeypatch, status=400, users=None)
    with pytest.raises(firebase_auth.FirebaseAuthError, match="could not be verified"):
        firebase_auth.verify_id_token("expired-token")


def test_a_network_failure_is_not_reported_as_a_bad_token(configured, monkeypatch):
    """A server problem must not read as "your credential is wrong"."""
    def boom(*a, **k):
        raise firebase_auth.requests.RequestException("connection reset")
    monkeypatch.setattr(firebase_auth.requests, "post", boom)
    with pytest.raises(firebase_auth.FirebaseAuthError, match="Could not reach"):
        firebase_auth.verify_id_token("t")


def test_an_empty_token_is_refused_without_calling_google(configured, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(firebase_auth.requests, "post",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    with pytest.raises(firebase_auth.FirebaseAuthError):
        firebase_auth.verify_id_token("")
    assert called["n"] == 0


# ── The endpoint: token in, session out ─────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(path))
    web_app._auth_sessions.clear()
    web_app._init_profiles_db()
    return path


def _firebase_login(body):
    return _run(web_app.api_auth_firebase(_FakeRequest(body)))


def test_a_first_sign_in_creates_a_local_account(configured, db, monkeypatch):
    _google_returns(monkeypatch, users=[_account(email="new@depaul.edu", uid="u1")])
    resp = _firebase_login({"id_token": "t"})
    assert resp.status_code == 200
    assert "session_token" in resp.headers.get("set-cookie", "")

    con = sqlite3.connect(db)
    row = con.execute("SELECT email, firebase_uid FROM users WHERE email = 'new@depaul.edu'").fetchone()
    con.close()
    assert row == ("new@depaul.edu", "u1")


def test_signing_in_again_does_not_duplicate_the_account(configured, db, monkeypatch):
    _google_returns(monkeypatch, users=[_account(email="repeat@depaul.edu", uid="u2")])
    _firebase_login({"id_token": "t"})
    _firebase_login({"id_token": "t"})
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM users WHERE email = 'repeat@depaul.edu'").fetchone()[0]
    con.close()
    assert n == 1


def test_a_firebase_sign_in_links_to_an_existing_password_account(configured, db, monkeypatch):
    """Somebody who already had a password account, signing in through Firebase
    with the same address, is the same person — linked, not duplicated, and the
    password still works."""
    _run(web_app.api_auth_signup(_FakeRequest(
        {"email": "both@depaul.edu", "password": "originalpw123"})))

    _google_returns(monkeypatch, users=[_account(email="both@depaul.edu", uid="fb-xyz")])
    _firebase_login({"id_token": "t"})

    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, firebase_uid, password_hash FROM users WHERE email='both@depaul.edu'").fetchall()
    con.close()
    assert len(rows) == 1                 # one account, not two
    assert rows[0][1] == "fb-xyz"          # now linked
    assert rows[0][2]                      # original password hash untouched


def test_a_refused_token_is_a_403_not_a_500(configured, db, monkeypatch):
    _google_returns(monkeypatch, users=[_account(verified=False)])
    resp = _firebase_login({"id_token": "unverified"})
    assert resp.status_code == 403
    assert "verify your email" in json.loads(resp.body)["error"]
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0   # nothing created
    con.close()


def test_the_session_it_issues_is_the_ordinary_one(configured, db, monkeypatch):
    """The point of the whole design: downstream code never learns this was
    Firebase. _current_user resolves it like any other session."""
    _google_returns(monkeypatch, users=[_account(email="jane@depaul.edu", uid="u")])
    resp = _firebase_login({"id_token": "t"})
    token = resp.headers["set-cookie"].split("session_token=")[1].split(";")[0]
    user = web_app._current_user(_FakeRequest(cookies={"session_token": token}))
    assert user["email"] == "jane@depaul.edu"


def test_the_endpoint_tolerates_the_camelcase_field_name(configured, db, monkeypatch):
    """The browser sends id_token; a stray idToken must not 500."""
    _google_returns(monkeypatch, users=[_account()])
    assert _firebase_login({"idToken": "t"}).status_code == 200


# ── Off by default is the safety property ──────────────────────────────────

def test_the_login_page_still_renders_with_firebase_unconfigured(monkeypatch):
    """The whole design promise: pushing this changes nothing until configured.
    The page must render, and the legacy password path must still be wired."""
    monkeypatch.setattr(firebase_auth, "PROJECT_ID", "")
    monkeypatch.setattr(firebase_auth, "API_KEY", "")
    from fastapi.testclient import TestClient
    html = TestClient(web_app.app).get("/login").text
    assert "/api/auth/' + _mode" in html      # legacy password path still wired
    assert "firebase-extra" in html           # the block exists but stays hidden


def test_the_config_endpoint_says_disabled_when_unset(monkeypatch):
    monkeypatch.setattr(firebase_auth, "PROJECT_ID", "")
    monkeypatch.setattr(firebase_auth, "API_KEY", "")
    resp = _run(web_app.api_firebase_config())
    assert json.loads(resp.body) == {"enabled": False}
