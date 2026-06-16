"""
InstaFinancials API – InstaDocs test script
Docs: https://api.instafinancials.com/Docs/InstaDocs

Tests:
    1. OrderReport    – GET /InstaReports/v1/InstaDocs/CompanyCIN/{CIN}/OrderReport
    2. DownloadReport – GET /InstaReports/v1/InstaDocs/OrderID/{OrderID}/DownloadReport

Setup:
    pip install requests python-dotenv
    Set INSTA_API_KEY and INSTA_TEST_CIN in .env
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
CIN      = os.getenv("INSTA_TEST_CIN")

ORDER_ENDPOINT    = "/InstaReports/v1/InstaDocs/CompanyCIN/{CompanyCIN}/OrderReport"
DOWNLOAD_ENDPOINT = "/InstaReports/v1/InstaDocs/OrderID/{OrderID}/DownloadReport"

BRISK_ENDPOINT = "/InstaReports/v1/BRiskSummary/CompanyCIN/{CompanyCIN}/OrderReport"
BRISK_DOWNLOAD_ENDPOINT = "/InstaReports/v1/BRiskSummary/CompanyCIN/{CompanyCIN}/DownloadReport"

HEADERS = {"user-key": API_KEY}


def get(url):
    print(f"\nRequesting (GET): {url}")
    response = requests.get(url, headers=HEADERS, timeout=120)
    print(f"HTTP {response.status_code}")

    if not response.ok:
        print("Request failed. Response body:")
        print(response.text[:800])
        return None

    return response.json()


def print_instadocs_summary(data):
    """Print MetaInfo and a short summary of documents instead of the full (huge) payload."""
    meta = data.get("MetaInfo", {})
    print("\n--- MetaInfo ---")
    print(json.dumps(meta, indent=2, ensure_ascii=False))

    docs = (data.get("ReportData", {}).get("InstaDocs", {}) or {}).get("Document") or []
    print(f"\n--- Documents: {len(docs)} total ---")
    for doc in docs[:5]:
        print(f"  [{doc.get('DocumentCategory')}] {doc.get('DocumentName')} "
              f"(filed: {doc.get('DocumentFillingDate')}, {doc.get('DocumentSize')} MB)")
    if len(docs) > 5:
        print(f"  ... and {len(docs) - 5} more")


def test_order_report():
    """Test 1: Order InstaDocs report by CIN. Returns OrderID for the download test."""
    url = BASE_URL + ORDER_ENDPOINT.format(CompanyCIN=CIN)
    data = get(url)
    if data is None:
        return None

    print_instadocs_summary(data)

    order_id = data.get("MetaInfo", {}).get("OrderID")
    print(f"\nOrderID: {order_id}")
    return order_id


def test_download_report(order_id):
    """Test 2: Download InstaDocs report by OrderID."""
    url = BASE_URL + DOWNLOAD_ENDPOINT.format(OrderID=order_id)
    data = get(url)
    if data is None:
        return

    print_instadocs_summary(data)


def test_brisk_summary():
    url = BASE_URL + BRISK_ENDPOINT.format(CompanyCIN=CIN)
    data = get(url)
    if data is None:
        return
    print("\n--- Raw response ---")
    print(json.dumps(data, indent=2, ensure_ascii=False))

def test_brisk_download():
    url = BASE_URL + BRISK_DOWNLOAD_ENDPOINT.format(CompanyCIN=CIN)
    data = get(url)
    if data is None:
        return
    print("\n--- Raw response ---")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    if not CIN:
        sys.exit("ERROR: INSTA_TEST_CIN is not set (add it to .env).")

    print("=" * 60)
    print("Test 1: InstaDocs OrderReport")
    print("=" * 60)
    order_id = test_order_report()

    if order_id:
        print("\n" + "=" * 60)
        print("Test 2: InstaDocs DownloadReport")
        print("=" * 60)
        test_download_report(order_id)
    else:
        print("\nSkipping DownloadReport test (no OrderID from OrderReport).")

    test_brisk_summary()  
    test_brisk_download() 


if __name__ == "__main__":
    main()
