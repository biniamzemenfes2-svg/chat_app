#!/usr/bin/env bash
# Browser-level end-to-end test: starts the real server, then drives the real
# page JavaScript (conversation.js, dashboard.js, realtime.js) in two simulated
# browsers (jsdom) that chat with each other.
#
#   export TEST_DATABASE_URL=postgresql://user:pass@localhost/chatapp_test
#   tests/browser/run.sh
#
# Needs Node 18+ and the project's Python virtualenv to be active.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to a throwaway database}"
PORT="${PORT:-5058}"
export DATABASE_URL="$TEST_DATABASE_URL" FLASK_CONFIG=development FLASK_DEBUG=0 PORT
export SECRET_KEY="${SECRET_KEY:-browser-test-only-$(python -c 'import secrets; print(secrets.token_hex(16))')}"
export UPLOAD_FOLDER="$(mktemp -d)" PYTHONPATH="$PWD"

[ -d tests/browser/node_modules ] || (cd tests/browser && npm install --silent)

flask --app run.py db upgrade >/dev/null
SETUP="$(python tests/browser/setup_e2e.py)"

python run.py >/tmp/chat-browser-test-server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break
  sleep 0.5
done

cd tests/browser
BASE="http://127.0.0.1:$PORT" node e2e.js "$SETUP"
