"""
InstaFinancials API – InstaSummary test script
Fetches company summary data by CIN.

Setup:
    pip install requests
    Set your API key below (or via env var INSTA_API_KEY).
"""

import os
import json
import requests

# ---------------- CONFIG ----------------
API_KEY  = os.getenv("INSTA_API_KEY", "fm3IoYaPDjL2U09KYZlsdo0zp6EGUHUe30cBFTGs2TGN8jnG1wm8dQ==")
BASE_URL = "https://instafinancials.com/api"
ENDPOINT = "/InstaSummary/v1/json/CompanyCIN/{cin}"
CIN      = os.getenv("INSTA_TEST_CIN", "L32309KA1954GOI000787")
# -----------------------------------------


def main():
    url = BASE_URL + ENDPOINT.format(cin=CIN)
    headers = {"user-key": API_KEY}

    print(f"Requesting (GET): {url}")
    response = requests.get(url, headers=headers, timeout=60)
    print(f"HTTP {response.status_code}")

    if not response.ok:
        print("Request failed. Response body:")
        print(response.text[:800])
        return

    data = response.json()

    # Pretty-print full payload
    print("\n--- Raw response ---")
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
