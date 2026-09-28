#!/usr/bin/env bash
# Copy the database off the instance to S3.
#
# On Render the instance was disposable and the disk was managed. Here the
# volume is yours, which means losing it is also yours. This is the part of
# self-hosting that has no equivalent on a platform and is easiest to skip
# until the week it matters.
#
# Run from cron:  0 4 * * *  /opt/faculty-matcher/backup.sh >>/var/log/fm-backup.log 2>&1
set -euo pipefail

DATA=/var/lib/faculty-matcher/faculty.db
BUCKET=${BACKUP_BUCKET:?"set BACKUP_BUCKET=s3://your-bucket/path"}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

[ -f "$DATA" ] || { echo "no database at $DATA"; exit 1; }

# .backup, not cp. SQLite may be mid-write, and copying the file underneath a
# live writer can capture a torn page — a backup that restores to a corrupt
# database, which is worse than no backup because you find out later.
sqlite3 "$DATA" ".backup '$TMP/faculty.db'"
gzip -9 "$TMP/faculty.db"

aws s3 cp "$TMP/faculty.db.gz" "$BUCKET/faculty-$STAMP.db.gz"
echo "$(date -u +%FT%TZ) backed up $(du -h "$TMP/faculty.db.gz" | cut -f1) to $BUCKET/faculty-$STAMP.db.gz"

# Keep 30 days. S3 lifecycle rules would do this too; doing it here keeps the
# whole policy in one readable place.
aws s3 ls "$BUCKET/" | awk '{print $4}' | grep '^faculty-' | sort | head -n -30 \
  | while read -r old; do aws s3 rm "$BUCKET/$old"; done
