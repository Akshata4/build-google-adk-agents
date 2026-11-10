#!/usr/bin/env bash
# Sets up the local Postgres store database for the e-commerce agent.
# Reads DB_USER / DB_PASS (and optional DB_NAME) from ../../.env, creates that
# role and database if missing, then loads the schema, the catalog and the demo
# order. Safe to re-run: it resets everything to the sample data.
# Usage: ./setup_local.sh [database-name]   (tests use a separate database)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../../.env"

# Read only the DB_* keys; other .env values may still be <placeholders>.
read_env() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']|["'\'']$//g' || true; }
DB_USER="${DB_USER:-$(read_env DB_USER)}"
DB_PASS="${DB_PASS:-$(read_env DB_PASS)}"
DB_NAME="${1:-${DB_NAME:-$(read_env DB_NAME)}}"
DB_NAME="${DB_NAME:-sportsstore}"
: "${DB_USER:?DB_USER missing in .env}"
: "${DB_PASS:?DB_PASS missing in .env}"
[[ "$DB_NAME" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "Invalid database name: $DB_NAME" >&2; exit 1; }

# Connect as the OS user (Homebrew's default superuser) for admin work.
ADMIN_PSQL=(psql -d postgres -v ON_ERROR_STOP=1 -qAt)

if [ "$("${ADMIN_PSQL[@]}" -c "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")" = "1" ]; then
  echo "Role $DB_USER exists"
else
  echo "Creating role $DB_USER"
  "${ADMIN_PSQL[@]}" -v user="$DB_USER" -v pass="$DB_PASS" <<'SQL'
CREATE ROLE :"user" WITH LOGIN PASSWORD :'pass';
SQL
fi

if [ "$("${ADMIN_PSQL[@]}" -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" = "1" ]; then
  echo "Database $DB_NAME exists"
else
  echo "Creating database $DB_NAME"
  "${ADMIN_PSQL[@]}" -c "CREATE DATABASE $DB_NAME OWNER \"$DB_USER\""
fi
# The extensions need a superuser; create them as the admin.
for ext in pg_trgm cube earthdistance; do
  PGOPTIONS="--client-min-messages=warning" psql -d "$DB_NAME" -v ON_ERROR_STOP=1 -qAt \
    -c "CREATE EXTENSION IF NOT EXISTS $ext" >/dev/null
done

echo "Loading schema, catalog and demo order into $DB_NAME"
for f in init.sql seed.sql demo.sql; do
  PGOPTIONS="--client-min-messages=warning" PGPASSWORD="$DB_PASS" \
    psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q \
    -f "$SCRIPT_DIR/$f" >/dev/null
done

PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -qAt -F ' ' -c \
  "SELECT 'Done:', (SELECT count(*) FROM products), 'products,',
          (SELECT count(*) FROM product_variants), 'variants,',
          (SELECT count(*) FROM stores), 'stores,',
          (SELECT count(*) FROM customers), 'customers'"
