"""
4PEL internal credit-scoring matrix (RC Credit Check).

Pure, deterministic implementation of the scoring bands defined in the
rc-credit-check skill. No AI / no external calls — this is the auditable
source of truth for the internal rating and decision.

Public API:
    score_credit_check(inputs: dict) -> dict
        returns {screening, parameters, total_score, max_score, rating, decision}
"""

from __future__ import annotations

# Long-term external rating -> score (parameter 1, max 20)
_RATING_SCORE = {
    "AAA+": 20, "AAA": 19, "AAA-": 18,
    "AA+": 17, "AA": 16, "AA-": 15,
    "A+": 14, "A": 13, "A-": 12,
    "BBB+": 10, "BBB": 7, "BBB-": 4,
    "BB+": 1, "BB": -3, "BB-": -7,
}

# Ratings that clear the "≥ BBB-" screening gate
_INVESTMENT_GRADE = {
    "AAA+", "AAA", "AAA-", "AA+", "AA", "AA-",
    "A+", "A", "A-", "BBB+", "BBB", "BBB-",
}

_INDUSTRY_SCORE = {
    "government": 5, "foreign mnc": 5, "it": 5, "pharma": 5, "fmcg": 5,
    "conglomerate": 4, "services": 4,
    "manufacturing": 3,
    "trading": 2, "textiles": 2,
    "real estate": 1, "risky": 1,
}


def _norm_rating(r: str | None) -> str:
    return (r or "").strip().upper().replace(" ", "")


def _band(value, bands, default=0):
    """bands: list of (threshold, score) sorted high->low; returns score for
    the first threshold value >= threshold."""
    if value is None:
        return default
    for threshold, score in bands:
        if value >= threshold:
            return score
    return default


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def score_credit_check(inp: dict) -> dict:
    """Compute screening + 12-parameter score + rating + decision.

    Expected keys (FY2025 basis, all optional/best-effort):
      long_term_rating, net_worth_cr, turnover_cr, pat_cr, market_cap_cr,
      debt_equity, interest_coverage, revenue_growth_pct, ebitda_margin_pct,
      pat_margin_pct, cash_profit_pct, industry, directors_rating (1-5),
      debt_free (bool), listed (bool)
    """
    rating = _norm_rating(inp.get("long_term_rating"))
    net_worth = _f(inp.get("net_worth_cr"))
    turnover = _f(inp.get("turnover_cr"))
    pat = _f(inp.get("pat_cr"))
    market_cap = _f(inp.get("market_cap_cr"))
    de = _f(inp.get("debt_equity"))
    icr = _f(inp.get("interest_coverage"))
    rev_growth = _f(inp.get("revenue_growth_pct"))
    ebitda_m = _f(inp.get("ebitda_margin_pct"))
    pat_m = _f(inp.get("pat_margin_pct"))
    cash_m = _f(inp.get("cash_profit_pct"))
    industry = (inp.get("industry") or "").strip().lower()
    directors = _f(inp.get("directors_rating"))
    debt_free = bool(inp.get("debt_free"))
    listed = bool(inp.get("listed"))

    # ---- Initial screening (all four must pass for standard path) ----
    def res(ok):
        return "PASS" if ok else "FAIL"

    screening = [
        {"parameter": "LT Credit Rating", "threshold": "≥ BBB-",
         "actual": rating or "NA",
         "result": res(rating in _INVESTMENT_GRADE)},
        {"parameter": "Net Worth", "threshold": "≥ ₹50 Cr",
         "actual": f"₹{net_worth:.0f} Cr" if net_worth is not None else "—",
         "result": res((net_worth or 0) >= 50)},
        {"parameter": "Annual Turnover", "threshold": "≥ ₹250 Cr",
         "actual": f"₹{turnover:.0f} Cr" if turnover is not None else "—",
         "result": res((turnover or 0) >= 250)},
        {"parameter": "PAT", "threshold": "≥ ₹5 Cr",
         "actual": f"₹{pat:.0f} Cr" if pat is not None else "—",
         "result": res((pat or 0) >= 5)},
    ]

    # ---- 12 scored parameters ----
    p = []

    # 1. LT Credit Rating (max 20)
    s1 = _RATING_SCORE.get(rating, 0)
    p.append(("LT Credit Rating", 20, s1,
              f"{rating or 'Unrated'}"))

    # 2. Net Worth (max 10)
    s2 = _band(net_worth, [(500, 10), (400, 8), (300, 6), (200, 4), (100, 2)], 0)
    p.append(("Net Worth", 10, s2,
              f"₹{net_worth:.0f} Cr" if net_worth is not None else "—"))

    # 3. Market Cap (max 5, listed only)
    s3 = _band(market_cap, [(3000, 5), (2500, 4), (2000, 3), (1500, 2), (1000, 1)], 0) if listed else 0
    p.append(("Market Capitalisation", 5, s3,
              ("Unlisted" if not listed else
               (f"₹{market_cap:.0f} Cr" if market_cap is not None else "—"))))

    # 4. Debt:Equity (max 10)
    if debt_free or (de is not None and de <= 0.5):
        s4 = 10
    elif de is None:
        s4 = 0
    elif de <= 1.0:
        s4 = 8
    elif de <= 1.5:
        s4 = 5
    elif de <= 1.75:
        s4 = 3
    elif de <= 2.0:
        s4 = 0
    else:
        s4 = -3
    p.append(("Debt / Equity Ratio", 10, s4,
              "Debt-free" if debt_free else (f"{de:.2f}x" if de is not None else "—")))

    # 5. Interest Coverage (max 5)
    if debt_free:
        s5 = 5
    else:
        s5 = _band(icr, [(5, 5), (4, 4), (3, 3), (2, 2), (1, 1)], 0)
    p.append(("Interest Coverage Ratio", 5, s5,
              "NA (debt-free)" if debt_free else (f"{icr:.2f}x" if icr is not None else "—")))

    # 6. Turnover (max 5)
    s6 = _band(turnover, [(1000, 5), (750, 4), (500, 3), (350, 2), (200, 1)], 0)
    p.append(("Revenue / Turnover", 5, s6,
              f"₹{turnover:.0f} Cr" if turnover is not None else "—"))

    # 7. Revenue Growth (max 5)
    if rev_growth is None:
        s7 = 0
    elif rev_growth >= 12.5:
        s7 = 5
    elif rev_growth >= 10:
        s7 = 4
    elif rev_growth >= 8.5:
        s7 = 3
    elif rev_growth >= 7:
        s7 = 2
    elif rev_growth >= 5:
        s7 = 1
    elif rev_growth >= 0:
        s7 = 0
    elif rev_growth >= -3:
        s7 = -2
    else:
        s7 = -5
    p.append(("Revenue Growth", 5, s7,
              f"{rev_growth:.1f}% YoY" if rev_growth is not None else "—"))

    # 8. EBITDA Margin (max 10)
    if ebitda_m is None:
        s8 = 0
    elif ebitda_m >= 15:
        s8 = 10
    elif ebitda_m >= 12.5:
        s8 = 8
    elif ebitda_m >= 10:
        s8 = 6
    elif ebitda_m >= 7.5:
        s8 = 4
    elif ebitda_m >= 5:
        s8 = 2
    elif ebitda_m >= 0:
        s8 = 0
    else:
        s8 = -5
    p.append(("EBITDA Margin", 10, s8,
              f"{ebitda_m:.1f}%" if ebitda_m is not None else "—"))

    # 9. PAT Margin (max 10)
    if pat_m is None:
        s9 = 0
    elif pat_m >= 8:
        s9 = 10
    elif pat_m >= 6:
        s9 = 8
    elif pat_m >= 4:
        s9 = 6
    elif pat_m >= 2:
        s9 = 4
    elif pat_m >= 1:
        s9 = 2
    elif pat_m >= 0:
        s9 = 0
    elif pat_m >= -2:
        s9 = -4
    elif pat_m >= -5:
        s9 = -8
    else:
        s9 = -10
    p.append(("PAT Margin", 10, s9,
              f"{pat_m:.1f}%" if pat_m is not None else "—"))

    # 10. Cash Profit % (max 10)
    if cash_m is None:
        s10 = 0
    elif cash_m >= 11.2:
        s10 = 10
    elif cash_m >= 8.4:
        s10 = 8
    elif cash_m >= 5.6:
        s10 = 6
    elif cash_m >= 2.8:
        s10 = 4
    elif cash_m >= 1:
        s10 = 2
    elif cash_m >= 0:
        s10 = 0
    else:
        s10 = -2
    p.append(("Cash Profit", 10, s10,
              f"{cash_m:.1f}%" if cash_m is not None else "—"))

    # 11. Industry (max 5)
    s11 = 0
    for key, val in _INDUSTRY_SCORE.items():
        if key in industry:
            s11 = max(s11, val)
    if industry and s11 == 0:
        s11 = 3  # default to manufacturing-level if named but unmatched
    p.append(("Industry / Sector", 5, s11, inp.get("industry") or "—"))

    # 12. Directors' rating (max 5, subjective 1-5)
    s12 = int(directors) if directors is not None else 3
    s12 = max(1, min(5, s12))
    p.append(("Board / Directors Profile", 5, s12, f"{s12}/5 (subjective)"))

    total = sum(score for (_, _, score, _) in p)
    max_score = sum(mx for (_, mx, _, _) in p)  # = 100

    rating_label, decision = _rating_from_total(total)

    return {
        "screening": screening,
        "parameters": [
            {"parameter": name, "max_score": mx, "score": sc, "remarks": rm}
            for (name, mx, sc, rm) in p
        ],
        "total_score": total,
        "max_score": max_score,
        "rating": rating_label,
        "decision": decision,
    }


def _rating_from_total(total: float) -> tuple[str, str]:
    table = [
        (95, "4PEL AAA+", "Approved"),
        (92.5, "4PEL AAA", "Approved"),
        (90, "4PEL AAA-", "Approved"),
        (85, "4PEL AA+", "Approved"),
        (80, "4PEL AA", "Approved"),
        (75, "4PEL AA-", "Approved"),
        (70, "4PEL A+", "Approved"),
        (65, "4PEL A", "Approved"),
        (60, "4PEL A-", "Approved"),
        (55, "4PEL BBB+", "Approved"),
        (50, "4PEL BBB", "Judgementally Approved"),
        (45, "4PEL BBB-", "Judgementally Approved"),
    ]
    for threshold, label, decision in table:
        if total >= threshold:
            return label, decision
    return "—", "Not Approved"
