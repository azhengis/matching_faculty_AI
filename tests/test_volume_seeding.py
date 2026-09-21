"""Bringing up the app on a freshly attached persistent disk.

A disk arrives empty. Point DATA_DIR at it and DB_PATH names a file that does
not exist, so the app boots, creates its profile tables, and serves a directory
with nobody in it. Nothing fails: the process is up, the health check passes,
every search just returns nothing.

The documented fix was to copy the seed over SSH after attaching the disk —
a manual step to remember on the one day you change hosting plans. These tests
cover doing it automatically, and the two things that makes dangerous: it must
never overwrite real faculty data, and it must never cost anyone their account.
"""
import gzip
import os
import sqlite3

import pytest

import web_app


def _make_seed(path, names=("Ada Lovelace", "Grace Hopper")):
    """A miniature baked database, shaped like the real one."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    con.executemany("INSERT INTO faculty (name, email) VALUES (?, ?)",
                    [(n, f"{n.split()[0].lower()}@depaul.edu") for n in names])
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, faculty_id INTEGER, title TEXT)")
    con.execute("INSERT INTO papers (faculty_id, title) VALUES (1, 'On the Analytical Engine')")
    con.commit(); con.close()


@pytest.fixture
def volume(tmp_path, monkeypatch):
    """A repo root holding a baked faculty.db, plus an empty mounted volume."""
    root = tmp_path / "app";  root.mkdir()
    data = tmp_path / "data"; data.mkdir()
    _make_seed(root / "faculty.db")
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(data / "faculty.db"))
    return root, data


def _faculty(db):
    con = sqlite3.connect(db)
    try:
        return [r[0] for r in con.execute("SELECT name FROM faculty ORDER BY name")]
    finally:
        con.close()


# ── The empty disk ─────────────────────────────────────────────────────────

def test_an_empty_volume_is_populated_from_the_baked_database(volume):
    """The whole point. Without this the site comes up with nobody in it."""
    root, data = volume
    web_app._seed_data_dir_if_empty()
    assert _faculty(data / "faculty.db") == ["Ada Lovelace", "Grace Hopper"]


def test_papers_come_across_too(volume):
    """Faculty without publications match on almost nothing."""
    root, data = volume
    web_app._seed_data_dir_if_empty()
    con = sqlite3.connect(data / "faculty.db")
    assert con.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    con.close()


def test_a_missing_parent_directory_is_created(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db")
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "nope" / "faculty.db"))
    web_app._seed_data_dir_if_empty()
    assert _faculty(tmp_path / "nope" / "faculty.db") == ["Ada Lovelace", "Grace Hopper"]


def test_it_falls_back_to_the_committed_seed_archive(tmp_path, monkeypatch):
    """The builder bakes faculty.db, but the gzipped seed ships too. Either is
    enough to bring a volume up."""
    root = tmp_path / "app"; (root / "data").mkdir(parents=True)
    plain = tmp_path / "plain.db"
    _make_seed(plain)
    with open(plain, "rb") as f_in, gzip.open(root / "data" / "seed_faculty.db.gz", "wb") as f_out:
        f_out.write(f_in.read())
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "vol" / "faculty.db"))

    web_app._seed_data_dir_if_empty()
    assert _faculty(tmp_path / "vol" / "faculty.db") == ["Ada Lovelace", "Grace Hopper"]
    assert not (tmp_path / "vol" / ".seed.tmp").exists(), "temp file left behind"


# ── Never destroy what is already there ────────────────────────────────────

def test_a_populated_volume_is_left_completely_alone(volume):
    """Runs on every boot, so this is the assertion that keeps it safe."""
    root, data = volume
    con = sqlite3.connect(data / "faculty.db")
    con.execute("CREATE TABLE faculty (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
    con.execute("INSERT INTO faculty (name, email) VALUES ('Real Person', 'r@depaul.edu')")
    con.commit(); con.close()

    web_app._seed_data_dir_if_empty()
    assert _faculty(data / "faculty.db") == ["Real Person"]


def test_accounts_on_the_volume_survive_seeding(volume):
    """The dangerous version: a volume that already holds people's accounts but
    lost its faculty table. Copying the seed file over the top would take the
    accounts with it, which is the exact thing paying for a disk is meant to
    stop."""
    root, data = volume
    db = data / "faculty.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    con.execute("INSERT INTO users (email) VALUES ('professor@depaul.edu')")
    con.commit(); con.close()

    web_app._seed_data_dir_if_empty()

    con = sqlite3.connect(db)
    assert con.execute("SELECT email FROM users").fetchone()[0] == "professor@depaul.edu"
    assert con.execute("SELECT COUNT(*) FROM faculty").fetchone()[0] == 2
    con.close()


def test_running_twice_changes_nothing(volume):
    """Every boot runs it."""
    root, data = volume
    web_app._seed_data_dir_if_empty()
    before = _faculty(data / "faculty.db")
    web_app._seed_data_dir_if_empty()
    assert _faculty(data / "faculty.db") == before


def test_local_development_is_untouched(tmp_path, monkeypatch):
    """With no DATA_DIR set, DB_PATH is the repo's own faculty.db. Seeding it
    from itself would be at best pointless."""
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db", names=("Only Local",))
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(root / "faculty.db"))

    web_app._seed_data_dir_if_empty()
    assert _faculty(root / "faculty.db") == ["Only Local"]


# ── Failure is survivable ──────────────────────────────────────────────────

def test_a_missing_seed_does_not_stop_the_app(tmp_path, monkeypatch, capsys):
    """Better to come up empty and say so than to refuse to boot."""
    root = tmp_path / "app"; root.mkdir()
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "vol" / "faculty.db"))
    web_app._seed_data_dir_if_empty()          # must not raise
    assert "starting empty" in capsys.readouterr().out


def test_it_runs_before_the_profile_tables_are_created():
    """Order matters: _init_profiles_db creates tables in DB_PATH, and seeding
    has to have chosen the file by then."""
    import inspect
    src = inspect.getsource(web_app.lifespan)
    assert src.index("_seed_data_dir_if_empty()") < src.index("_init_profiles_db()")


# ── The blueprint and the code have to agree ───────────────────────────────

def _blueprint():
    import yaml
    from pathlib import Path
    path = Path(web_app.__file__).parent / "render.yaml"
    return yaml.safe_load(path.read_text())["services"][0]


def test_the_mount_path_and_data_dir_match():
    """If these drift, the app writes to the container filesystem while a paid
    disk sits empty beside it — and the data loss the disk was bought to
    prevent happens anyway, silently."""
    svc = _blueprint()
    mount = svc["disk"]["mountPath"]
    data_dir = next(e["value"] for e in svc["envVars"] if e["key"] == "DATA_DIR")
    assert data_dir == mount, f"DATA_DIR={data_dir} but disk mounts at {mount}"


def test_a_disk_is_only_declared_on_a_plan_that_can_mount_one():
    """Render free instances cannot take a disk; the deploy is rejected."""
    svc = _blueprint()
    if "disk" in svc:
        assert svc["plan"] != "free", "free instances cannot mount a persistent disk"


def test_the_api_key_is_never_committed():
    svc = _blueprint()
    key = next(e for e in svc["envVars"] if e["key"] == "ANTHROPIC_API_KEY")
    assert key.get("sync") is False and "value" not in key


# ── Accounts never travel in an image ──────────────────────────────────────

def test_account_tables_are_never_copied_from_the_seed(tmp_path, monkeypatch):
    """A baked seed is usually built from somebody's local database. Copying
    every table would put their test accounts — and whatever those accounts had
    written — onto a production volume. Only reference data travels."""
    root = tmp_path / "app"; root.mkdir()
    baked = root / "faculty.db"
    _make_seed(baked)
    con = sqlite3.connect(baked)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT)")
    con.execute("INSERT INTO users (email, password_hash) VALUES ('dev@example.com', 'x')")
    con.execute("CREATE TABLE auth_sessions (token TEXT, user_id INTEGER)")
    con.execute("INSERT INTO auth_sessions VALUES ('leaked-token', 1)")
    con.execute("CREATE TABLE proposals (id INTEGER PRIMARY KEY, background TEXT)")
    con.execute("INSERT INTO proposals (background) VALUES ('someone private draft')")
    con.commit(); con.close()

    monkeypatch.setattr(web_app, "_ROOT", str(root))
    vol = tmp_path / "vol" / "faculty.db"
    monkeypatch.setattr(web_app, "DB_PATH", str(vol))
    web_app._seed_data_dir_if_empty()

    con = sqlite3.connect(vol)
    present = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "faculty" in present and "papers" in present
    for leaked in ("users", "auth_sessions", "proposals"):
        assert leaked not in present, f"{leaked} was copied out of the seed image"


def test_the_allowlist_holds_only_reference_data():
    """A table added here later is a table that can travel in an image."""
    assert web_app._SEEDABLE_TABLES == {
        "faculty", "papers", "scholar_papers", "faculty_overrides"}
