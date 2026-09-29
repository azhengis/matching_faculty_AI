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


def test_the_plan_the_disk_and_data_dir_move_as_one_unit():
    """Three settings, two valid combinations, and every mixture is a bug:

      free    + no disk + no DATA_DIR   deploys, loses data on redeploy
      starter + disk    + DATA_DIR      deploys, keeps data

    Every other combination fails or silently misbehaves. A disk on a free
    instance is rejected outright by Render — which is how a blueprint edit
    made ahead of the actual upgrade broke the deploy. DATA_DIR without a disk
    points the database at a path nothing persists. A disk without DATA_DIR is
    the quiet one: the app writes to the container filesystem while the paid
    volume sits empty beside it, and the data loss it was bought to prevent
    happens anyway."""
    svc = _blueprint()
    paid = svc["plan"] != "free"
    has_disk = "disk" in svc
    data_dir = next((e.get("value") for e in svc["envVars"]
                     if e["key"] == "DATA_DIR"), None)

    assert has_disk == bool(data_dir), (
        f"disk declared={has_disk} but DATA_DIR={data_dir!r} — "
        "these must be set together or not at all")
    if has_disk:
        assert paid, "free instances cannot mount a persistent disk"
        assert data_dir == svc["disk"]["mountPath"], (
            f"DATA_DIR={data_dir} but the disk mounts at {svc['disk']['mountPath']}")


def test_the_upgrade_instructions_stay_next_to_the_thing_they_change():
    """The three edits live commented in the file so nobody has to remember
    them. If the disk block is uncommented the instructions have served their
    purpose; while it is commented they must still be there."""
    from pathlib import Path
    text = (Path(web_app.__file__).parent / "render.yaml").read_text()
    if "disk" not in _blueprint():
        assert "TO UPGRADE" in text
        assert "mountPath: /data" in text, "the disk block to uncomment is missing"
        assert "value: /data" in text, "the DATA_DIR line to uncomment is missing"


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
    try:
        # Reference data arrives.
        assert con.execute("SELECT COUNT(*) FROM faculty").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        # Account data does not. The tables may exist — _init_profiles_db
        # creates them anyway — but every row from the image is gone.
        for leaked in ("users", "auth_sessions", "proposals"):
            n = con.execute(f"SELECT COUNT(*) FROM {leaked}").fetchone()[0]
            assert n == 0, f"{leaked} carried {n} row(s) out of the seed image"
        # Specifically: no credential material.
        assert con.execute(
            "SELECT COUNT(*) FROM users WHERE email = 'dev@example.com'"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_the_allowlist_holds_only_reference_data():
    """A table added here later is a table that can travel in an image."""
    assert web_app._SEEDABLE_TABLES == {
        "faculty", "papers", "scholar_papers", "faculty_overrides"}


# ── Seeding must never stop the app starting ───────────────────────────────

@pytest.mark.parametrize("failing,exc", [
    ("os.makedirs",     OSError("read-only file system")),
    ("sqlite3.connect", sqlite3.OperationalError("unable to open database file")),
])
def test_a_filesystem_failure_does_not_take_the_app_down(failing, exc, tmp_path,
                                                         monkeypatch, capsys):
    """Every failure here is a filesystem one on a disk mounted seconds ago:
    not writable yet, full, wrong permissions. Letting any of them out of
    lifespan kills the process, the health check times out and the deploy
    fails — trading "the directory is empty" for "the site is gone".

    These three calls sat outside any try/except, which is a plausible cause of
    a deploy failing the moment a disk was first attached."""
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db")
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "vol" / "faculty.db"))

    module, _, attr = failing.rpartition(".")
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr({"os": os, "sqlite3": sqlite3}[module], attr, boom)

    web_app._seed_data_dir_if_empty()           # must not raise
    assert "[seed] skipped" in capsys.readouterr().out


def test_the_app_still_starts_when_the_volume_cannot_be_seeded(tmp_path, monkeypatch):
    """The whole point, stated as the outcome rather than the mechanism."""
    monkeypatch.setattr(web_app, "_ROOT", str(tmp_path / "nothing-here"))
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "vol" / "faculty.db"))
    def boom(*a, **k):
        raise OSError("read-only file system")
    monkeypatch.setattr(os, "makedirs", boom)

    web_app._seed_data_dir_if_empty()           # the assertion is that this returns


def test_a_full_disk_while_unpacking_the_seed_does_not_take_the_app_down(
        tmp_path, monkeypatch, capsys):
    """The gzip fallback writes a temporary file onto the volume. That is the
    one place seeding needs free space, so it is the one most likely to fail on
    a disk that is smaller than someone thought."""
    import shutil
    root = tmp_path / "app"; (root / "data").mkdir(parents=True)
    plain = tmp_path / "plain.db"
    _make_seed(plain)
    with open(plain, "rb") as f_in, gzip.open(root / "data" / "seed_faculty.db.gz", "wb") as f_out:
        f_out.write(f_in.read())
    monkeypatch.setattr(web_app, "_ROOT", str(root))       # no baked faculty.db
    monkeypatch.setattr(web_app, "DB_PATH", str(tmp_path / "vol" / "faculty.db"))

    def boom(*a, **k):
        raise OSError("no space left on device")
    monkeypatch.setattr(shutil, "copyfileobj", boom)

    web_app._seed_data_dir_if_empty()                      # must not raise
    assert "[seed] skipped" in capsys.readouterr().out


# ── Startup memory ─────────────────────────────────────────────────────────

def test_the_paper_index_is_not_loaded_during_startup():
    """56MB of embeddings that only the Stage 4 collaborator search reads.
    Loading them at startup put them inside the container's memory high-water
    mark, which is where a 512MB instance was OOM-killed — before the health
    check had even run."""
    import inspect
    src = inspect.getsource(web_app.lifespan)
    assert "get_paper_index" not in src, \
        "the paper index is being loaded during startup again"


def test_the_paper_index_loads_on_first_use_and_is_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(web_app, "_st", {"people": [], "model": object()})
    monkeypatch.setattr(web_app.sm, "get_paper_index",
                        lambda *a, **k: calls.append(1) or {"by_faculty": {}})
    assert web_app._paper_index() == {"by_faculty": {}}
    web_app._paper_index()
    assert len(calls) == 1, "the index should be built once, then cached"


def test_the_collaborator_search_goes_through_the_lazy_accessor():
    """If any caller reads _st["paper_idx"] directly it gets None before the
    first load, and silently returns no publications as evidence."""
    from pathlib import Path
    src = Path(web_app.__file__).read_text()
    body = src[src.index("def _paper_index"):]
    body = body[body.index("# ── App startup"):]      # everything after it
    assert '_st["paper_idx"]' not in body, \
        "something reads the index directly instead of calling _paper_index()"


# ── The two host configs must not disagree ─────────────────────────────────

def _fly():
    from pathlib import Path
    import re
    text = (Path(web_app.__file__).parent / "fly.toml").read_text()
    # Small hand parse; tomllib would do, but this keeps the test readable
    # about exactly which two lines it cares about.
    model = re.search(r'CHATBOT_MODEL\s*=\s*"([^"]+)"', text).group(1)
    mem   = re.search(r'memory\s*=\s*"(\d+)mb"', text).group(1)
    data  = re.search(r'DATA_DIR\s*=\s*"([^"]+)"', text).group(1)
    mount = re.search(r'destination\s*=\s*"([^"]+)"', text).group(1)
    return {"model": model, "memory_mb": int(mem), "data_dir": data, "mount": mount}


def test_both_hosts_talk_to_the_same_model():
    """A researcher must not get a different advisor depending on where it is
    deployed. Haiku was tried and rejected: it kept grading answers through
    four prompt iterations."""
    fly = _fly()
    render = next(e["value"] for e in _blueprint()["envVars"]
                  if e["key"] == "CHATBOT_MODEL")
    assert fly["model"] == render, f"fly={fly['model']} render={render}"


def test_the_fly_volume_and_data_dir_agree():
    """Same invariant as the Render one: a mount the app does not write to is
    a paid disk sitting empty while data goes to the container filesystem."""
    fly = _fly()
    assert fly["data_dir"] == fly["mount"]


def test_fly_is_given_more_than_the_memory_that_was_killed():
    """512MB is what Render OOM-killed during startup. Shipping the same
    number here would reproduce it on the alternative host."""
    assert _fly()["memory_mb"] >= 1024


# ── The AWS deployment assets ──────────────────────────────────────────────

def _deploy_dir():
    from pathlib import Path
    return Path(web_app.__file__).parent / "deploy" / "aws"


@pytest.mark.parametrize("script", ["bootstrap.sh", "enable-https.sh", "backup.sh"])
def test_the_deploy_scripts_are_valid_shell(script):
    """A syntax error here is found at 2am on a box with no app on it."""
    import subprocess
    r = subprocess.run(["bash", "-n", str(_deploy_dir() / script)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("script", ["bootstrap.sh", "enable-https.sh", "backup.sh"])
def test_the_deploy_scripts_fail_fast(script):
    """set -euo pipefail. Without it a failed step is skipped over and the
    script reports success having done half its job."""
    assert "set -euo pipefail" in (_deploy_dir() / script).read_text()


def test_the_host_volume_matches_the_data_dir_the_container_is_told_to_use():
    """The same invariant as Render's and Fly's. A mount the app does not
    write to means data goes to the container filesystem and dies with it."""
    unit = (_deploy_dir() / "bootstrap.sh").read_text()
    assert "-v /var/lib/faculty-matcher:/data" in unit
    assert "-e DATA_DIR=/data" in unit


def test_the_service_survives_a_reboot_and_a_crash():
    unit = (_deploy_dir() / "bootstrap.sh").read_text()
    assert "Restart=always" in unit
    assert "systemctl enable faculty-matcher" in unit


def test_the_api_key_is_read_from_a_file_not_baked_into_the_unit():
    """A systemd unit is world-readable. The key belongs in an env file the
    README tells you to chmod 600."""
    unit = (_deploy_dir() / "bootstrap.sh").read_text()
    assert "EnvironmentFile=/opt/faculty-matcher/env" in unit
    assert "sk-ant-" not in unit or "sk-ant-..." in unit   # placeholder only


def test_no_real_secret_is_committed_in_the_deploy_assets():
    import re
    for path in _deploy_dir().glob("*"):
        text = path.read_text()
        real = re.findall(r"sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}", text)
        assert real == [], f"{path.name} contains what looks like a live key"


def test_the_backup_uses_sqlites_own_backup_not_a_file_copy():
    """cp on a live SQLite file can capture a torn page — a backup that
    restores to a corrupt database, discovered only when it is needed."""
    text = (_deploy_dir() / "backup.sh").read_text()
    assert ".backup" in text
    assert "cp " not in text.replace("aws s3 cp", "")


def test_https_setup_refuses_to_run_against_the_wrong_dns():
    """Requesting a certificate for a name that points elsewhere fails in a
    way that is hard to read. Check first, say so plainly."""
    text = (_deploy_dir() / "enable-https.sh").read_text()
    assert "does not resolve" in text
    assert "checkip.amazonaws.com" in text


def test_ci_builds_the_image_because_the_instance_cannot():
    """The builder stage needs several GB; the instance has one. If this
    workflow disappears there is no way to produce an image to deploy."""
    from pathlib import Path
    wf = Path(web_app.__file__).parent / ".github" / "workflows" / "build-image.yml"
    assert wf.is_file()
    text = wf.read_text()
    assert "ghcr.io" in text
    assert "cache-from: type=gha" in text, "without caching every push rebuilds the model export"


def test_the_image_name_is_lowercased_before_tagging():
    """Docker references must be lowercase. This repository is
    azhengis/matching_faculty_AI, so ${{ github.repository }} used directly
    produces an invalid tag and build-push-action fails in ONE SECOND with
    "repository name must be lowercase" — before the build starts, which reads
    like a build error rather than a naming one. It cost a full CI run to
    diagnose."""
    from pathlib import Path
    wf = (Path(web_app.__file__).parent / ".github" / "workflows"
          / "build-image.yml").read_text()
    assert "${GITHUB_REPOSITORY,,}" in wf, \
        "the image name is not being lowercased; tags will be invalid"
    # And the raw form must not survive anywhere in the tag list.
    assert "IMAGE: ${{ github.repository }}" not in wf


def test_the_repository_name_actually_needs_lowercasing():
    """Guards the guard: if the repo is ever renamed to something already
    lowercase, the test above would pass vacuously and the reason for the step
    would be lost."""
    import subprocess
    url = subprocess.run(["git", "remote", "get-url", "origin"],
                         capture_output=True, text=True).stdout.strip()
    if not url:
        pytest.skip("no git remote configured")
    name = url.rsplit("/", 1)[-1].removesuffix(".git")
    if name.islower():
        pytest.skip(f"repository {name!r} is already lowercase")
    assert not name.islower()      # documents why the step exists


# ── Railway ────────────────────────────────────────────────────────────────

def _railway():
    import json
    from pathlib import Path
    return json.loads((Path(web_app.__file__).parent / "railway.json").read_text())


def test_railway_builds_the_dockerfile_not_a_guessed_buildpack():
    """Nixpacks would try to infer a Python app and miss the ONNX export and
    the baked indexes entirely."""
    b = _railway()["build"]
    assert b["builder"] == "DOCKERFILE"
    assert b["dockerfilePath"] == "Dockerfile"


def test_railway_binds_the_port_it_is_given():
    """Railway assigns $PORT; it is not fixed at 8000. Binding the wrong one
    means the health check never connects and the deploy is rolled back."""
    assert "$PORT" in _railway()["deploy"]["startCommand"]


def test_the_health_check_allows_time_for_the_model_to_load():
    """First boot loads the ONNX model and seeds the volume. A default
    timeout fails a start that is working perfectly, and the failure looks
    like a crash."""
    d = _railway()["deploy"]
    assert d["healthcheckPath"] == "/login"     # 200 without a session
    assert d["healthcheckTimeout"] >= 300


def test_the_health_check_path_really_answers_without_a_session():
    """Checking an authenticated path would mark every healthy deploy failed,
    because the checker has no cookie. Asserted against the app, not just the
    config: /login is a route, and / redirects rather than returning 200."""
    from fastapi.testclient import TestClient
    client = TestClient(web_app.app)
    assert client.get(_railway()["deploy"]["healthcheckPath"]).status_code == 200


def test_every_host_config_agrees_on_the_model():
    """Three hosts now describe this app. A researcher must not get a
    different advisor depending on which one is serving."""
    render = next(e["value"] for e in _blueprint()["envVars"]
                  if e["key"] == "CHATBOT_MODEL")
    assert _fly()["model"] == render
    # Railway sets it in the dashboard, so the doc is what carries it.
    from pathlib import Path
    doc = (Path(web_app.__file__).parent / "docs" / "DEPLOYMENT.md").read_text()
    assert f"`{render}`" in doc, "the Railway setup table names a different model"


# ── The indexes have to reach the volume too ───────────────────────────────

def test_the_prebuilt_indexes_are_seeded_onto_an_empty_volume(tmp_path, monkeypatch):
    """The bug that killed three deploys. search.py resolves its index paths
    under DATA_DIR; the Dockerfile bakes them next to the code. Point DATA_DIR
    at a fresh volume and the app finds no index, decides to build one, and
    falls back from the 110MB ONNX encoder to full SPECTER2 via torch (~834MB)
    to embed 1,440 faculty during startup. Killed partway through, with a log
    that says "Killed" while building embeddings."""
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db")
    (root / "faculty_index.pkl").write_bytes(b"FACULTY-INDEX")
    (root / "paper_index.pkl").write_bytes(b"PAPER-INDEX")
    vol = tmp_path / "vol"
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DATA_DIR", str(vol))
    monkeypatch.setattr(web_app, "DB_PATH", str(vol / "faculty.db"))

    web_app._seed_data_dir_if_empty()

    assert (vol / "faculty_index.pkl").read_bytes() == b"FACULTY-INDEX"
    assert (vol / "paper_index.pkl").read_bytes() == b"PAPER-INDEX"


def test_an_existing_index_on_the_volume_is_never_overwritten(tmp_path, monkeypatch):
    """An index is rewritten when the faculty text changes and its fingerprint
    stops matching. That rebuilt copy is newer than the baked one and must
    survive every subsequent boot."""
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db")
    (root / "faculty_index.pkl").write_bytes(b"BAKED")
    vol = tmp_path / "vol"; vol.mkdir()
    (vol / "faculty_index.pkl").write_bytes(b"REBUILT-ON-THE-VOLUME")
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DATA_DIR", str(vol))
    monkeypatch.setattr(web_app, "DB_PATH", str(vol / "faculty.db"))

    web_app._seed_data_dir_if_empty()
    assert (vol / "faculty_index.pkl").read_bytes() == b"REBUILT-ON-THE-VOLUME"


def test_local_development_does_not_copy_indexes_onto_itself(tmp_path, monkeypatch):
    root = tmp_path / "app"; root.mkdir()
    _make_seed(root / "faculty.db")
    (root / "faculty_index.pkl").write_bytes(b"X")
    monkeypatch.setattr(web_app, "_ROOT", str(root))
    monkeypatch.setattr(web_app, "DATA_DIR", str(root))
    monkeypatch.setattr(web_app, "DB_PATH", str(root / "faculty.db"))
    web_app._seed_data_dir_if_empty()           # must be a no-op, not a self-copy
    assert (root / "faculty_index.pkl").read_bytes() == b"X"


def test_where_search_looks_for_an_index_is_where_seeding_puts_one():
    """The two must agree. They did not, and nothing failed until a volume was
    attached — so this asserts the contract rather than the symptom."""
    import search as sm
    from pathlib import Path
    assert Path(sm.INDEX).name == "faculty_index.pkl"
    assert Path(sm.PAPER_INDEX).name == "paper_index.pkl"
    src = Path(web_app.__file__).read_text()
    assert '"faculty_index.pkl", "paper_index.pkl"' in src, \
        "seeding no longer copies the files search.py expects to find"


def test_the_rebuild_path_says_what_it_will_cost():
    """When it does happen, the log has to explain itself. "Killed" partway
    through building embeddings reads as an app fault; it is a missing file."""
    from pathlib import Path
    src = (Path(web_app.__file__).parent / "search.py").read_text()
    assert "No usable index at" in src
    assert "OOM-killed" in src


# ── Say whether data will survive ──────────────────────────────────────────

def test_startup_warns_loudly_when_data_will_not_survive(tmp_path, monkeypatch, capsys):
    """Accounts vanished on every redeploy for weeks and nothing in the log
    said so — the faculty roster always came back, because it is baked into
    the image, which made it look like the database was fine."""
    import inspect, re
    src = inspect.getsource(web_app.lifespan)
    # The message is built across several source lines; join them so the test
    # reads the text a user would see, not the way it happens to be wrapped.
    flat = re.sub(r'"\s*\n\s*"', "", src)
    assert "EPHEMERAL" in flat
    assert "will be LOST on the next deploy" in flat
    assert "Mount a volume and set DATA_DIR" in flat


def test_startup_confirms_persistence_when_a_volume_is_mounted():
    """The reassuring half matters too: somebody who mounted a volume should
    be able to see that it took effect, rather than inferring it."""
    import inspect
    assert "persistent — user data survives redeploys" in inspect.getsource(web_app.lifespan)


def test_nothing_expires_a_profile():
    """Sessions expire after 30 days; profiles never do. A sweep that reached
    profiles, users or projects would silently delete somebody's proposal."""
    from pathlib import Path
    src = Path(web_app.__file__).read_text()
    for table in ("profiles", "users", "projects", "proposals"):
        assert f"DELETE FROM {table} WHERE datetime" not in src, \
            f"something expires rows in {table}"
    assert "DELETE FROM auth_sessions WHERE datetime(expires_at)" in src
