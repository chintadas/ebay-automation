#!/usr/bin/env bash
# install_cron.sh — installs (or updates) the daily cron entry
# The job runs every day at 06:00 local time.
# Logs are written to: <project_dir>/logs/ebay_deleter.log
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
MAIN="$SCRIPT_DIR/delete_renewing_items.py"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/ebay_deleter.log"

# ---------- pre-flight checks ----------
if [[ ! -x "$PYTHON" ]]; then
    echo "Error: virtual environment not found. Run ./install.sh first."
    exit 1
fi

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "Error: .env not found. Copy .env.example to .env and fill in credentials."
    exit 1
fi

mkdir -p "$LOG_DIR"

# ---------- build cron line ----------
# Rotate log at 5 MB to keep disk usage reasonable
CRON_LINE="0 6 * * * $PYTHON $MAIN >> $LOG_FILE 2>&1"

MARKER="# ebay-auto-renewal-deleter"

# Remove any previous entry from crontab
EXISTING=$(crontab -l 2>/dev/null || true)
CLEANED=$(echo "$EXISTING" | grep -v "$MARKER" | grep -v "delete_renewing_items" || true)

# Append new entry with marker comment
NEW_CRONTAB="${CLEANED}
$MARKER
$CRON_LINE
"

echo "$NEW_CRONTAB" | crontab -

echo "Cron job installed:"
echo "  Schedule : every day at 06:00 local time"
echo "  Command  : $PYTHON $MAIN"
echo "  Log      : $LOG_FILE"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -l | grep -v 'delete_renewing_items' | grep -v '$MARKER' | crontab -"
