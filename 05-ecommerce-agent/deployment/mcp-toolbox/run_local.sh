#!/usr/bin/env bash
# Starts MCP Toolbox locally against the Homebrew Postgres store database.
# Downloads the toolbox binary on first run. Serves MCP at http://127.0.0.1:5000/mcp
# Extra arguments are passed to toolbox (e.g. --port 5001).
set -euo pipefail

TOOLBOX_VERSION="1.15.0"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../../.env"
cd "$SCRIPT_DIR"

if [ ! -x ./toolbox ]; then
  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64) PLATFORM="darwin/arm64" ;;
    Darwin/x86_64) PLATFORM="darwin/amd64" ;;
    Linux/x86_64) PLATFORM="linux/amd64" ;;
    *) echo "Unsupported platform $(uname -s)/$(uname -m)" >&2; exit 1 ;;
  esac
  echo "Downloading MCP Toolbox v$TOOLBOX_VERSION ($PLATFORM)"
  curl -sSfL -o toolbox \
    "https://storage.googleapis.com/mcp-toolbox-for-databases/v$TOOLBOX_VERSION/$PLATFORM/toolbox"
  chmod +x toolbox
fi

# Read only the DB_* keys; other .env values may still be <placeholders>.
# Values already in the environment win (the tests point at a test database).
read_env() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/^["'\'']|["'\'']$//g' || true; }
export DB_USER="${DB_USER:-$(read_env DB_USER)}"
export DB_PASS="${DB_PASS:-$(read_env DB_PASS)}"
export DB_NAME="${DB_NAME:-$(read_env DB_NAME)}"
export DB_NAME="${DB_NAME:-sportsstore}"

# Only accept requests addressed to this machine (blocks DNS rebinding / other sites).
exec ./toolbox --configs tools.yaml,local.yaml \
  --allowed-hosts 127.0.0.1,localhost \
  --allowed-origins http://127.0.0.1:5000,http://localhost:5000 \
  "$@"
