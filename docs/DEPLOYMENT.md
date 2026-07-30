# Deployment

The goal: a URL the AI Institute team can open and test against.

## What makes this app awkward to host

Read this before picking a host — three constraints rule out most default choices.

**1. It needs ~1.5GB of RAM, steady-state.** SPECTER2's weights, the torch
runtime, and the unpickled paper index all sit resident. A 512MB or 1GB
instance will OOM while building embeddings on boot, not gracefully degrade.

**2. It is not serverless-shaped.** Model load takes ~30s and the embedding
indexes are held in memory between requests. Vercel, Lambda, and Cloud
Functions are the wrong tool — every cold start would pay the full model load,
and the 250MB unzipped bundle limit is smaller than torch alone. Use a host
that runs a persistent process.

**3. The database must persist and must not be in the image.** `faculty.db`
holds user accounts (with password hashes), projects, and proposals. It is
gitignored and `.dockerignore`d for that reason. A container filesystem is
wiped on every deploy, so it needs a mounted volume — otherwise the first
redeploy silently destroys every account and proposal.

`DATA_DIR` controls where the database, indexes, and uploads live. It defaults
to the repo root, so local development is unchanged; production points it at
the volume.

---

## Deploying to Fly.io

Fly is preconfigured here (`fly.toml`, `Dockerfile`) because it does persistent
volumes and always-on machines simply. Render, Railway, or a DePaul-hosted VM
work equally well — the constraints above are what matter, not the vendor.

### 1. Create the app and volume

```bash
fly launch --no-deploy          # accepts fly.toml; may rename if the name is taken
fly volumes create faculty_data --size 3 --region ord
```

3GB fits the 12MB database, the 60MB paper index, and room for uploads.

### 2. Set the API key as a secret

```bash
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
```

Never put this in `fly.toml` — that file is committed.

### 3. Deploy

```bash
fly deploy
```

The first build takes 10–15 minutes: it downloads CPU-only torch and bakes
SPECTER2 and the cross-encoder into the image so boots don't depend on
HuggingFace being reachable.

### 4. Seed the database

The app builds its own schema on first boot, but an empty database matches
nothing. Build a seed containing only public faculty data:

```bash
python3 pipeline/make_seed_db.py       # -> seed_faculty.db
```

This copies `faculty.db`, drops every user table (accounts, password hashes,
sessions, profiles, projects, proposals, uploads, self-edits), VACUUMs so the
rows are really gone rather than sitting in free pages, and verifies the result
before writing it. Your local test data stays local; testers start clean.

Upload it as the live database:

```bash
fly ssh console -C "mkdir -p /data"
fly sftp shell
  put seed_faculty.db /data/faculty.db
```

Then upload the paper index to skip a slow first boot:

```bash
fly sftp shell
  put paper_index.pkl /data/paper_index.pkl
```

**Do not bother shipping `faculty_index.pkl`.** Stripping `faculty_overrides`
changes the research text of anyone who had self-edited, so the index
fingerprint no longer matches and it rebuilds on boot regardless (~2 minutes
for 1,389 faculty). The paper index is unaffected and is reused, which is the
one worth uploading — it covers 18,665 papers.

### 5. Check it

```bash
fly logs                        # "Loading SPECTER2 model..." then Uvicorn running
fly open
```

---

## Deploying somewhere else

The `Dockerfile` is host-agnostic. Any platform that runs a persistent
container with a mounted volume works — only the plumbing differs.

### Render

Create a **Web Service** from the repo, Docker environment.

- Instance type: **Standard** or larger (2GB). Starter is 512MB and will OOM.
- Add a **Disk**: 3GB, mount path `/data`.
- Environment: `DATA_DIR=/data`, `CHATBOT_MODEL=anthropic/claude-haiku-4-5`,
  and `ANTHROPIC_API_KEY` as a secret.
- Health check path: `/login` (it returns 200 without a session; `/` redirects).

Render has no SFTP. Seed the volume from a shell on the instance:
`render ssh` (or the dashboard Shell tab), then pull `seed_faculty.db` from
somewhere reachable — a signed URL, or `base64` through the shell for a 12MB
file.

### Railway

`railway up` picks up the Dockerfile. Add a volume mounted at `/data`, set the
same three environment variables, then seed via `railway run` with a shell.

### A DePaul VM (or any Linux box)

Often the best answer for university data — no third-party host holding it, no
per-month cost. Without Docker:

```bash
git clone <repo> /opt/faculty-matcher && cd /opt/faculty-matcher
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo mkdir -p /var/lib/faculty-matcher
sudo cp seed_faculty.db /var/lib/faculty-matcher/faculty.db
sudo cp paper_index.pkl /var/lib/faculty-matcher/
```

`/etc/systemd/system/faculty-matcher.service`:

```ini
[Unit]
Description=DePaul Faculty Matcher
After=network.target

[Service]
WorkingDirectory=/opt/faculty-matcher
Environment=DATA_DIR=/var/lib/faculty-matcher
Environment=CHATBOT_MODEL=anthropic/claude-haiku-4-5
EnvironmentFile=/etc/faculty-matcher.env      # holds ANTHROPIC_API_KEY, chmod 600
ExecStart=/opt/faculty-matcher/.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Then `systemctl enable --now faculty-matcher` and put nginx or Apache in front
for TLS. Bind to `127.0.0.1` as above so only the reverse proxy is exposed.

---

## Before sending the link out

- [ ] **Anyone with the URL can sign up.** There is no email-domain restriction
      and no invite gate. For a small internal test that is probably fine; if it
      isn't, add one before sharing.
- [ ] **Data on this host is scraped from public DePaul pages.** No private
      records, but it is a public URL with real people's names on it. Worth a
      moment's thought about whether it should be indexable.
- [ ] `fly secrets list` shows `ANTHROPIC_API_KEY` and nothing unexpected.
- [ ] Sign up, complete a profile, and run one advisor conversation end to end.
      The advisor is the part that costs money per message — confirm the model
      is Haiku (`fly config env`) and not something pricier.
- [ ] Set a spend limit. The advisor makes an LLM call per turn plus literature
      searches; an unattended loop is the failure mode that produces a surprise
      bill.

## Cost

Roughly $5–10/month for a 2GB shared-CPU machine kept warm, plus 3GB of volume.
LLM usage is separate and depends on conversation volume — Haiku 4.5 is
$1/$5 per million tokens, and a full proposal conversation is a few cents.

## Rolling back

```bash
fly releases                    # list
fly deploy --image <previous>   # or: fly releases rollback
```

The volume is untouched by a rollback, so accounts and proposals survive.
