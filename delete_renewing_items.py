#!/usr/bin/env python3
"""
eBay Auto-Renewal Deletion Script
==================================
Finds all active Good Till Cancelled (GTC) listings that will renew
within the configured window (default: next 36 hours) and ends them
via the eBay Trading API.

Designed to run daily on a cron job.

Usage:
    python delete_renewing_items.py [--dry-run] [--window-hours N]

Environment variables (see .env.example):
    EBAY_APP_ID, EBAY_CERT_ID, EBAY_DEV_ID, EBAY_USER_TOKEN
    EBAY_ENVIRONMENT, RENEWAL_WINDOW_HOURS, DRY_RUN, LOG_LEVEL
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ebay-renew-deleter")

# ---------------------------------------------------------------------------
# Constants & Config
# ---------------------------------------------------------------------------

TRADING_API_ENDPOINTS = {
    "production": "https://api.ebay.com/ws/api.dll",
    "sandbox":    "https://api.sandbox.ebay.com/ws/api.dll",
}

# eBay Trading API XML namespace
NS = "urn:ebay:apis:eBLBaseComponents"

# Max items eBay allows per EndItems call
END_ITEMS_BATCH_SIZE = 10

# Max active listings per GetMyeBaySelling page
LISTINGS_PAGE_SIZE = 200


@dataclass
class Config:
    app_id:               str
    cert_id:              str
    dev_id:               str
    user_token:           str
    environment:          str   = "production"
    renewal_window_hours: float = 36.0
    dry_run:              bool  = False

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        missing: list[str] = []
        for key in ("EBAY_APP_ID", "EBAY_CERT_ID", "EBAY_DEV_ID", "EBAY_USER_TOKEN"):
            if not os.getenv(key):
                missing.append(key)
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Copy .env.example to .env and fill in your credentials."
            )

        return cls(
            app_id=os.environ["EBAY_APP_ID"],
            cert_id=os.environ["EBAY_CERT_ID"],
            dev_id=os.environ["EBAY_DEV_ID"],
            user_token=os.environ["EBAY_USER_TOKEN"],
            environment=os.getenv("EBAY_ENVIRONMENT", "production").lower(),
            renewal_window_hours=float(os.getenv("RENEWAL_WINDOW_HOURS", "36")),
            dry_run=os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes"),
        )


@dataclass
class Listing:
    item_id:   str
    title:     str
    sku:       str | None
    end_time:  datetime
    price:     float
    quantity:  int


@dataclass
class RunReport:
    found:   list[Listing]          = field(default_factory=list)
    ended:   list[str]              = field(default_factory=list)
    failed:  list[tuple[str, str]]  = field(default_factory=list)


# ---------------------------------------------------------------------------
# eBay Trading API Client
# ---------------------------------------------------------------------------

class EbayTradingClient:
    """Thin wrapper around the eBay Trading API (XML over HTTPS)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.endpoint = TRADING_API_ENDPOINTS[config.environment]
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "text/xml;charset=utf-8",
            "X-EBAY-API-CALL-NAME": "",
            "X-EBAY-API-APP-NAME":  config.app_id,
            "X-EBAY-API-CERT-NAME": config.cert_id,
            "X-EBAY-API-DEV-NAME":  config.dev_id,
            "X-EBAY-API-SITEID":    "0",
            "X-EBAY-API-COMPATIBILITY-LEVEL": "1283",
        })

    def _make_envelope(self, call_name: str, body: str) -> str:
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<{call_name}Request xmlns="{NS}">'
            f"<RequesterCredentials><eBayAuthToken>{self.config.user_token}</eBayAuthToken></RequesterCredentials>"
            f"{body}"
            f"</{call_name}Request>"
        )

    def _call(self, call_name: str, body: str, retries: int = 3) -> ET.Element:
        xml_payload = self._make_envelope(call_name, body)
        headers_patch = {"X-EBAY-API-CALL-NAME": call_name}

        for attempt in range(1, retries + 1):
            try:
                resp = self.session.post(
                    self.endpoint,
                    data=xml_payload.encode("utf-8"),
                    headers=headers_patch,
                    timeout=30,
                )
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                self._check_errors(call_name, root)
                return root
            except (requests.RequestException, ET.ParseError) as exc:
                log.warning("Attempt %d/%d for %s failed: %s", attempt, retries, call_name, exc)
                if attempt < retries:
                    time.sleep(2 ** attempt)
                else:
                    raise

    @staticmethod
    def _check_errors(call_name: str, root: ET.Element) -> None:
        ack = root.findtext(f"{{{NS}}}Ack", "")
        if ack not in ("Success", "Warning"):
            errors = root.findall(f".//{{{NS}}}Errors")
            msgs = "; ".join(
                (e.findtext(f"{{{NS}}}LongMessage") or e.findtext(f"{{{NS}}}ShortMessage") or "unknown")
                for e in errors
            )
            raise RuntimeError(f"{call_name} returned Ack={ack!r}: {msgs}")

    @staticmethod
    def _text(element: ET.Element | None, tag: str, default: str = "") -> str:
        if element is None:
            return default
        return element.findtext(f"{{{NS}}}{tag}") or default

    def get_active_listings(self) -> Generator[Listing, None, None]:
        """
        Paginate through all active GTC listings using GetMyeBaySelling.
        Yields Listing objects one by one.
        """
        page = 1
        while True:
            log.debug("Fetching active listings page %d ...", page)
            body = (
                "<ActiveList>"
                f"  <Pagination><EntriesPerPage>{LISTINGS_PAGE_SIZE}</EntriesPerPage>"
                f"             <PageNumber>{page}</PageNumber></Pagination>"
                "</ActiveList>"
            )
            root = self._call("GetMyeBaySelling", body)

            items_node = root.find(f".//{{{NS}}}ActiveList/{{{NS}}}ItemArray")
            if items_node is None:
                break

            items = items_node.findall(f"{{{NS}}}Item")
            if not items:
                break

            for item in items:
                listing_details = item.find(f"{{{NS}}}ListingDetails")
                end_time_str = (
                    self._text(listing_details, "EndTime")
                    if listing_details is not None
                    else self._text(item, "EndTime")
                )

                if not end_time_str:
                    log.debug("No EndTime for item %s, skipping", self._text(item, "ItemID"))
                    continue

                try:
                    end_time = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                except ValueError:
                    log.warning(
                        "Could not parse EndTime %r for item %s",
                        end_time_str,
                        self._text(item, "ItemID"),
                    )
                    continue

                selling_status = item.find(f"{{{NS}}}SellingStatus")
                try:
                    price = float(self._text(selling_status, "CurrentPrice") if selling_status is not None else "0")
                except ValueError:
                    price = 0.0

                try:
                    quantity = int(self._text(item, "Quantity") or "0")
                except ValueError:
                    quantity = 0

                yield Listing(
                    item_id=self._text(item, "ItemID"),
                    title=self._text(item, "Title"),
                    sku=self._text(item, "SKU") or None,
                    end_time=end_time,
                    price=price,
                    quantity=quantity,
                )

            total_pages_str = (
                root.findtext(
                    f".//{{{NS}}}ActiveList/{{{NS}}}PaginationResult/{{{NS}}}TotalNumberOfPages"
                )
                or "1"
            )
            if page >= int(total_pages_str):
                break
            page += 1

    def end_items(self, item_ids: list[str]) -> dict[str, str]:
        """
        End a batch of up to 10 listings.
        Returns dict of {item_id: "Success" | error_message}.
        """
        assert 1 <= len(item_ids) <= END_ITEMS_BATCH_SIZE

        item_xml = "".join(
            f"<EndItemRequestContainer>"
            f"  <MessageID>{i}</MessageID>"
            f"  <ItemID>{iid}</ItemID>"
            f"  <EndingReason>NotAvailable</EndingReason>"
            f"</EndItemRequestContainer>"
            for i, iid in enumerate(item_ids)
        )

        root = self._call("EndItems", item_xml)

        results: dict[str, str] = {}
        for container in root.findall(f".//{{{NS}}}EndItemResponseContainer"):
            msg_id  = container.findtext(f"{{{NS}}}CorrelationID") or ""
            ack     = container.findtext(f"{{{NS}}}Ack") or ""
            errors  = container.findall(f".//{{{NS}}}Errors")
            err_msg = "; ".join(
                (e.findtext(f"{{{NS}}}LongMessage") or e.findtext(f"{{{NS}}}ShortMessage") or "")
                for e in errors
            ) or ""

            try:
                item_id = item_ids[int(msg_id)]
            except (ValueError, IndexError):
                item_id = msg_id

            results[item_id] = ack if ack in ("Success", "Warning") else f"Error: {err_msg}"

        return results


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def find_renewing_soon(client: EbayTradingClient, window_hours: float) -> list[Listing]:
    """Return all listings whose EndTime falls within the next window_hours."""
    now      = datetime.now(tz=timezone.utc)
    cutoff   = now + timedelta(hours=window_hours)
    renewing = []

    log.info(
        "Scanning all active listings for renewals before %s ...",
        cutoff.strftime("%Y-%m-%d %H:%M UTC"),
    )

    for listing in client.get_active_listings():
        if now <= listing.end_time <= cutoff:
            log.debug(
                "  -> %s [%s] ends at %s",
                listing.item_id,
                listing.title[:60],
                listing.end_time.strftime("%Y-%m-%d %H:%M UTC"),
            )
            renewing.append(listing)

    return renewing


def end_renewing_listings(
    client: EbayTradingClient,
    listings: list[Listing],
    dry_run: bool,
) -> RunReport:
    """End all listings in batches; build and return a RunReport."""
    report = RunReport(found=listings)

    if not listings:
        log.info("No listings renewing in the window. Nothing to do.")
        return report

    log.info(
        "%s %d listing(s) that renew soon ...",
        "DRY-RUN -- would end" if dry_run else "Ending",
        len(listings),
    )

    batches = [
        listings[i : i + END_ITEMS_BATCH_SIZE]
        for i in range(0, len(listings), END_ITEMS_BATCH_SIZE)
    ]

    for batch in batches:
        ids = [l.item_id for l in batch]
        if dry_run:
            log.info("  [DRY-RUN] Would end: %s", ", ".join(ids))
            report.ended.extend(ids)
            continue

        try:
            results = client.end_items(ids)
        except Exception as exc:
            log.error("Batch EndItems call failed: %s", exc)
            for item_id in ids:
                report.failed.append((item_id, str(exc)))
            continue

        for item_id, status in results.items():
            if status in ("Success", "Warning"):
                log.info("  Ended %s", item_id)
                report.ended.append(item_id)
            else:
                log.warning("  Failed to end %s -- %s", item_id, status)
                report.failed.append((item_id, status))

    return report


def print_report(report: RunReport, dry_run: bool) -> None:
    """Print a human-readable summary to stdout."""
    sep = "=" * 60
    print(f"\n{sep}")
    print("  eBay Auto-Renewal Deletion -- Run Report")
    print(sep)
    print(f"  Mode    : {'DRY-RUN (no changes made)' if dry_run else 'LIVE'}")
    print(f"  Found   : {len(report.found)} listing(s) renewing soon")
    print(f"  {'Would end' if dry_run else 'Ended':<7} : {len(report.ended)}")
    print(f"  Failed  : {len(report.failed)}")

    if report.found:
        print("\n  Listings identified:")
        for l in report.found:
            print(
                f"    [{l.item_id}] {l.title[:50]:<50}  "
                f"ends {l.end_time.strftime('%Y-%m-%d %H:%M UTC')}  "
                f"${l.price:.2f}  qty={l.quantity}"
            )

    if report.failed:
        print("\n  Failures:")
        for item_id, reason in report.failed:
            print(f"    [{item_id}] {reason}")

    print(f"{sep}\n")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="End eBay GTC listings that would auto-renew tomorrow.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Identify listings but do NOT end them (overrides DRY_RUN env var).",
    )
    parser.add_argument(
        "--window-hours",
        type=float,
        default=None,
        metavar="N",
        help="End listings expiring within the next N hours (overrides RENEWAL_WINDOW_HOURS).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        metavar="FILE",
        help="Path to the .env file to load credentials from.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.env_file and args.env_file.exists():
        load_dotenv(args.env_file, override=True)

    try:
        config = Config.from_env()
    except EnvironmentError as exc:
        log.error("%s", exc)
        return 1

    if args.dry_run is not None:
        config.dry_run = args.dry_run
    if args.window_hours is not None:
        config.renewal_window_hours = args.window_hours

    log.info(
        "Starting eBay auto-renewal deletion  [env=%s | window=%.1fh | dry_run=%s]",
        config.environment,
        config.renewal_window_hours,
        config.dry_run,
    )

    client = EbayTradingClient(config)

    try:
        renewing = find_renewing_soon(client, config.renewal_window_hours)
        report   = end_renewing_listings(client, renewing, config.dry_run)
    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        return 1

    print_report(report, config.dry_run)

    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
