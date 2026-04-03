#!/usr/bin/env bash
# run.sh — Starts the bot if not already running. Designed to be called by cron.
# Cron example: * * * * * /home/user/bot-financeiro/run.sh >> /home/user/bot-financeiro/logs/cron.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/data/bot.pid"
LOG_FILE="$SCRIPT_DIR/logs/bot.log"
PYTHON="${PYTHON:-python3}"
BOT_SCRIPT="$SCRIPT_DIR/bot.py"

# ---------------------------------------------------------------------------
# PID-based lock: check if existing process is still alive
# ---------------------------------------------------------------------------

if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') Bot already running (PID $OLD_PID). Exiting."
        exit 0
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') Stale PID $OLD_PID found. Removing lock and restarting."
        rm -f "$PID_FILE"
    fi
fi

# ---------------------------------------------------------------------------
# Start the bot in the background
# ---------------------------------------------------------------------------

cd "$SCRIPT_DIR"

# Activate virtualenv if it exists
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting bot…"
nohup "$PYTHON" "$BOT_SCRIPT" >> "$LOG_FILE" 2>&1 &

BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') Bot started with PID $BOT_PID."
