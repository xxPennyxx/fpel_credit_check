#!/usr/bin/env python3
"""
fetch_instafinancials.py
========================
Fetches company financial data from the InstaFinancials API and maps it into
the data.json schema consumed by generate_report.py (the RC Credit Check skill).

This is a SCAFFOLD. The endpoint paths and JSON field locations marked with
"# >>> CONFIGURE" must be confirmed against the InstaFinancials API documentation
for the product the team subscribes to (e.g. "Company Financials" / "Comprehensive
Company Report"). Once confirmed, only the values in CONFIG and the field paths in
_map_response() need changing — the rest of the pipeline is unchanged.

Auth:
  Reads the API key from the INSTAFIN_API_KEY environment variable.
  NEVER hard-code the key in this file.

Usage:
  export INSTAFIN_API_KEY="xxxxxxxx"

  # By CIN (preferred — InstaFinancials is keyed on CIN):
  python3 fetch_instafinancials.py --cin U12345MH2009PLC123456 \
      --out /path/to/data_partial.json

  # By name (uses the search endpoint to resolve CIN first):
  python3 fetch_instafinancials.py --name "Acme Industries Pvt Ltd" \
      --out /path/to/data_partial.json

  # Offline test with a saved sample response (no network / no key needed):
  python3 fetch_instafinancials.py --mock sample_response.json \
      --out /path/to/data_partial.json

Output:
  Writes a PARTIAL data.json containing the keys this API can populate
  (company_name, incorporation_date, financials, screening, and a numeric
  helper block `_metrics`). Web search / uploads fill the qualitative sections
  (brief_profile_paragraphs, strengths, weaknesses, latest_updates, credit_view).
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

# ----------------------------------------------------------------------------
# CONFIG  — confirm all of these against the InstaFinancials API docs
# ----------------------------------------------------------------------------
CONFIG = {
    # >>> CONFIGURE: base URL from your InstaFinancials API docs
    "base_url": "https://www.instafinancials.com/api",
    # >>> CONFIGURE: endpoint that returns balance sheet + P&L by CIN
    "financials_path": "/v1/company/financials",      # e.g. /CompanyFinancials/V1/{cin}
    # >>> CONFIGURE: name -> CIN search endpoint (if available on your plan)
    "search_path": "/v1/company/search",
    # >>> CONFIGURE: how the key is passed — header vs query param
    "auth_mode": "header",            # "header" or "query"
    "auth_header_name": "X-API-Key",  # used if auth_mode == "header"
    "auth_query_param": "apikey",     # used if auth_mode == "query"
    "timeout_sec": 30,
}

LAKH_PER_CRORE = 100.0     # 1 crore = 100 lakh
RUPEES_PER_CRORE = 1e7     # 1 crore = 10,000,000


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
def _api_key():
    key = os.environ.get("INSTAFIN_API_KEY")
    if not key:
        sys.exit("ERROR: INSTAFIN_API_KEY environment variable is not set.")
    return key


def _request(path, params):
    """GET helper. Returns parsed JSON dict."""
    key = _api_key()
    query = dict(params or {})
    headers = {"Accept": "application/json"}

    if CONFIG["auth_mode"] == "header":
        headers[CONFIG["auth_header_name"]] = key
    else:
        query[CONFIG["auth_query_param"]] = key

    qs = urllib.parse.urlencode(query)
    url = f"{CONFIG['base_url']}{path}?{qs}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=CONFIG["timeout_sec"]) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: InstaFinancials API returned HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}")
    except urllib.error.URLError as e:
        sys.exit(f"ERROR: could not reach InstaFinancials API: {e.reason}")


def resolve_cin(name):
    """Resolve a company name to a CIN via the search endpoint."""
    data = _request(CONFIG["search_path"], {"name": name})
    # >>> CONFIGURE: adjust to the search response shape
    results = data.get("results") or data.get("Companies") or []
    if not results:
        sys.exit(f"ERROR: no CIN match found for name: {name!r}")
    first = results[0]
    cin = first.get("cin") or first.get("CIN")
    if not cin:
        sys.exit("ERROR: search result did not contain a CIN field (check field mapping).")
    print(f"Resolved '{name}' -> CIN {cin}", file=sys.stderr)
    return cin


def fetch_financials(cin):
    """Fetch the raw financials payload for a CIN."""
    return _request(CONFIG["financials_path"], {"cin": cin})


# ----------------------------------------------------------------------------
# MAPPING  — the part you adapt to the real response shape
# ----------------------------------------------------------------------------
def _to_crore(value, unit="crore"):
    """Normalise a raw figure to INR Crore. InstaFinancials often reports in
    actual rupees or in lakh depending on the field — confirm and set `unit`."""
    if value is None:
        return None
    try:
        v = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if unit == "rupees":
        return round(v / RUPEES_PER_CRORE, 2)
    if unit == "lakh":
        return round(v / LAKH_PER_CRORE, 2)
    return round(v, 2)  # already crore


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _pct(a, b):
    r = _safe_div(a, b)
    return round(r * 100, 1) if r is not None else None


def _map_response(raw):
    """
    Map the raw InstaFinancials JSON into our intermediate metrics + data.json.

    >>> CONFIGURE every raw[...] path below against the actual API response.
    The structure assumed here (raw["financials"] = list of yearly dicts) is a
    placeholder. Replace get_year()/field names with the real ones.
    """
    # ---- locate the yearly statements -------------------------------------
    years = raw.get("financials") or raw.get("FinancialData") or []
    by_fy = {}
    for y in years:
        fy = str(y.get("fy") or y.get("FinancialYear") or "").strip()
        if fy:
            by_fy[fy] = y

    # pick the three most recent FYs present
    fy_keys = sorted(by_fy.keys(), reverse=True)[:3]

    def field(y, *names, unit="rupees"):
        """Pull the first matching field name from a year dict, in crore."""
        for n in names:
            if n in y and y[n] is not None:
                return _to_crore(y[n], unit=unit)
        return None

    metrics_by_year = {}
    for fy in fy_keys:
        y = by_fy[fy]
        revenue = field(y, "RevenueFromOperations", "OperationalIncome", "Revenue")
        ebitda = field(y, "EBITDA")
        finance_cost = field(y, "FinanceCosts", "InterestExpense")
        depreciation = field(y, "Depreciation")
        pat = field(y, "PAT", "ProfitAfterTax", "NetProfit")
        net_worth = field(y, "NetWorth", "ShareholdersFunds")
        lt_debt = field(y, "LongTermBorrowings", "LongTermDebt") or 0
        st_debt = field(y, "ShortTermBorrowings", "ShortTermDebt") or 0
        cash = field(y, "CashAndCashEquivalents", "Cash")
        total_debt = (lt_debt or 0) + (st_debt or 0)

        metrics_by_year[fy] = {
            "revenue": revenue,
            "ebitda": ebitda,
            "ebitda_margin_pct": _pct(ebitda, revenue),
            "finance_cost": finance_cost,
            "depreciation": depreciation,
            "pat": pat,
            "pat_margin_pct": _pct(pat, revenue),
            "cash_profit": (pat + depreciation) if (pat is not None and depreciation is not None) else None,
            "net_worth": net_worth,
            "total_debt": total_debt,
            "de_ratio": round(_safe_div(total_debt, net_worth), 2) if net_worth else None,
            "icr": round(_safe_div(ebitda, finance_cost), 2) if finance_cost else None,
            "cash": cash,
        }

    # ---- company master fields --------------------------------------------
    company_name = (raw.get("companyName") or raw.get("CompanyName")
                    or raw.get("LegalName") or "")
    cin = raw.get("cin") or raw.get("CIN") or ""
    incorp = (raw.get("incorporationDate") or raw.get("DateOfIncorporation") or "")

    # ---- build the financials table (for the report) ---------------------
    def fmt(v, suffix=""):
        if v is None:
            return "—"
        return f"{v:,.1f}{suffix}" if isinstance(v, float) else f"{v}{suffix}"

    def row(label, key, suffix=""):
        return {"values": [label] + [fmt(metrics_by_year.get(fy, {}).get(key), suffix)
                                     for fy in fy_keys], "section_header": False}

    financials_table = {
        "columns": ["Particulars"] + fy_keys,
        "rows": [
            row("Revenue from Operations", "revenue"),
            row("EBITDA", "ebitda"),
            row("EBITDA Margin (%)", "ebitda_margin_pct", "%"),
            row("PAT", "pat"),
            row("PAT Margin (%)", "pat_margin_pct", "%"),
            row("Net Worth", "net_worth"),
            row("Total Debt (LT + ST)", "total_debt"),
            row("Debt / Equity (D/E)", "de_ratio", "x"),
            row("Cash & Equivalents", "cash"),
        ],
    }

    # ---- screening (uses most recent FY) ----------------------------------
    latest = metrics_by_year.get(fy_keys[0], {}) if fy_keys else {}

    def screen(param, threshold, actual, ok):
        return {"parameter": param, "threshold": threshold,
                "actual": actual, "result": "PASS" if ok else "FAIL"}

    nw = latest.get("net_worth")
    rev = latest.get("revenue")
    pat = latest.get("pat")
    screening = [
        screen("LT Credit Rating", "≥ BBB–", "see rating report", True),  # rating not in financials API
        screen("Net Worth", "≥ ₹50 Crores", f"₹{nw:,.0f} Cr" if nw else "—", (nw or 0) >= 50),
        screen("Annual Turnover", "≥ ₹250 Crores", f"₹{rev:,.0f} Cr" if rev else "—", (rev or 0) >= 250),
        screen("PAT", "≥ ₹5 Crores", f"₹{pat:,.0f} Cr" if pat else "—", (pat or 0) >= 5),
    ]

    return {
        "company_name": company_name,
        "cin": cin,
        "incorporation_date": incorp,
        "financials": financials_table,
        "screening": screening,
        # numeric helper block for the scoring step (not rendered directly)
        "_metrics": {"by_year": metrics_by_year, "fy_order": fy_keys},
        "_data_source": "InstaFinancials API",
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Fetch InstaFinancials data -> partial data.json")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cin", help="Corporate Identification Number")
    g.add_argument("--name", help="Company name (resolved to CIN via search)")
    g.add_argument("--mock", help="Path to a saved sample response JSON (offline test)")
    ap.add_argument("--out", required=True, help="Output path for partial data.json")
    args = ap.parse_args()

    if args.mock:
        with open(args.mock, "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        cin = args.cin or resolve_cin(args.name)
        raw = fetch_financials(cin)

    mapped = _map_response(raw)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(mapped, f, indent=2, ensure_ascii=False)

    fy = mapped.get("_metrics", {}).get("fy_order", [])
    print(f"Wrote {args.out}", file=sys.stderr)
    print(f"  Company: {mapped.get('company_name') or '(name not in response)'}", file=sys.stderr)
    print(f"  FYs mapped: {', '.join(fy) if fy else '(none — check field mapping)'}", file=sys.stderr)


if __name__ == "__main__":
    main()
