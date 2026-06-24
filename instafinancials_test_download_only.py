#!/usr/bin/env python3
"""
brisksum_download_2303172.py
============================
Downloads ONLY the finished BRiskSUM report for OrderID 2303172.
No order is placed and no status polling is done.

Docs: https://api.instafinancials.com/Docs/BRisksum
Endpoint (HTTP GET, auth via the `user-key` header):
  GET /InstaReports/v1/BRiskSummary/OrderID/{OrderID}/DownloadReport

Auth & config (loaded from the .env next to this script via python-dotenv):
  INSTA_API_KEY  - the InstaFinancials user-key (required)

Usage:
  python3 brisksum_download_2303172.py                 # prints JSON to stdout
  python3 brisksum_download_2303172.py --out report.json
"""

import argparse
import json
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("ERROR: the 'requests' package is required. Install it with: "
             "pip install requests")

try:
    from dotenv import load_dotenv, find_dotenv
except ImportError:
    sys.exit("ERROR: python-dotenv is required. Install it with: "
             "pip install python-dotenv")

# Load .env sitting next to this script (fall back to nearest .env up the tree).
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH if os.path.exists(_ENV_PATH) else find_dotenv(usecwd=True))

BASE_URL = "https://api.instafinancials.com"
AUTH_HEADER = "user-key"          # must be exact lowercase; the API is case-sensitive
HTTP_TIMEOUT = 60
ORDER_ID = 2303172                # fixed OrderID for this script


def download_report(order_id):
    """Download the finished report for an OrderID. Returns the parsed JSON."""
    key = os.environ.get("INSTA_API_KEY") or os.environ.get("INSTA_USER_KEY")
    if not key:
        sys.exit("ERROR: INSTA_API_KEY is not set. Add it to the .env file next to this script.")

    url = f"{BASE_URL}/InstaReports/v1/BRiskSummary/OrderID/{order_id}/DownloadReport"
    # requests preserves the header name exactly as 'user-key' (urllib would
    # capitalize it to 'User-key', which the API rejects with HTTP 401).
    headers = {AUTH_HEADER: key, "Accept": "application/json"}
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


def main():
    p = argparse.ArgumentParser(description=f"Download BRiskSUM report for OrderID {ORDER_ID}.")
    p.add_argument("--out", help="Path to write the JSON report (default: print to stdout).")
    args = p.parse_args()

    print(f"Downloading BRiskSUM report for OrderID {ORDER_ID} ...", file=sys.stderr)
    report = download_report(ORDER_ID)

    meta = report.get("MetaInfo", {}) if isinstance(report, dict) else {}
    print(f"  Delivered: {meta.get('DeliveryTimeStamp')} | Input: {meta.get('Input')}",
          file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"Saved report to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
