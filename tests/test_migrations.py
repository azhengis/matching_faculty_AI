"""The schema migrations in _init_profiles_db.

Four `projects` migrations once sat ABOVE their own CREATE TABLE. On an
existing database the columns were already present so nothing looked wrong;
on a fresh one the ALTER hit a table that did not exist yet, was swallowed by
a bare `except sqlite3.OperationalError: pass`, and produced an install
missing chat_history, gap_map, lit_references, and mode. It could only ever
have broken a first deploy.

These tests cover both halves of the fix: migrations run after the table they
alter, and a migration that goes wrong is now loud instead of silent.
"""
import re
import sqlite3
import sys
from pathlib import Path

import pytest

import web_app

SOURCE = Path(web_app.__file__).read_text()


def _fresh(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    return sqlite3.connect(db_path)


def _columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


# ── The ordering invariant ───────────────────────────────────────────────────

def test_every_migration_runs_after_the_table_it_alters_is_created():
    """The structural guard. A new migration added above its CREATE TABLE
    fails here rather than on somebody's first deploy."""
    creates, alters = {}, []
    for lineno, line in enumerate(SOURCE.splitlines(), 1):
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", line)
        if m:
            creates.setdefault(m.group(1), lineno)
        m = re.search(r'_add_column\(con, "(\w+)", ', line)
        if m:
            alters.append((lineno, m.group(1)))

    assert alters, "no migrations found — did _add_column get renamed?"

    misordered = [
        (table, lineno, creates.get(table))
        for lineno, table in alters
        if creates.get(table) is None or lineno < creates[table]
    ]
    assert misordered == [], (
        "migration(s) run before their CREATE TABLE: "
        + ", ".join(f"{t} at line {a} (created at {c})" for t, a, c in misordered))


def test_migrations_all_go_through_the_guarded_helper():
    """A raw ALTER with a bare except is how the bug hid. Only _add_column's
    own implementation may issue one."""
    offenders = [
        lineno for lineno, line in enumerate(SOURCE.splitlines(), 1)
        if "ADD COLUMN" in line and "_add_column" not in line
        and "f\"ALTER TABLE {table} ADD COLUMN {column} {decl}\"" not in line
        and not line.strip().startswith("#")
    ]
    assert offenders == [], f"un-guarded ALTER ... ADD COLUMN at line(s) {offenders}"


# ── The behaviour of the guard ───────────────────────────────────────────────

def test_adding_a_column_twice_is_tolerated():
    """Startup runs on every boot, so a repeat must be a no-op."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (id INTEGER)")
    web_app._add_column(con, "t", "a", "TEXT")
    web_app._add_column(con, "t", "a", "TEXT")
    assert _columns(con, "t") == ["id", "a"]
    con.close()


def test_a_migration_against_a_missing_table_raises_instead_of_passing_silently():
    """This is the exact latent bug. Silence here is what cost us months."""
    con = sqlite3.connect(":memory:")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        web_app._add_column(con, "never_created", "a", "TEXT")
    con.close()


def test_a_malformed_column_declaration_raises():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t (id INTEGER)")
    with pytest.raises(sqlite3.OperationalError):
        web_app._add_column(con, "t", "b", "NOT A REAL TYPE (((")
    con.close()


# ── The result on a brand-new database ───────────────────────────────────────

EXPECTED = {
    "projects": ["chat_history", "gap_map", "lit_references", "mode"],
    "profiles": ["research_interests", "user_id", "photo_file", "chat_history",
                 "research_activities", "explore_history"],
    "proposals": ["edited_sections", "problem_statement", "novelty", "abstract",
                  "ethical_considerations", "ai_role"],
    "project_matches": ["relevant_work"],
    "profile_documents": ["research_summary"],
}


@pytest.mark.parametrize("table,columns", sorted(EXPECTED.items()))
def test_a_fresh_database_has_every_migrated_column(table, columns, tmp_path, monkeypatch):
    """What a first deploy actually gets."""
    con = _fresh(tmp_path, monkeypatch)
    present = _columns(con, table)
    con.close()
    missing = [c for c in columns if c not in present]
    assert missing == [], f"{table} is missing {missing} on a fresh install"


def test_initialising_twice_is_idempotent(tmp_path, monkeypatch):
    """Every server start re-runs this."""
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(db_path))
    web_app._init_profiles_db()
    before = {t: _columns(sqlite3.connect(db_path), t) for t in EXPECTED}

    web_app._init_profiles_db()   # must not raise
    after = {t: _columns(sqlite3.connect(db_path), t) for t in EXPECTED}
    assert before == after
