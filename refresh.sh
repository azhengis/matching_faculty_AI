#!/usr/bin/env bash
#
# refresh.sh — re-fetch DePaul's faculty directory and rebuild everything from it.
#
# The sequence has several ways to silently produce stale or broken output, and
# every one of them has actually happened:
#
#   Re-running the parser reads the PAGE CACHE, not depaul.edu, so it looks
#   exactly like a refresh while learning nothing new. Only stage 1 --no-cache
#   really re-downloads. This script always passes it.
#
#   Forgetting the reindex leaves new people in the database but unfindable,
#   because search runs on the embedding pickles, not the table.
#
#   Forgetting the seed leaves the LIVE SITE untouched. Render builds from
#   data/seed_faculty.db.gz, which is committed; faculty.db is gitignored.
#
#   Running the reindex alongside anything heavy gets it OOM-killed partway.
#   This script runs it alone and checks it actually finished.
#
# Usage:
#   ./refresh.sh              # full refresh: re-download every page
#   ./refresh.sh --reparse    # skip the download, re-parse cached pages
#                             #   (use after changing the parser)
#   ./refresh.sh --no-papers  # skip stages 6-8 (the ~2h publication fetch)
#
# Safe to interrupt. Stages 1, 6 and 7 resume where they stopped.

set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python3"
[ -x "$PY" ] || PY="python3"

REPARSE=0
PAPERS=1
for arg in "$@"; do
  case "$arg" in
    --reparse)   REPARSE=1 ;;
    --no-papers) PAPERS=0 ;;
    -h|--help)   sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
fail() { printf '\n\033[31mFAILED at: %s\033[0m\n' "$1"; exit 1; }

# A snapshot to compare against, and to restore from if something goes wrong.
BACKUP=".refresh_backup"
mkdir -p "$BACKUP"
[ -f faculty.db ] && cp faculty.db "$BACKUP/faculty.db"
cp data/depaul_faculty_enriched.json "$BACKUP/" 2>/dev/null || true
cp data/depaul_roster_clean.json     "$BACKUP/" 2>/dev/null || true
echo "Backed up faculty.db and the data JSONs to $BACKUP/"

before_count() {
  [ -f "$BACKUP/faculty.db" ] || { echo 0; return; }
  $PY - <<'EOF'
import sqlite3
try:
    print(sqlite3.connect(".refresh_backup/faculty.db").execute(
        "SELECT COUNT(*) FROM faculty").fetchone()[0])
except Exception:
    print(0)
EOF
}
FACULTY_BEFORE=$(before_count)

# ── 1. The directory itself ──────────────────────────────────────────────────
if [ "$REPARSE" -eq 1 ]; then
  step "1/8  Skipping the download (--reparse): using cached pages"
else
  step "1/8  Re-downloading every faculty page from the sitemap (~25 min)"
  $PY pipeline/1_extract_faculty.py --no-cache || fail "stage 1 (directory scrape)"
fi

step "2/8  Parsing bios, research areas, and courses"
$PY pipeline/2_enrich_bios.py || fail "stage 2 (bio parsing)"

# ── 3. OpenAlex fill. Budget-limited, so a stop here is expected, not fatal ──
step "3/8  OpenAlex summaries for faculty whose page has none"
if $PY pipeline/3_enrich_openalex.py; then
  echo "  stage 3 completed"
else
  echo "  stage 3 stopped early (usually the daily OpenAlex budget)."
  echo "  Not fatal: it saved what it got and skips those people next run."
fi

step "4/8  Loading into faculty.db"
$PY pipeline/4_db_setup.py || fail "stage 4 (database load)"

step "5/8  Cleaning scrape artifacts"
$PY pipeline/5_fix_data.py || fail "stage 5 (data cleanup)"

# ── 6-8. Publications ───────────────────────────────────────────────────────
if [ "$PAPERS" -eq 1 ]; then
  step "6/8  Publications via OpenAlex (~1 h, resumable)"
  $PY pipeline/6_fetch_papers.py || fail "stage 6 (OpenAlex papers)"
  step "7/8  Publications via Semantic Scholar and CrossRef (~1 h, resumable)"
  $PY pipeline/7_fetch_papers_s2.py || fail "stage 7 (S2/CrossRef papers)"
  step "8/8  Dropping misattributed papers"
  $PY pipeline/8_clean_papers.py || fail "stage 8 (paper cleanup)"
else
  step "6-8/8  Skipping publications (--no-papers)"
fi

# ── Embeddings. Alone, and verified — a partial build is worse than none ─────
step "Rebuilding the search index (~10 min; nothing else should run now)"
rm -f faculty_index.pkl paper_index.pkl
$PY - <<'EOF' || exit 1
import search as sm
people = sm.load_faculty()
model = sm.load_model()
sm.get_index(people, model)
sm.get_paper_index(people, model)
print(f"index rebuilt for {len(people)} faculty")
EOF
for f in faculty_index.pkl paper_index.pkl; do
  [ -s "$f" ] || fail "index rebuild (missing $f — likely killed for memory; re-run with nothing else running)"
done

# ── The seed is the only thing the live site ever sees ───────────────────────
step "Regenerating the deployment seed"
$PY pipeline/make_seed_db.py --gzip-to data/seed_faculty.db.gz || fail "seed build"

step "Checking the seed for user data and new personal information"
$PY pipeline/check_seed_pii.py || fail "PII gate — do NOT commit this seed"

# ── What changed ────────────────────────────────────────────────────────────
step "Summary"
FACULTY_BEFORE="$FACULTY_BEFORE" $PY pipeline/refresh_summary.py || true

cat <<'EOF'

Done. Nothing is live yet — Render builds from the committed seed:

    git add -A && git commit -m "Refresh faculty data" && git push

The previous database is in .refresh_backup/ if you need to compare or revert.
EOF
