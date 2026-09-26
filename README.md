# eBay Auto-Renewal Deletion Automation

Automatically finds and deletes eBay Good Till Cancelled (GTC) listings that are scheduled to auto-renew within the next **36 hours** (configurable), preventing unwanted renewal charges.

## How It Works

1. **`GetMyeBaySelling`** — paginates through all your active listings
2. Filters any listing whose `EndTime` falls within the next `RENEWAL_WINDOW_HOURS` (default: 36 h)
3. **`EndItems`** — ends matching listings in batches of 10 (eBay API limit)
4. Prints a concise run report; exits non-zero if any listing failed (cron-alerting friendly)

## Quick Start

### 1. Install dependencies

```bash
chmod +x install.sh install_cron.sh
./install.sh
```

### 2. Add your eBay API credentials

```bash
# .env was created from .env.example by install.sh
nano .env
```

| Variable | Description |
|---|---|
| `EBAY_APP_ID` | Your app's Client ID (from eBay Developer portal) |
| `EBAY_CERT_ID` | Your app's Client Secret |
| `EBAY_DEV_ID` | Your Developer ID |
| `EBAY_USER_TOKEN` | OAuth User Token with `https://api.ebay.com/oauth/api_scope/sell.inventory` scope |

### 3. Test with a dry run

```bash
.venv/bin/python delete_renewing_items.py --dry-run
```

No listings will be ended; the report shows what *would* be deleted.

### 4. Install the daily cron (06:00 local time)

```bash
./install_cron.sh
```

Logs are written to `logs/ebay_deleter.log`. Run `crontab -l` to confirm.

---

## Configuration (``.env``)

| Variable | Default | Description |
|---|---|---|
| `EBAY_ENVIRONMENT` | `production` | `production` or `sandbox` |
| `RENEWAL_WINDOW_HOURS` | `36` | Delete listings expiring within this many hours |
| `DRY_RUN` | `false` | Set `true` to preview without deleting |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

## CLI Flags

```
python delete_renewing_items.py [OPTIONS]

Options:
  --dry-run            Preview only — do not end any listings
  --window-hours N     Override RENEWAL_WINDOW_HOURS
  --env-file FILE      Path to .env file (default: .env)
```

## Files

| File | Purpose |
|---|---|
| `delete_renewing_items.py` | Main script |
| `.env.example` | Credentials template |
| `requirements.txt` | Python dependencies |
| `install.sh` | One-time setup (venv + .env) |
| `install_cron.sh` | Installs / updates the daily cron entry |
| `logs/ebay_deleter.log` | Runtime log (created on first run) |

## Getting eBay API Credentials

1. Log in to the [eBay Developers Program](https://developer.ebay.com/)
2. Create an app → note the **App ID**, **Cert ID**, **Dev ID**
3. Generate a **User Token** with scope `https://api.ebay.com/oauth/api_scope/sell.inventory`
   - Use the [Auth'N'Auth flow](https://developer.ebay.com/api-docs/static/oauth-auth-code-grant-request.html) or the Token Generator in the developer portal

## Cron Schedule

Default: `0 6 * * *` — runs at **06:00 every day**.

To change the time, edit `install_cron.sh` before running it, or update your crontab directly:

```bash
crontab -e
```

## Alerting on Failure

The script exits with code `1` if any listing fails to be ended.
Pair with a cron wrapper or mail-on-error setup to get notified:

```bash
# Example: send email on failure (requires a local MTA)
0 6 * * * /path/to/.venv/bin/python /path/to/delete_renewing_items.py || mail -s "eBay deleter FAILED" you@example.com
```
