# Deploying on AWS EC2

Keeps SQLite, keeps the Docker image, keeps everything the app already does.
Only the host changes.

## Why the image is built in CI, not here

The Dockerfile's builder stage downloads a 437 MB model, exports it to ONNX,
quantizes it, and embeds 18,681 papers. That needs several GB of memory. The
instance has one. So `.github/workflows/build-image.yml` builds on a GitHub
runner and pushes to `ghcr.io`, and the instance only ever pulls a finished
image.

This is also why the first deploy is fast: no model download, no index build.

## Setup

**1. Launch the instance.** Amazon Linux 2023, `t3.micro` (2 vCPU, 1 GB).
Security group: allow 22 from your address, 80 and 443 from anywhere. Leave
8000 closed — Caddy will reach it locally.

> 1 GB, not 512 MB, on purpose. A 512 MB container was OOM-killed during
> startup on Render, before its health check ran.

**2. Bootstrap.** Paste `bootstrap.sh` into the User data box at launch, or
run it once over SSH. Installs Docker, creates `/var/lib/faculty-matcher`,
and installs a systemd unit that restarts on failure and on reboot.

**3. Write the env file** — `/opt/faculty-matcher/env`, `chmod 600`:

```
IMAGE=ghcr.io/<user>/<repo>:latest
CHATBOT_MODEL=anthropic/claude-sonnet-5
ANTHROPIC_API_KEY=sk-ant-...
```

**4. Deploy.**

```bash
sudo /opt/faculty-matcher/deploy.sh
```

The app seeds `/var/lib/faculty-matcher` itself on first boot — 1,440 faculty,
18,681 papers, about 13 MB. Nothing to copy in.

**5. HTTPS — do not skip this.**

```bash
sudo ./enable-https.sh matcher.example.edu you@depaul.edu
```

The login form POSTs a password. Over plain HTTP that password, and the
session cookie after it, cross the network in the clear. Render gave you TLS
for free; this is the part you rebuild yourself.

Needs a **domain** — Let's Encrypt will not issue for a raw IP. A `depaul.edu`
subdomain from campus IT works and costs nothing.

**6. Backups.** The volume is yours now, which means losing it is too.

```bash
sudo tee -a /etc/crontab <<< "0 4 * * * root BACKUP_BUCKET=s3://your-bucket/fm /opt/faculty-matcher/backup.sh"
```

Uses `sqlite3 .backup`, not `cp` — copying a file underneath a live writer can
capture a torn page, which restores to a corrupt database.

## Updating

Push to `main`. CI builds and pushes the image, then:

```bash
sudo /opt/faculty-matcher/deploy.sh
```

Not automatic. Adding that means giving CI a key to the instance; worth doing
once the deploy is proven, not before.

## What this actually costs

| | Year 1 | After |
|---|---|---|
| `t3.micro` (1 GB) | **$0** if free-tier eligible | ~$7.50/mo |
| EBS 8 GB | $0 (30 GB free) | ~$0.64/mo |
| S3 backups | ~$0 | pennies |
| **Total** | **~$0** | **~$8/mo** |

> **Be honest about year two.** This saves the ~$7/mo Render charge for twelve
> months and then costs about the same, for more work and a machine you
> maintain. AWS also changed free-tier terms for newer accounts — some now get
> a credit allowance rather than twelve months of `t3.micro`. **Check your
> account's Free Tier page before assuming $0.**
>
> If the goal is purely to stop paying:
> - **Oracle Cloud Always Free** — 24 GB RAM ARM, free permanently, same
>   self-managed tradeoff.
> - **DePaul IT** — a research VM and a `depaul.edu` name, plausibly free, and
>   one email to find out.
> - **Fly.io** — already configured in `fly.toml` at 1 GB for ~$6/mo. Less than
>   Render, twice the memory, no server to maintain.

## What you take on

Render handled these. Now you do:

- **OS patching** — `dnf update` periodically; nothing does it for you
- **Certificate renewal** — Caddy handles it, but if Caddy stops so does HTTPS
- **Backups** — the cron job above, and checking it still runs
- **Monitoring** — nothing tells you the instance is down
- **The disk filling** — 8 GB is plenty, until logs are not rotated

None is hard. All are yours.
