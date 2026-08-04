# Deployment

The goal: a URL the AI Institute team can open and test against.

## What it actually needs

Measured, not estimated:

| | |
|---|---|
| Steady state | **280 MB** after a real query encode and a 25-pair rerank |
| Peak, faculty index rebuild | 238 MB (63s) |
| Peak, paper index rebuild | 262 MB (475s, 18,665 papers) |

So it fits any 512 MB instance, including free tiers. SPECTER2's weights are
mmap'd from safetensors and cost far less resident than their 440 MB on disk.

Two things still constrain the choice of host:

**It is not serverless-shaped.** Model load takes ~10s and the indexes stay in
memory between requests. Vercel, Lambda, and Cloud Functions are the wrong
tool — torch alone exceeds their bundle limits. Use a host that runs a
persistent process.

**Writable state needs somewhere to live.** `DATA_DIR` controls where the
database, indexes, and uploads go. Leave it unset and everything sits beside
the code, which is right for a free host with an ephemeral filesystem. Set it
to a mounted volume and user accounts survive redeploys.

---

## Option 1 — Render free tier (zero cost)

**Use this to get a link in front of the team.** The repo carries everything
needed: `render.yaml` describes the service, and the Docker build unpacks a
committed public-data seed and bakes the embedding indexes into the image, so
there is no data upload step and no slow first boot.

1. Push to GitHub (done).
2. At [render.com](https://render.com) → **New** → **Blueprint**, pick this
   repo. It reads `render.yaml`.
3. Set `ANTHROPIC_API_KEY` in the dashboard when prompted.
4. Deploy. First build takes ~15 minutes — most of it is baking the indexes.

### What you are trading away

**Free instances have no persistent disk, and the filesystem is destroyed on
every spin-down — which happens after 15 minutes without traffic.** Concretely:
faculty data always comes back, because it is baked into the image. Everything
a tester creates — their account, their projects, their proposals — does not.

A tester who works through a proposal in one sitting is fine; the container
stays warm while in use. A tester who comes back tomorrow finds their account
gone. Say so when you send the link, or use Option 2.

Free instances also get 750 hours/month across the workspace, and the first
request after a sleep pays ~60s of cold start.

### Making it durable while staying free

Litestream continuously replicates SQLite to object storage and restores on
boot, which fixes the spin-down problem without paying for a disk. Cloudflare
R2 and Backblaze B2 both have free tiers that comfortably fit a 12 MB database.

Sketch: add `litestream` to the image, point it at the bucket, and change the
entrypoint to `litestream restore -if-db-not-exists` then
`litestream replicate -exec "uvicorn ..."`. Roughly an hour of work, and worth
it before real faculty rely on it. Not done here.

---

## Option 2 — any host with a volume (~$3-7/month)

Render Starter ($7/mo) plus a 1 GB disk, or an equivalent elsewhere. Add a
`disk:` block to `render.yaml` mounted at `/data` and set `DATA_DIR=/data`.
Nothing else changes, and accounts and proposals then survive redeploys.

---

## Option 3 — Fly.io

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
- Environment: `DATA_DIR=/data`, `CHATBOT_MODEL=anthropic/claude-sonnet-5`,
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
Environment=CHATBOT_MODEL=anthropic/claude-sonnet-5
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
