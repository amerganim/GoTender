#!/usr/bin/env bash
# Provision a fresh Debian/Ubuntu VPS for TenderRadar.
#
# Idempotent: safe to re-run after a code change. Run as root.
#
# What it does NOT do, on purpose:
#   * obtain TLS certificates (run certbot yourself, it needs DNS to resolve)
#   * write .env (it holds secrets; copy it in by hand)
#   * start the crawler (start it once you have confirmed the migration ran)
set -euo pipefail

APP_USER=tenderradar
APP_DIR=/opt/tenderradar
REPO="${REPO:-https://github.com/amerganim/GoTender.git}"

echo "==> packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev build-essential git curl \
    postgresql postgresql-contrib postgresql-server-dev-all \
    nginx certbot python3-certbot-nginx ca-certificates

echo "==> application user"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

echo "==> code"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    git clone "$REPO" "$APP_DIR"
fi

echo "==> pgvector (needed by Phase 2 matching)"
# Debian ships it for recent Postgres; build from source only if absent.
if ! apt-get install -y postgresql-"$(psql --version | grep -oE '[0-9]+' | head -1)"-pgvector 2>/dev/null; then
    tmp=$(mktemp -d)
    git clone --branch v0.8.0 --depth 1 https://github.com/pgvector/pgvector.git "$tmp"
    make -C "$tmp" && make -C "$tmp" install
    rm -rf "$tmp"
fi

echo "==> database"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$APP_USER'" | grep -q 1 || \
    sudo -u postgres createuser "$APP_USER"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='tenderradar'" | grep -q 1 || \
    sudo -u postgres createdb -O "$APP_USER" tenderradar
sudo -u postgres psql -d tenderradar -c "CREATE EXTENSION IF NOT EXISTS vector"
sudo -u postgres psql -d tenderradar -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"

echo "==> python environment"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR"

echo "==> directories"
# storage/ holds the append-only raw archive (§8.4); .cache holds the ~1GB
# embedding model, which must be writable or the first match run fails.
mkdir -p "$APP_DIR/storage/raw" "$APP_DIR/storage/outbox" "$APP_DIR/backups" "$APP_DIR/.cache"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

if [ ! -f "$APP_DIR/.env" ]; then
    echo
    echo "!! $APP_DIR/.env does not exist. Copy it in, then re-run:"
    echo "     cp .env.example .env && \$EDITOR .env"
    echo "   It must set DATABASE_URL, ALERT_TOKEN_SECRET, VAPID keys and SITE_BASE_URL."
    exit 1
fi
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

echo "==> migrations"
sudo -u "$APP_USER" env $(grep -v '^#' "$APP_DIR/.env" | xargs -d '\n') \
    "$APP_DIR/.venv/bin/python" -m tenderradar.db.migrate

echo "==> pre-download the embedding model"
# Otherwise the first daily run pays a 1GB download while holding the pipeline,
# and a slow link can time it out.
sudo -u "$APP_USER" env HOME="$APP_DIR" $(grep -v '^#' "$APP_DIR/.env" | xargs -d '\n') \
    "$APP_DIR/.venv/bin/python" -c \
    "from tenderradar.matching.embeddings import get_model; get_model(); print('model cached')"

echo "==> systemd"
install -m 644 "$APP_DIR"/deploy/systemd/*.service "$APP_DIR"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tenderradar-web.service
systemctl enable --now tenderradar-daily.timer tenderradar-awards.timer tenderradar-backup.timer

echo "==> nginx"
install -m 644 "$APP_DIR/deploy/nginx-tenderradar.conf" /etc/nginx/sites-available/tenderradar
ln -sf /etc/nginx/sites-available/tenderradar /etc/nginx/sites-enabled/tenderradar
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

cat <<'DONE'

Installed. Two steps left, both deliberate:

  1. TLS — push notifications need a secure context, so do this before
     announcing the site:
       certbot --nginx -d your-domain

  2. Start the crawler once you have confirmed the schema is right:
       systemctl enable --now tenderradar-scheduler

     That unit IS the 30-minute freshness claim and the start of Gate 0's
     72-hour clock. Watch the first sweep finish before walking away:
       journalctl -u tenderradar-scheduler -f

DONE
