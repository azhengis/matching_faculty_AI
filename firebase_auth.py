"""Verify Firebase ID tokens without the Admin SDK.

Firebase handles the parts that are genuinely hard to get right — storing
passwords, sending reset and verification emails, Google sign-in. The backend's
only job is to confirm that the ID token the browser presents is a real,
unexpired token Firebase issued for THIS project, and to read the verified
email out of it. Everything after that is the app's existing session system.

Deliberately no `firebase-admin` and no local JWT signature checking. The Admin
SDK pulls in google-auth and grpc; verifying RS256 ourselves pulls in
`cryptography`. Both cost memory on a container that is already tight, and both
exist to avoid one network round-trip per LOGIN — an event that happens rarely.
So instead we ask Google to validate the token, through the Identity Toolkit
`accounts:lookup` endpoint, which rejects an invalid or expired token and
returns the account record for a good one. The only credential it needs is the
Web API key, which is public (it ships in every Firebase web app's config).

Nothing here touches the database or FastAPI, so it unit-tests with the one
network call mocked.
"""
from __future__ import annotations

import os

import requests

# Public config. The project id and web api key are not secrets — they are
# embedded in the frontend. Their presence is also the feature flag: with no
# project id set, Firebase sign-in is simply off and password login is
# unaffected.
PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
API_KEY    = os.environ.get("FIREBASE_API_KEY", "").strip()

# If set, only addresses in this domain may sign in — the impersonation guard.
# "depaul.edu" rejects anyone who cannot receive mail there. Empty = any
# verified address, which is the right default until the project is locked down.
ALLOWED_DOMAIN = os.environ.get("AUTH_EMAIL_DOMAIN", "").strip().lower()

# Whether an email/password account must have confirmed its address first.
# On by default: an unverified email is exactly the impersonation hole. Google
# sign-ins are inherently verified, so this only ever gates the password path.
REQUIRE_VERIFIED = os.environ.get("FIREBASE_REQUIRE_VERIFIED", "1").strip().lower() \
    not in ("0", "false", "no")

_LOOKUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:lookup"
_TIMEOUT = 10


class FirebaseAuthError(Exception):
    """A token that could not be accepted. The message is safe to show."""


def is_configured() -> bool:
    """True when the project is set up enough to offer Firebase sign-in."""
    return bool(PROJECT_ID and API_KEY)


def public_config() -> dict:
    """What the login page needs to initialise the Firebase SDK. All public."""
    if not is_configured():
        return {"enabled": False}
    return {
        "enabled": True,
        "apiKey": API_KEY,
        "authDomain": f"{PROJECT_ID}.firebaseapp.com",
        "projectId": PROJECT_ID,
        "allowedDomain": ALLOWED_DOMAIN,
    }


def verify_id_token(id_token: str) -> dict:
    """Validate a Firebase ID token with Google and return the verified account.

    Returns {"email": ..., "uid": ..., "email_verified": bool}. Raises
    FirebaseAuthError for anything that should not be let in — a bad token, an
    unverified address where verification is required, or a domain that is not
    allowed. The caller turns the raise into a 401/403.
    """
    if not is_configured():
        raise FirebaseAuthError("Firebase sign-in is not configured on this server.")
    id_token = (id_token or "").strip()
    if not id_token:
        raise FirebaseAuthError("No sign-in token was provided.")

    try:
        resp = requests.post(
            _LOOKUP_URL, params={"key": API_KEY},
            json={"idToken": id_token}, timeout=_TIMEOUT)
    except requests.RequestException as e:
        # A network failure here must not read as "bad token" — it is the
        # server's problem, not the user's credential.
        raise FirebaseAuthError("Could not reach the sign-in service. Try again.") from e

    if resp.status_code != 200:
        # Google returns 400 with an INVALID_ID_TOKEN / TOKEN_EXPIRED message.
        raise FirebaseAuthError("That sign-in could not be verified. Please sign in again.")

    users = (resp.json() or {}).get("users") or []
    if not users:
        raise FirebaseAuthError("That sign-in could not be verified. Please sign in again.")
    record = users[0]

    email = (record.get("email") or "").strip().lower()
    if not email:
        raise FirebaseAuthError("That account has no email address.")

    verified = bool(record.get("emailVerified"))
    if REQUIRE_VERIFIED and not verified:
        raise FirebaseAuthError(
            "Please verify your email first. Check your inbox for the confirmation link.")

    if ALLOWED_DOMAIN and not email.endswith("@" + ALLOWED_DOMAIN):
        raise FirebaseAuthError(f"Only {ALLOWED_DOMAIN} addresses can sign in here.")

    return {"email": email, "uid": record.get("localId") or "", "email_verified": verified}
