#!/usr/bin/env bash
# Move the database from Neon to Postgres on the VPS.
#
# Run ON THE VPS, with NEON_URL set to the old connection string and
# DATABASE_URL pointing at the new local database.
#
# Stop writers first. A dump taken while the crawler is mid-sweep is
# consistent (pg_dump uses a snapshot) but will be missing that sweep, and the
# difference is silent.
set -euo pipefail

: "${NEON_URL:?set NEON_URL to the old Neon connection string}"
: "${DATABASE_URL:?set DATABASE_URL to the new local database}"

DUMP="/tmp/tenderradar-neon-$(date -u +%Y%m%d-%H%M).dump"

echo "==> stopping writers"
systemctl stop tenderradar-scheduler.service 2>/dev/null || true
systemctl stop tenderradar-web.service 2>/dev/null || true

echo "==> dumping Neon"
pg_dump --dbname="$NEON_URL" --format=custom --compress=6 --no-owner --no-acl \
        --file="$DUMP"
echo "    $(du -h "$DUMP" | cut -f1) written to $DUMP"

echo "==> restoring locally"
psql --dbname="$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector"
psql --dbname="$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"
# --clean so a re-run is idempotent; --if-exists so the first run is not noisy.
pg_restore --dbname="$DATABASE_URL" --no-owner --no-acl --clean --if-exists "$DUMP"

echo "==> verifying row counts match"
for table in tenders tender_versions raw_documents users user_profiles \
             feedback matches contract_awards tender_embeddings; do
    old=$(psql -tAc "SELECT count(*) FROM $table" "$NEON_URL" 2>/dev/null || echo "n/a")
    new=$(psql -tAc "SELECT count(*) FROM $table" "$DATABASE_URL" 2>/dev/null || echo "n/a")
    status="OK"
    [ "$old" != "$new" ] && status="MISMATCH"
    printf '    %-20s neon=%-8s local=%-8s %s\n' "$table" "$old" "$new" "$status"
done

echo
echo "==> checking the vector index survived"
psql --dbname="$DATABASE_URL" -tAc \
  "SELECT count(*) FROM pg_indexes WHERE indexname='tender_embeddings_hnsw_idx'"

cat <<'DONE'

If every row count matches:
  1. Point .env DATABASE_URL at the local database.
  2. systemctl start tenderradar-web tenderradar-scheduler
  3. Run one sweep and confirm it reports 0 new, 0 changed:
       sudo -u tenderradar .venv/bin/python -m tenderradar.cli crawl egp_tender --no-details
     A large "changed" count means the restore altered data -- most likely
     NUMERIC scale or timezone handling -- and should be investigated before
     any digest goes out.

  Keep the Neon project until you have seen a full week run locally. It costs
  nothing on the free tier and is the only rollback you have.

DONE
