"""
InstaFinancials API – InstaSummary test script
Fetches company summary data by CIN.

Setup:
    pip install requests
    Set your API key below (or via env var INSTA_API_KEY).
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
API_KEY  = os.getenv("INSTA_API_KEY") or sys.exit("ERROR: INSTA_API_KEY is not set (add it to .env).")
BASE_URL = "https://api.instafinancials.com"
ENDPOINT = "/InstaReports/v1/BRiskSummary/CompanyCIN/{CompanyCIN}/OrderReport"
CIN      = os.getenv("INSTA_TEST_CIN")
# -----------------------------------------

def main():
    url = BASE_URL + ENDPOINT.format(CompanyCIN=CIN)
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
