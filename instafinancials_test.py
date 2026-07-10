#!/usr/bin/env python3
"""
brisksum_client.py
==================
Client for the InstaFinancials **Business Risk Summary (BRiskSUM)** product.

Docs: 
- https://api.instafinancials.com/Docs/BRisksum
- https://api.instafinancials.com/Docs/SandBoxinputs

BRiskSUM is an *asynchronous* report. The workflow is a three-step chain:

  1. OrderReport   -> place an order for a company CIN; returns an OrderID.
  2. GetStatus     -> poll with the OrderID until the report is ready.
  3. DownloadReport-> fetch the finished JSON report using the OrderID.

Endpoints (all HTTP GET, auth via the `user-key` header):
  GET /InstaReports/v1/BRiskSummary/CompanyCIN/{CIN}/OrderReport
  GET /InstaReports/v1/BRiskSummary/OrderID/{OrderID}/GetStatus
  GET /InstaReports/v1/BRiskSummary/OrderID/{OrderID}/DownloadReport

Auth & config:
  Loaded from the .env file next to this script (via python-dotenv):
    INSTA_API_KEY   - the InstaFinancials user-key (required)
    INSTA_TEST_CIN  - optional default CIN used when --cin is omitted
  NEVER hard-code the key.

Usage:
  # Full chain: order -> poll -> download, saved to a file
  python3 brisksum_client.py --cin L23201MH1959GOI011388 --out report.json

  # Use the default CIN from .env (INSTA_TEST_CIN)
  python3 brisksum_client.py --out report.json

  # Re-use an existing OrderID (skip placing a new, billable order)
  python3 brisksum_client.py --order-id 2303172 --out report.json

  # Just check status of an existing order
  python3 brisksum_client.py --order-id 2303172 --status-only

  # Offline test against a saved DownloadReport sample (no network / key)
  python3 brisksum_client.py --mock sample.json --out report.json
"""

import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("ERROR: the 'requests' package is required. Install it with: "
             "pip install requests")

# Load environment variables from the .env file that sits next to this script
# (falls back to the nearest .env up from the current directory), so
# INSTA_API_KEY and INSTA_TEST_CIN are available without exporting them manually.
try:
    from dotenv import load_dotenv, find_dotenv
except ImportError:
    sys.exit("ERROR: python-dotenv is required. Install it with: "
             "pip install python-dotenv")

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    load_dotenv(_ENV_PATH)
else:
    load_dotenv(find_dotenv(usecwd=True))

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
BASE_URL = "https://api.instafinancials.com"
AUTH_HEADER = "user-key"

# Status values (from GetStatus / OrderReport "OrderStatus") that mean the
# report is finished and can be downloaded. Compared case-insensitively.
READY_STATUSES = {"order delivered", "delivered", "completed", "report ready", "success"}

# Status values that mean the order failed / will never deliver.
FAILED_STATUSES = {"order failed", "failed", "rejected", "error", "data not available"}

DEFAULT_POLL_INTERVAL = 10   # seconds between status checks
DEFAULT_POLL_TIMEOUT = 30000   # give up after this many seconds
HTTP_TIMEOUT = 60            # per-request timeout


# ----------------------------------------------------------------------------
# HTTP helper
# ----------------------------------------------------------------------------
def _api_key():
    key = os.environ.get("INSTA_API_KEY") or os.environ.get("INSTA_USER_KEY")
    if not key:
        sys.exit("ERROR: INSTA_API_KEY is not set. Add it to the .env file next to this script.")
    return key


def _get(path):
    """GET {BASE_URL}{path} with the user-key header. Returns parsed JSON.

    NOTE: we use `requests` rather than urllib because urllib silently rewrites
    the header name to 'User-key', and the InstaFinancials API matches the
    header name case-sensitively (it only accepts the exact lowercase
    'user-key'). requests preserves the header name exactly as given.
    """
    url = f"{BASE_URL}{path}"
    headers = {AUTH_HEADER: _api_key(), "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        sys.exit(f"ERROR: could not reach {url}: {e}")

    if resp.status_code != 200:
        sys.exit(f"ERROR: HTTP {resp.status_code} from {url}\n{resp.text}")

    try:
        return resp.json()
    except ValueError:
        sys.exit(f"ERROR: non-JSON response from {url}:\n{resp.text[:500]}")


# ----------------------------------------------------------------------------
# Endpoint wrappers
# ----------------------------------------------------------------------------
def order_report(cin):
    """Step 1 — place a BRiskSUM order for a CIN. Returns the response dict."""
    return _get(f"/InstaReports/v1/BRiskSummary/CompanyCIN/{cin}/OrderReport")


def get_status(order_id):
    """Step 2 — check the status of an order. Returns the response dict."""
    return _get(f"/InstaReports/v1/BRiskSummary/OrderID/{order_id}/GetStatus")


def download_report(order_id):
    """Step 3 — download the finished report. Returns the full report dict."""
    return _get(f"/InstaReports/v1/BRiskSummary/OrderID/{order_id}/DownloadReport")


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def _status_text(resp):
    return str(resp.get("OrderStatus", "")).strip()


def poll_until_ready(order_id, interval, timeout):
    """Poll GetStatus until the report is ready, failed, or we time out."""
    deadline = time.time() + timeout
    while True:
        resp = get_status(order_id)
        status = _status_text(resp)
        print(f"  [status] OrderID {order_id}: {status or '<unknown>'}", file=sys.stderr)

        low = status.lower()
        if low in READY_STATUSES:
            return resp
        if low in FAILED_STATUSES:
            sys.exit(f"ERROR: order {order_id} failed with status '{status}'. "
                     f"Remarks: {resp.get('OrderRemarks')}")

        if time.time() >= deadline:
            sys.exit(f"ERROR: timed out after {timeout}s waiting for order {order_id} "
                     f"(last status: '{status}').")

        time.sleep(interval)


def run_chain(cin=None, order_id=None, interval=DEFAULT_POLL_INTERVAL,
              timeout=DEFAULT_POLL_TIMEOUT):
    """Run the order -> poll -> download chain. Returns the report dict."""
    if order_id is None:
        if not cin:
            sys.exit("ERROR: provide either --cin or --order-id (or set INSTA_TEST_CIN in .env).")
        print(f"Placing BRiskSUM order for CIN {cin} ...", file=sys.stderr)
        placed = order_report(cin)
        order_id = placed.get("OrderID")
        print(f"  OrderID: {order_id} | {_status_text(placed)} | "
              f"{placed.get('OrderRemarks')}", file=sys.stderr)
        if not order_id:
            sys.exit(f"ERROR: OrderReport did not return an OrderID:\n{json.dumps(placed, indent=2)}")
    else:
        print(f"Re-using existing OrderID {order_id} ...", file=sys.stderr)

    print(f"Polling status (every {interval}s, up to {timeout}s) ...", file=sys.stderr)
    poll_until_ready(order_id, interval, timeout)

    print(f"Downloading report for OrderID {order_id} ...", file=sys.stderr)
    return download_report(order_id)


# ----------------------------------------------------------------------------
# Small convenience summary of a downloaded report
# ----------------------------------------------------------------------------
def summarize(report):
    meta = report.get("MetaInfo", {}) if isinstance(report, dict) else {}
    rd = report.get("ReportData", {}) if isinstance(report, dict) else {}
    print("\n--- Report summary ---", file=sys.stderr)
    print(f"  OrderID    : {meta.get('OrderID')}", file=sys.stderr)
    print(f"  Product    : {meta.get('InstaProduct')}", file=sys.stderr)
    print(f"  Input (CIN): {meta.get('Input')}", file=sys.stderr)
    print(f"  Delivered  : {meta.get('DeliveryTimeStamp')}", file=sys.stderr)
    if isinstance(rd, dict):
        print(f"  Sections   : {', '.join(rd.keys())}", file=sys.stderr)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="InstaFinancials BRiskSUM report client.")
    p.add_argument("--cin", default=os.environ.get("INSTA_TEST_CIN"),
                   help="Company CIN to order a report for (default: INSTA_TEST_CIN from .env).")
    p.add_argument("--order-id", type=int, help="Existing OrderID (skip placing a new order).")
    p.add_argument("--out", help="Path to write the downloaded JSON report.")
    p.add_argument("--status-only", action="store_true",
                   help="Only check status of --order-id and exit.")
    p.add_argument("--mock", help="Load a saved DownloadReport JSON instead of calling the API.")
    p.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL,
                   help=f"Seconds between status polls (default {DEFAULT_POLL_INTERVAL}).")
    p.add_argument("--timeout", type=int, default=DEFAULT_POLL_TIMEOUT,
                   help=f"Max seconds to wait for the report (default {DEFAULT_POLL_TIMEOUT}).")
    args = p.parse_args()

    # Offline mock mode
    if args.mock:
        with open(args.mock, encoding="utf-8") as f:
            report = json.load(f)
        summarize(report)
        report_out = report
    elif args.status_only:
        if not args.order_id:
            sys.exit("ERROR: --status-only requires --order-id.")
        resp = get_status(args.order_id)
        print(json.dumps(resp, indent=2))
        return
    else:
        report_out = run_chain(
            cin=args.cin, order_id=args.order_id,
            interval=args.interval, timeout=args.timeout,
        )
        summarize(report_out)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report_out, f, indent=2, ensure_ascii=False)
        print(f"\nSaved report to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(report_out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
