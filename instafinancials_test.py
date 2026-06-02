"""
InstaFinancials API – Sandbox test script
Fetches financial details for a sample company (e.g. Hyundai Motor India).

Setup:
    pip install requests
    Set your sandbox API key below (or via env var INSTA_API_KEY).

NOTE: InstaFinancials uses per-product endpoints keyed by a company's CIN.
Confirm the exact BASE_URL, ENDPOINT path and AUTH header name against your
API Users Dashboard / sandbox docs, then adjust the CONFIG block if needed.
"""

import os
import sys
import json
import requests

# ---------------- CONFIG (adjust to match your sandbox dashboard) ----------------
API_KEY     = os.getenv("INSTA_API_KEY", "fm3IoYaPDjL2U09KYZlsdo0zp6EGUHUe30cBFTGs2TGN8jnG1wm8dQ==")
BASE_URL    = "https://api.instafinancials.com"

# Auth header name as shown in your dashboard.
AUTH_HEADER = "ApiKey"

# Product endpoint. InstaFinancials pattern: /{Product}/V1/json/CompanyCIN/{cin}/all
# Products: InstaBasic, InstaSummary, InstaFinancials, etc.
PRODUCT     = "InstaSummary"
ENDPOINT    = "/{product}/V1/json/CompanyCIN/{cin}/all"

# Sample company for the sandbox test.
# Replace with the sample CIN your sandbox provides if different.
COMPANY_NAME = "MARUTI OXYGEN PRIVATE LIMITED"
SAMPLE_CIN   = "U24111HR2000PTC034588"   
#testing: U40108TG2010PTC070806
# ---------------------------------------------------------------------------------


def fetch_financials(cin: str) -> dict:
    url = BASE_URL + ENDPOINT.format(product=PRODUCT, cin=cin)
    headers = {
        AUTH_HEADER: API_KEY,
        "Accept": "application/json",
    }
    print(f"Requesting: {url}")
    resp = requests.get(url, headers=headers, timeout=30)
    print(f"HTTP {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def main():
    if API_KEY == "PASTE_YOUR_SANDBOX_KEY_HERE":
        sys.exit("Set your sandbox key in API_KEY or env var INSTA_API_KEY.")

    print(f"--- Credit check (sandbox) : {COMPANY_NAME} ---")
    try:
        data = fetch_financials(SAMPLE_CIN)
    except requests.HTTPError as e:
        print("Request failed:", e)
        print("Response body:", e.response.text[:1000])
        return
    except requests.RequestException as e:
        print("Network error:", e)
        return

    # Pretty-print full payload
    print("\n--- Raw response ---")
    print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])

    # Best-effort summary (key names vary by product/version)
    print("\n--- Quick summary ---")
    for k in ("CompanyName", "Networth", "Revenue", "PAT", "PBT",
              "CreditRating", "Expenditure"):
        # search shallow + one level deep
        if k in data:
            print(f"{k}: {data[k]}")
        else:
            for v in data.values():
                if isinstance(v, dict) and k in v:
                    print(f"{k}: {v[k]}")
                    break


if __name__ == "__main__":
    main()
