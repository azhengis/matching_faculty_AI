import sqlite3

import web_app
from auth import hash_password, verify_password


def _columns(con, table):
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()]


def test_hash_password_generates_salt_and_hash_of_expected_length():
    password_hash, salt = hash_password("correct horse battery staple")
    assert len(salt) == 32          # secrets.token_hex(16) -> 32 hex chars
    assert len(password_hash) == 64  # sha256 digest -> 64 hex chars


def test_hash_password_is_deterministic_given_same_salt():
    password_hash1, salt = hash_password("hunter22")
    password_hash2, _ = hash_password("hunter22", salt=salt)
    assert password_hash1 == password_hash2


def test_hash_password_differs_across_calls_without_explicit_salt():
    password_hash1, salt1 = hash_password("hunter22")
    password_hash2, salt2 = hash_password("hunter22")
    assert salt1 != salt2
    assert password_hash1 != password_hash2


def test_verify_password_accepts_correct_password():
    password_hash, salt = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash, salt) is True


def test_verify_password_rejects_wrong_password():
    password_hash, salt = hash_password("correct horse battery staple")
    assert verify_password("wrong password", password_hash, salt) is False


def test_init_profiles_db_creates_users_table(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))

    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    cols = _columns(con, "users")
    assert cols == ["id", "email", "password_hash", "password_salt", "created_at"]
    con.close()


def test_users_table_enforces_unique_email(tmp_path, monkeypatch):
    db_path = tmp_path / "test_faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO users (email, password_hash, password_salt) VALUES ('jane@depaul.edu', 'h', 's')"
    )
    con.commit()

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO users (email, password_hash, password_salt) VALUES ('jane@depaul.edu', 'h2', 's2')"
        )
        con.commit()
    con.close()
