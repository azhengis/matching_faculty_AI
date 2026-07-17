# Authentication: Password-Based Accounts, Sessions, and Page Cleanup

## Problem

Today there is no login. A "profile" is identified purely by a `profile_id`
stored in the browser's `localStorage` and passed as a plain field in every
API request body (`web_app.py`'s `api_profile_save`, `api_advisor_chat`, and
the `faculty_overrides`-related endpoints all trust whatever `profile_id` or
`email` the client sends). This has three concrete costs, surfaced across the
two most recent features:

- A profile is lost the moment someone clears their browser or switches
  devices — there is no durable identity behind it.
- Anyone can currently overwrite any faculty member's self-edited bio/research
  interests by searching their name and clicking "This is me" — the
  profile-expansion feature explicitly flagged this as an accepted, temporary
  testing-phase risk ("worth an explicit gate before any non-trivial
  deployment").
- `POST /api/profile/save` always `INSERT`s a new `profiles` row — every
  "Update my project" click from the advisor page silently creates another
  orphaned row, with only the client's `localStorage` pointer moving to the
  new one.

Separately, the app has grown three pages (`/baseline`, `/search`, `/chat`)
that predate the advisor and duplicate its underlying matching capability
without personalization. They are no longer part of the intended product
surface.

This spec adds real password-based accounts, a session mechanism, and closes
the profile-ownership and claim-restriction gaps that real identity makes
possible — plus removes the three superseded pages.

## Goals

- Email + password signup/login, fully self-contained (no email delivery, no
  OAuth app registration) so the person building/testing this can log in
  immediately without any external setup.
- One profile per user account (`profiles.user_id`, `UNIQUE`), with
  `POST /api/profile/save` becoming an upsert keyed by the logged-in user
  instead of always inserting a new row.
- `/profile` and `/advisor` (and their supporting API endpoints) require a
  valid session; identity is derived server-side from the session, not from
  client-submitted `profile_id`/`email` fields.
- After signup or login, land on `/profile`.
- Restrict faculty self-edit ("This is me" → editing `faculty_overrides`) to
  accounts whose own email matches the target faculty record's email.
- Delete `/baseline`, `/search`, `/chat` (pages, templates, and their API
  endpoints) and simplify the nav to Profile / Advisor / Log out.

## Non-goals

- No email verification or password reset flow (no email-sending
  infrastructure exists yet; a known, accepted limitation for this testing
  phase).
- No OAuth/SSO integration.
- No migration of existing anonymous `profiles` rows to accounts — they're
  left as orphaned test data (`user_id IS NULL`).
- No support for multiple profiles per account, or multiple accounts per
  profile.
- No change to `search.py`'s matching functions — they're shared with the
  advisor's own `search_faculty` tool and are unaffected by deleting the
  three standalone pages.
- Sessions do not survive a server restart (in-memory, matching the existing
  `_sessions` dict already used for advisor chat history) — logging in again
  after a restart is an accepted trade-off, not a defect.

## Design

### 1. Data model

```sql
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now'))
)
```

```sql
ALTER TABLE profiles ADD COLUMN user_id INTEGER UNIQUE REFERENCES users(id);
```

`users.email` is stored lowercased/trimmed and is the unique lookup key for
login. `profiles.user_id` is `UNIQUE`, enforcing one profile per account.

`POST /api/profile/save` changes from an unconditional `INSERT` to an upsert
keyed by `user_id` (`INSERT ... ON CONFLICT(user_id) DO UPDATE SET ...`),
matching the pattern already used for `faculty_overrides`
(`email TEXT PRIMARY KEY`, upserted). `profiles.email` is still populated on
save, but always from the logged-in user's `users.email` — never from a
client-submitted field — so it stays an authoritative value rather than
client-trusted input.

### 2. Password hashing

Stdlib only, no new dependency:

```python
import hashlib, secrets

def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return digest.hex(), salt

def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = _hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)
```

`secrets.compare_digest` avoids timing side-channels on the comparison.
200,000 PBKDF2 iterations is a widely-cited reasonable floor for SHA-256.

### 3. Sessions

At successful signup/login: `token = secrets.token_urlsafe(32)`, stored in a
new in-memory dict `_auth_sessions: dict[str, int]` (token → `user_id`),
mirroring the existing `_sessions: dict[str, list]` pattern already used for
advisor chat history. Set as an `HttpOnly`, `SameSite=Lax` cookie
(`session_token`). Logout removes the dict entry and clears the cookie.

A helper, `_current_user(request: Request) -> dict | None`, reads the cookie,
looks up `_auth_sessions`, and loads `{id, email}` from `users` — used by
every route that needs to know who's asking.

### 4. Routes

- `GET /login` — new page: one form, toggled between "Log in" and "Sign up"
  (email + password in both cases).
- `POST /api/auth/signup` — `{email, password}`. 400 if email already
  registered, or password is under 8 characters. Creates the user, starts a
  session, sets the cookie, returns `{email}`.
- `POST /api/auth/login` — `{email, password}`. 401 on no such email or wrong
  password. Starts a session, sets the cookie, returns `{email}`.
- `POST /api/auth/logout` — clears the session and cookie.
- `GET /api/auth/me` — returns `{id, email}` for the current session, or 401.
  Called by `/profile` and `/advisor` on page load; a 401 redirects to
  `/login` client-side (same shape as today's client-side "no profile" check,
  now checking real auth instead of `localStorage`).
- `GET /` — was an unconditional redirect to `/baseline` (being deleted); now
  redirects to `/profile` if `/api/auth/me` would succeed, else `/login`.
- `POST /api/profile/save`, `GET /api/profile/{id}` (collapses to "the
  current user's profile" — see below), `POST /api/advisor/chat`, and the
  `faculty-overrides` endpoints all require `_current_user(request)` to
  succeed (401 otherwise), and derive the profile/user identity from the
  session rather than a client-submitted `profile_id`. The frontend no longer
  needs to track a `profile_id` in `localStorage` at all — it only needs to
  know "am I logged in," which `/api/auth/me` answers.

### 5. Claim restriction

In the upsert path of `api_profile_save`, before writing to
`faculty_overrides`: look up the target faculty record's `email` and compare
it (lowercased) to the logged-in user's `users.email`. On mismatch, skip the
`faculty_overrides` write — same silent-skip shape as today's existing
blank-faculty-email case — while the requester's own `profiles` row still
saves normally.

### 6. Page cleanup

Delete `templates/baseline.html`, `templates/search.html`, `templates/chat.html`
and their routes (`GET /baseline`, `/search`, `/chat`) and API endpoints
(`POST /api/baseline`, `/api/search`, `/api/chat`) from `web_app.py`. Update
the nav markup in `profile.html`, `advisor.html`, and the new `login.html` to
just Profile / Advisor / Log out (Log out calls `POST /api/auth/logout` then
redirects to `/login`). `search.py`'s matching functions are untouched — only
the standalone pages/routes that called them directly are removed; the
advisor's own `_advisor_search` continues to call the same underlying
functions.

## Error handling

- Signup with an already-registered email → 400, "An account with that email
  already exists."
- Signup with a password under 8 characters → 400, "Password must be at
  least 8 characters."
- Login with an unknown email or wrong password → 401, a single generic
  message ("Incorrect email or password") for both cases — not revealing
  which one was wrong.
- Any protected page/endpoint with no/invalid/expired session → 401; pages
  redirect to `/login` client-side, API calls surface a clear inline error.
- `api_profile_save`'s faculty-email mismatch → silent skip of the
  `faculty_overrides` write only; the profile save itself still succeeds
  (matches the existing blank-email skip precedent).
- Server restart → all sessions invalidated; users simply log in again (this
  is the accepted trade-off from Non-goals, not a bug to guard against).

## Testing plan

- Automated (mirroring this repo's existing style — direct async calls with
  a fake request, monkeypatched `DB_PATH`):
  - `_hash_password`/`_verify_password` round-trip correctly, and reject a
    wrong password.
  - `users` table creation is idempotent; email uniqueness is enforced.
  - Signup rejects a duplicate email and a too-short password without
    raising.
  - Login succeeds with correct credentials, fails with wrong password and
    with an unknown email.
  - `POST /api/profile/save` upserts by `user_id` — two saves from the same
    session update one row, not two.
  - Claim restriction: saving with a `faculty_id` whose email doesn't match
    the session's user email skips the `faculty_overrides` write; a matching
    email writes it.
- Manual: full signup → land on `/profile` → complete the wizard → visit
  `/advisor` → log out → confirm `/profile`/`/advisor` now redirect to
  `/login` → log back in with the same credentials → confirm the same
  profile loads.
- Manual: confirm `/baseline`, `/search`, `/chat` return 404 (or are simply
  gone from the nav) after cleanup, and that `/advisor`'s own faculty search
  still works end-to-end.
