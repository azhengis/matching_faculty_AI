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

The app starts with an empty database and will build its own schema, but with
no faculty in it, matching returns nothing. Copy the local one up:

```bash
fly ssh console -C "mkdir -p /data"
fly sftp shell
  put faculty.db /data/faculty.db
```

The embedding indexes rebuild themselves on first boot (~2 minutes for 1,389
faculty, longer for 18,665 papers). To skip that wait, upload them too:

```bash
fly sftp shell
  put faculty_index.pkl /data/faculty_index.pkl
  put paper_index.pkl /data/paper_index.pkl
```

**Before uploading, decide what should be in the seeded database.** The local
copy contains your own test accounts and proposals. To ship faculty data only:

```bash
sqlite3 faculty.db ".backup /tmp/seed.db"
sqlite3 /tmp/seed.db "DELETE FROM users; DELETE FROM auth_sessions; \
  DELETE FROM profiles; DELETE FROM projects; DELETE FROM proposals; \
  DELETE FROM project_matches; DELETE FROM profile_documents; \
  DELETE FROM faculty_overrides; VACUUM;"
```

Then upload `/tmp/seed.db` as `/data/faculty.db`.

### 5. Check it

```bash
fly logs                        # look for "Loading SPECTER2 model..." then Uvicorn running
fly open
```

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
