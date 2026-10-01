# Firebase sign-in — setup

The app can authenticate through Firebase: email/password with a real password
reset and email verification, plus "Continue with Google". Firebase owns the
credential; the app still issues its own session cookie and keeps every profile
in SQLite. Nothing about the data layer changes.

**It is off until configured.** With no `FIREBASE_PROJECT_ID` set, the login
page shows the original password form and behaves exactly as before. So this
code is already deployed and inert; turning it on is console work plus three
environment variables.

## What only you can do (the Firebase console)

1. **Create a project** at <https://console.firebase.google.com> — a free
   "Spark" project is enough.

2. **Add a Web app** (the `</>` icon). Firebase shows a config object. Two
   values from it matter: `projectId` and `apiKey`. Both are public — they are
   meant to live in the browser — so they are safe to put in env vars.

3. **Enable sign-in methods** under Authentication → Sign-in method:
   - **Email/Password** — on. (This is what password reset and verification
     hang off.)
   - **Google** — on, if DePaul faculty email runs on Google Workspace. This is
     the strongest option: Google has already proven the person owns the
     address.

4. **Authorize the domain** under Authentication → Settings → Authorized
   domains: add the Railway domain the site is served from (e.g.
   `something.up.railway.app`), and any custom domain later. Google blocks
   sign-in from domains not on this list.

## The three environment variables (Railway → Variables)

| Key | Value | Notes |
|---|---|---|
| `FIREBASE_PROJECT_ID` | from the config object | turning this on is the flag |
| `FIREBASE_API_KEY` | the `apiKey` from the config | public; not a secret |
| `AUTH_EMAIL_DOMAIN` | `depaul.edu` | optional; rejects non-DePaul addresses |

Optional: `FIREBASE_REQUIRE_VERIFIED=0` lifts the email-verification requirement
for a walkthrough. Leave it on in normal use — an unverified address is the
impersonation hole this exists to close.

Railway redeploys when you add variables. The login page then shows the Google
button and a "Forgot your password?" link, and the email/password form routes
through Firebase.

## What happens to existing accounts

Nothing breaks. Accounts are matched by email, so someone who already had a
password account and then signs in through Firebase with the same address is
linked to that one account — not duplicated — and their old password still
works. A brand-new Firebase sign-in creates a local account with no usable
password; their credential lives in Firebase.

## How it verifies a token (for whoever maintains this)

The backend does **not** run the Firebase Admin SDK and does **not** verify the
JWT signature locally — both cost memory on a small container to avoid one
network call per login. Instead `firebase_auth.verify_id_token` asks Google's
Identity Toolkit `accounts:lookup` endpoint to validate the token, using the
public web API key. Google rejects an invalid or expired token; a good one
comes back with the verified email, which the app then maps to a session.

Tests mock that one call: `tests/test_firebase_auth.py`.

## What this does and does not fix

Fixes: impersonation (an unverified or wrong-domain address cannot sign in),
password reset, email verification, and gives a passwordless Google option.

Does not add: rate limiting on the legacy password endpoints (still there as a
fallback), or account deletion from the app (do it in the Firebase console).
