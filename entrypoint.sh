#!/bin/bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

mkdir -p "$HERMES_HOME"/{memories,skills,sessions,cron/output,hooks,logs}

# Config must go to BOTH filenames — CLI and gateway use different loaders
cp /app/hermes/cli-config.yaml "$HERMES_HOME/cli-config.yaml"
cp /app/hermes/cli-config.yaml "$HERMES_HOME/config.yaml"
cp /app/hermes/SOUL.md "$HERMES_HOME/SOUL.md"

# Hermes reads secrets from this file, NOT from system env
cat > "$HERMES_HOME/.env" << EOF
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
TELEGRAM_ALLOWED_USERS=${TELEGRAM_ALLOWED_USERS:-}
TELEGRAM_HOME_CHANNEL=${TELEGRAM_HOME_CHANNEL:-}
SHOP_API_URL=${SHOP_API_URL:-http://localhost:8000}
EOF
chmod 600 "$HERMES_HOME/.env"

# Start the shop backend in the background
python -m uvicorn shop_backend.main:app --host 0.0.0.0 --port 8000 &

# Hermes v0.20.1 pins python-telegram-bot 22.8, which has a confirmed upstream
# regression breaking Telegram connect entirely ("Any cannot be instantiated" -
# NousResearch/hermes-agent#85272). Pinning this at build time in the Dockerfile
# does not stick - something in Hermes's own startup re-resolves it back to
# 22.8 before the gateway ever runs. Re-applying it here, last, right before
# the gateway starts, is what actually survives. Hermes's venv has no bare
# `pip` binary (dependencies are managed via a different installer) - go
# through the venv's own python -m pip instead.
/usr/local/lib/hermes-agent/venv/bin/python3.11 -m pip install --no-cache-dir --quiet 'python-telegram-bot[webhooks]==22.6'

echo "Hermes config ready. Starting gateway..."
exec hermes gateway run
