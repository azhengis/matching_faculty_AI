# Deployment

The goal: a URL the AI Institute team can open and test against.

## What it actually needs

Measured, not estimated:

| | |
|---|---|
| Steady state | **280 MB** after a real query encode and a 25-pair rerank |
| Peak, faculty index rebuild | 238 MB (63s) |
| Peak, paper index rebuild | 262 MB (475s, 18,665 papers) |

> **512 MB is not enough, despite those numbers.** They measure steady state
> *after* startup. The high-water mark is *during* boot, when the ONNX session,
> the tokenizer and the faculty index are allocated at once — and a 512 MB
> container on Render was OOM-killed there, before its health check ran.
>
> Three changes cut that peak: the ONNX CPU arena is disabled, the 56 MB paper
> index now loads on first use rather than at boot, and startup prints its own
> peak RSS so the real figure is in the log rather than estimated. **Give it
> 1 GB.** The difference is about a dollar a month anywhere.

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

**This is what `render.yaml` is now configured for.** Render Starter plus a 1 GB
disk mounted at `/data`, with `DATA_DIR=/data`. Accounts, projects, proposals
and uploaded documents then survive redeploys, and the instance stops sleeping.

A new disk mounts **empty**, which used to mean copying the seed over SSH before
the site had anybody in it. That step is gone: on boot the app detects an empty
`DATA_DIR` and copies the baked roster onto it. Two properties make that safe to
run every time —

- It only ever fills a gap. A volume that already holds faculty is untouched,
  and a volume holding accounts but no faculty gets the roster merged in rather
  than the file replaced, so nobody loses a login.
- It copies **reference data only** (`faculty`, `papers`, `scholar_papers`,
  `faculty_overrides`). Account tables are created empty by `_init_profiles_db`
  and never travel inside an image — a baked seed is usually built from a
  developer's local database, and copying every table would put their test
  accounts on the production volume.

Custom domains are **free on Render**, TLS included; add one under Settings →
Custom Domains. The only cost is the name itself from a registrar, and a
`depaul.edu` subdomain from campus IT costs nothing.

---

## Option 3 — Railway (recommended; ~$5/mo base plus usage)

`railway.json` in the repo root configures the build and the health check.
Everything else is four settings in the dashboard.

**Why this one.** Railway does not impose a fixed per-service memory ceiling
the way Render and Fly do. A 512MB cap OOM-killed this app twice during
startup, and the real peak is still unmeasured — on Railway an app that spikes
during boot is billed slightly more for that minute rather than killed. It also
deploys automatically on push, which removes the "did the deploy actually run?"
question that cost several rounds of debugging here.

**The tradeoff** is that usage billing is less predictable than a fixed monthly
size. This app holds the ONNX model resident, so it will not idle down to
nothing. Watch the first week.

### Setup

1. **New Project → Deploy from GitHub repo.** Railway reads `railway.json` and
   builds the Dockerfile. The first build takes 15–20 minutes: it downloads the
   model, quantizes it, and embeds 18,681 papers.

2. **Add a volume.** Service → Data → Add Volume, mount path **`/data`**.
   Without this the database lives in the container filesystem and every deploy
   wipes accounts — the exact problem being escaped.

3. **Set variables** (Service → Variables):

   | Key | Value |
   |---|---|
   | `DATA_DIR` | `/data` — **must equal the volume mount path** |
   | `CHATBOT_MODEL` | `anthropic/claude-sonnet-5` |
   | `ANTHROPIC_API_KEY` | your key |

4. **Generate a domain.** Settings → Networking → Generate Domain. HTTPS is
   included; no certificate to manage.

### Checking it worked

In the deploy logs, look for:

```
[seed] copied faculty.db -> /data/faculty.db: faculty=1440, papers=18681
Ready — 1440 faculty indexed, peak NNNMB; paper index loads on first use
```

The first line means the volume seeded itself — no data upload step. The second
gives the **real peak memory**, which is the number this project has never had.
Note it down; it settles the sizing question on any host.

Then sign up, and check you are still logged in tomorrow. That is the proof the
volume is real.

### Gotchas

- **`DATA_DIR` must match the volume mount exactly.** If they differ the app
  writes to the container filesystem while the volume sits empty beside it, and
  the data loss happens anyway, silently.
- **The health check needs a long timeout.** `railway.json` sets 300s because
  the first boot loads the model and seeds the volume. The default would fail a
  perfectly healthy start.
- **`$PORT` is assigned by Railway**, not fixed at 8000. The start command in
  `railway.json` uses it.

---

## Option 4 — AWS EC2 (~$0 for year one, then ~$8/mo)

Full walkthrough: [`deploy/aws/README.md`](../deploy/aws/README.md).

Keeps SQLite and the existing image; only the host changes. The image is built
in GitHub Actions and pulled by the instance, because the builder stage needs
several GB and a `t3.micro` has one.

Three things Render did for you that become yours here: **HTTPS** (a script
using Caddy is provided, and it needs a domain — Let's Encrypt will not issue
for an IP), **backups** (a cron script using `sqlite3 .backup`, not `cp`), and
**OS patching**.

Be honest about year two: this saves the ~$7/mo Render charge for twelve
months and then costs about the same, for more maintenance. Check your
account's Free Tier page first — AWS moved newer accounts to a credit
allowance rather than twelve months of `t3.micro`.

---

## Option 5 — Fly.io

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
