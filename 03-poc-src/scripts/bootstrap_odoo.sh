# Odoo POC bootstrap — creates the poc_test DB with demo data and documents
# the per-user API key steps that must be done in the Odoo UI (Odoo has no
# supported API to generate API keys non-interactively).
#
# Usage (from 03-poc-src):
#   bash scripts/bootstrap_odoo.sh          # full bootstrap
#   bash scripts/bootstrap_odoo.sh --no-demo
# Followed by: python -m poc.db.init

set -euo pipefail
cd "$(dirname "$0")/.."

WITH_DEMO="--without-demo=1"
if [ "${1:-}" != "--no-demo" ]; then
  WITH_DEMO="--load-language=en_US"
fi

echo "==> Waiting for Postgres + Odoo containers..."
docker compose up -d
until docker compose exec -T db pg_isready -U odoo >/dev/null 2>&1; do sleep 2; done

if ! docker compose exec -T db psql -U odoo -lqt | cut -d '|' -f 1 | grep -qw poc_test; then
  echo "==> Creating poc_test database (this takes 1-3 minutes)..."
  docker compose exec -T odoo odoo -d poc_test -i sale_management,stock \
    --db_host=db --db_user=odoo --db_password=odoo $WITH_DEMO \
    --max-cron-threads=0 --stop-after-init
else
  echo "==> poc_test already exists, skipping init."
fi

cat <<'EOF'

==> DB ready. Now create the 3 POC users + API keys (UI steps, no CLI path exists):
    1. http://localhost:8069/web/login  (admin / admin)
    2. Settings > Users: create sales_user@test, readonly_user@test,
       no_access_user@test. Give sales_user Sales: Administrator;
       readonly_user Sales read-only; no_access_user no Sales rights.
    3. Each user: Preferences > Account Security > API Keys > New Key ("poc").
    4. Put the 3 keys in .env as ODOO_API_KEY_{SALES_USER,READONLY_USER,NO_ACCESS_USER}.

Then: python -m poc.db.init
EOF
