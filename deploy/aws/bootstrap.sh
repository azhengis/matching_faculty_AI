#!/usr/bin/env bash
# One-time setup for a fresh Amazon Linux 2023 EC2 instance.
#
# Paste this into the "User data" box when launching, or run it once over SSH.
# It is idempotent: running it again is harmless.
#
# What it does NOT do is build the image. That happens in GitHub Actions,
# because the build needs several GB and this instance has one.
set -euo pipefail

APP_DIR=/opt/faculty-matcher
DATA_DIR=/var/lib/faculty-matcher      # survives redeploys; back this up

echo "==> Installing Docker"
dnf install -y docker
systemctl enable --now docker

echo "==> Creating directories"
mkdir -p "$APP_DIR" "$DATA_DIR"

# The container runs as root inside its own namespace; the host directory just
# needs to exist and be writable. Keep it off the root volume's default perms
# so a stray process cannot read proposals.
chmod 700 "$DATA_DIR"

echo "==> Installing the deploy script"
cat > "$APP_DIR/deploy.sh" <<'DEPLOY'
#!/usr/bin/env bash
# Pull the newest image and restart. Safe to run any time.
set -euo pipefail
source /opt/faculty-matcher/env

echo "==> Pulling $IMAGE"
docker pull "$IMAGE"

echo "==> Restarting"
systemctl restart faculty-matcher

echo "==> Waiting for health"
for i in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/login; then
    echo "==> Healthy after ${i}s"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 1
done

echo "!! Did not become healthy in 60s. Recent logs:" >&2
journalctl -u faculty-matcher -n 40 --no-pager >&2
exit 1
DEPLOY
chmod 755 "$APP_DIR/deploy.sh"

echo "==> Installing the service"
cat > /etc/systemd/system/faculty-matcher.service <<'UNIT'
[Unit]
Description=DePaul Faculty Matcher
After=docker.service
Requires=docker.service

[Service]
EnvironmentFile=/opt/faculty-matcher/env
Restart=always
RestartSec=5
TimeoutStartSec=300

ExecStartPre=-/usr/bin/docker rm -f faculty-matcher
ExecStart=/usr/bin/docker run --rm --name faculty-matcher \
  -p 8000:8000 \
  -v /var/lib/faculty-matcher:/data \
  -e DATA_DIR=/data \
  -e CHATBOT_MODEL=${CHATBOT_MODEL} \
  -e ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} \
  -e PYTHONUNBUFFERED=1 \
  ${IMAGE}
ExecStop=/usr/bin/docker stop faculty-matcher

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable faculty-matcher

cat <<'NEXT'

==> Bootstrap complete. Two things left, both by hand:

 1. Write /opt/faculty-matcher/env  (chmod 600 — it holds the API key):

      IMAGE=ghcr.io/<your-github-user>/<repo>:latest
      CHATBOT_MODEL=anthropic/claude-sonnet-5
      ANTHROPIC_API_KEY=sk-ant-...

    If the GitHub package is private, log in once:
      docker login ghcr.io -u <user> -p <a token with read:packages>

 2. Deploy:
      sudo /opt/faculty-matcher/deploy.sh

    The app seeds /var/lib/faculty-matcher itself on first boot — 1,440
    faculty and 18,681 papers, about 13MB. No copying anything in.

NEXT
