"""
auth.py
-------
Pure, dependency-free password hashing for the login system. No FastAPI,
no database access — just hash/verify, so it's trivial to unit test and
reuse.
"""
import hashlib
import secrets

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Hash a password with PBKDF2-HMAC-SHA256. Returns (hash_hex, salt_hex).

    Generates a new random salt if one isn't provided (signup); pass the
    stored salt back in to verify a login attempt.
    """
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Check a password against a stored hash+salt using a constant-time comparison."""
    candidate, _ = hash_password(password, salt)
    return secrets.compare_digest(candidate, password_hash)
