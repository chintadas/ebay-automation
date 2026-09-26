#!/usr/bin/env bash
# install.sh — one-time setup for the eBay auto-renewal deletion cron
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating virtual environment ..."
python3 -m venv .venv

echo "==> Installing dependencies ..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> Checking for .env ..."
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "    Created .env from .env.example — fill in your API credentials before running."
else
    echo "    .env already exists."
fi

echo ""
echo "Setup complete! Next steps:"
echo "  1. Edit .env and add your eBay API credentials"
echo "  2. Test with a dry run:"
echo "       $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/delete_renewing_items.py --dry-run"
echo "  3. Install the cron (runs daily at 6 AM local time):"
echo "       $SCRIPT_DIR/install_cron.sh"
