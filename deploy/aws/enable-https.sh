#!/usr/bin/env bash
# Put HTTPS in front of the app, with an automatically renewed certificate.
#
# WHY THIS IS NOT OPTIONAL: the login form POSTs a password. Over plain HTTP
# that password crosses the network in the clear, and so does the session
# cookie that follows it — on university wifi, in front of anyone on the same
# network. Render provided TLS for free and this is the part of it you have to
# rebuild yourself on EC2.
#
# REQUIRES A DOMAIN NAME. Let's Encrypt will not issue a certificate for a raw
# IP address, so until the instance has a name pointing at it there is no way
# to serve HTTPS. A depaul.edu subdomain from campus IT works and costs
# nothing; so does any registrar name.
#
#   usage:  sudo ./enable-https.sh matcher.depaul.edu you@depaul.edu
set -euo pipefail

DOMAIN=${1:?"usage: enable-https.sh <domain> <email>"}
EMAIL=${2:?"usage: enable-https.sh <domain> <email>"}

echo "==> Checking that $DOMAIN points here"
MY_IP=$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '\n' || true)
DNS_IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)
if [ -z "$DNS_IP" ]; then
  echo "!! $DOMAIN does not resolve. Add an A record pointing to $MY_IP first." >&2
  exit 1
fi
if [ "$DNS_IP" != "$MY_IP" ]; then
  echo "!! $DOMAIN resolves to $DNS_IP but this instance is $MY_IP." >&2
  echo "   Certificate issuance will fail until that matches." >&2
  exit 1
fi
echo "    $DOMAIN -> $MY_IP, good"

echo "==> Installing Caddy (chosen over nginx+certbot: it obtains and renews"
echo "    the certificate itself, with no cron job to forget)"
dnf install -y 'dnf-command(copr)'
dnf copr enable -y @caddy/caddy
dnf install -y caddy

cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000

    # The app sets its own session cookie; make the browser refuse to send it
    # over anything but HTTPS from here on.
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "DENY"
        Referrer-Policy           "strict-origin-when-cross-origin"
        -Server
    }
}
CADDY

echo "==> Requesting a certificate for $DOMAIN"
sed -i "s/^\(\s*email\).*/\1 $EMAIL/" /etc/caddy/Caddyfile 2>/dev/null || true
systemctl enable --now caddy
systemctl restart caddy

sleep 5
if curl -fsS -o /dev/null "https://$DOMAIN/login"; then
  echo "==> HTTPS is live at https://$DOMAIN"
  echo
  echo "    Now CLOSE PORT 8000 in the security group. The app should only be"
  echo "    reachable through Caddy; leaving 8000 open serves the same site"
  echo "    over plain HTTP and defeats the point of this script."
else
  echo "!! Certificate not serving yet. Check: journalctl -u caddy -n 40" >&2
  exit 1
fi
