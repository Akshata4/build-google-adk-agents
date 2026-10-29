#!/usr/bin/env bash
# Sets up the local Postgres ticket database for the bug assistant.
# Reads DB_USER / DB_PASS from ../../.env, creates that role and the ticketsdb
# database if missing, then loads init.sql. Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../../.env"
DB_NAME="ticketsdb"

# Read only the DB_* keys; other .env values may still be <placeholders>.
read_env() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']|["'\'']$//g'; }
DB_USER="$(read_env DB_USER)"
DB_PASS="$(read_env DB_PASS)"
: "${DB_USER:?DB_USER missing in .env}"
: "${DB_PASS:?DB_PASS missing in .env}"

# Connect as the OS user (Homebrew's default superuser) for admin work.
ADMIN_PSQL=(psql -d postgres -v ON_ERROR_STOP=1 -qAt)

if [ "$("${ADMIN_PSQL[@]}" -c "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")" = "1" ]; then
  echo "Role $DB_USER exists, updating password"
  "${ADMIN_PSQL[@]}" -v user="$DB_USER" -v pass="$DB_PASS" <<'SQL'
ALTER ROLE :"user" WITH LOGIN PASSWORD :'pass';
SQL
else
  echo "Creating role $DB_USER"
  "${ADMIN_PSQL[@]}" -v user="$DB_USER" -v pass="$DB_PASS" <<'SQL'
CREATE ROLE :"user" WITH LOGIN SUPERUSER PASSWORD :'pass';
SQL
fi

if [ "$("${ADMIN_PSQL[@]}" -c "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" = "1" ]; then
  echo "Database $DB_NAME exists"
else
  echo "Creating database $DB_NAME"
  "${ADMIN_PSQL[@]}" -c "CREATE DATABASE $DB_NAME OWNER \"$DB_USER\""
fi

echo "Loading schema and sample data"
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q -f "$SCRIPT_DIR/init.sql"

echo "Done. Tickets loaded:"
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" -qAt -c "SELECT count(*) FROM tickets"
