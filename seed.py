"""
Dummy data loader.
All names / numbers below are fictional and used only to demo the workflow.
"""

from datetime import date, datetime
from models import db, Company, FinancialRecord, Case, CreditCheck, AuditLog


def load_dummy_data():
    # ---------- Companies ----------
    companies_data = [
        dict(name="Suryam Steelworks Pvt Ltd", cin="U27100MH2010PTC123456",
             industry="Steel Manufacturing", is_existing_offtaker=True,
             external_rating_agency="CRISIL", external_rating="AA",
             external_rating_date=date(2025, 10, 14)),
        dict(name="Megha Textiles Ltd", cin="U17100GJ2008PLC987654",
             industry="Textiles", is_existing_offtaker=False,
             external_rating_agency="ICRA", external_rating="A+",
             external_rating_date=date(2025, 8, 2)),
        dict(name="Vrinda Pharma Industries", cin="U24230TG2012PTC112233",
             industry="Pharmaceuticals", is_existing_offtaker=True,
             external_rating_agency="CARE", external_rating="AA-",
             external_rating_date=date(2025, 11, 30)),
        dict(name="Tejas Auto Components", cin="U29100TN2015PTC445566",
             industry="Auto Components", is_existing_offtaker=False,
             external_rating_agency="CRISIL", external_rating="A",
             external_rating_date=date(2026, 1, 18)),
    ]
    companies = []
    for d in companies_data:
        c = Company(**d)
        db.session.add(c)
        companies.append(c)
    db.session.flush()

    # ---------- Financials (INR Crores) ----------
    fin_rows = [
        # Suryam Steelworks
        (0, "FY2025", 1240.0, 198.0, 88.0, 720.0, 410.0, 42.0),
        (0, "FY2024", 1080.0, 162.0, 70.0, 650.0, 380.0, 38.0),
        (0, "FY2023",  920.0, 130.0, 55.0, 590.0, 340.0, 35.0),
        # Megha Textiles
        (1, "FY2025",  680.0,  95.0, 38.0, 410.0, 220.0, 24.0),
        (1, "FY2024",  610.0,  82.0, 30.0, 380.0, 210.0, 22.0),
        (1, "FY2023",  555.0,  70.0, 24.0, 360.0, 200.0, 21.0),
        # Vrinda Pharma
        (2, "FY2025",  890.0, 178.0, 95.0, 540.0, 180.0, 18.0),
        (2, "FY2024",  780.0, 148.0, 78.0, 470.0, 170.0, 17.0),
        (2, "FY2023",  720.0, 130.0, 65.0, 420.0, 165.0, 16.0),
        # Tejas Auto
        (3, "FY2025",  430.0,  56.0, 18.0, 250.0, 165.0, 14.0),
        (3, "FY2024",  390.0,  48.0, 14.0, 235.0, 155.0, 13.0),
        (3, "FY2023",  370.0,  44.0, 12.0, 225.0, 150.0, 12.0),
    ]
    for idx, fy, rev, ebitda, pat, nw, debt, intr in fin_rows:
        db.session.add(FinancialRecord(
            company_id=companies[idx].id, fiscal_year=fy,
            revenue_cr=rev, ebitda_cr=ebitda, pat_cr=pat,
            networth_cr=nw, debt_cr=debt, interest_cr=intr,
        ))

    # ---------- Cases ----------
    cases_data = [
        dict(company=companies[0], case_ref="CC-2026-001",
             solar_mwp=4.5, wind_mw=0, park_name="Ramanakoppa",
             state_name="Karnataka",
             consumption_summary="Avg 6.2 MU per month; replacement ~38%.",
             raised_by="Aarav Sharma (BD)", status="Maker Done"),
        dict(company=companies[1], case_ref="CC-2026-002",
             solar_mwp=2.8, wind_mw=0.6, park_name="Amreli",
             state_name="Gujarat",
             consumption_summary="Avg 3.4 MU per month; replacement ~45%.",
             raised_by="Neha Kapoor (BD)", status="Submitted"),
        dict(company=companies[2], case_ref="CC-2026-003",
             solar_mwp=6.0, wind_mw=0, park_name="Rooftop - Hyderabad",
             state_name="Telangana",
             consumption_summary="Avg 8.1 MU per month; replacement ~52%.",
             raised_by="Rohan Mehta (BD)", status="Dispatched"),
    ]
    cases = []
    for d in cases_data:
        case = Case(**d)
        db.session.add(case)
        cases.append(case)
    db.session.flush()

    # ---------- Credit checks for the first and third cases ----------
    cc1 = CreditCheck(
        case=cases[0],
        brief_profile=("Suryam Steelworks is a mid-sized integrated steel "
                       "producer with operations in Maharashtra and Karnataka."),
        financial_analysis=("Revenue grew 14.8% YoY in FY25 on the back of "
                            "higher long-product realisations. EBITDA margin "
                            "expanded ~100 bps. Leverage remains moderate."),
        strengths="Stable promoter group; long-term offtake contracts; healthy interest coverage.",
        weaknesses="Cyclical exposure to steel prices; modest geographic diversification.",
        latest_updates="Announced a 0.3 MTPA capacity expansion in Mar-2026 (internal accruals).",
        revenue_growth_pct=14.8, ebitda_margin_pct=16.0,
        debt_equity=0.57, interest_coverage=4.7,
        decision="Approved", decision_conditions="Standard payment security; PDC of 1 month.",
        maker="Priya Iyer (RC)", reviewer="Karan Desai (RM)",
    )
    cc1.composite_score = 78.5
    cc1.internal_rating = "4PEL AA"
    cc1.rc_head_required = False
    db.session.add(cc1)

    cc3 = CreditCheck(
        case=cases[2],
        brief_profile=("Vrinda Pharma is a formulations player focused on "
                       "domestic and SE-Asia markets."),
        financial_analysis=("Strong 14% revenue CAGR over FY23-25; EBITDA "
                            "margin steady at ~20%. Net debt declining."),
        strengths="High EBITDA margin; low leverage; consistent cash generation.",
        weaknesses="Concentration in a few therapy areas; FX exposure on exports.",
        latest_updates="Cleared USFDA inspection at Hyderabad facility (Feb-2026).",
        revenue_growth_pct=14.1, ebitda_margin_pct=20.0,
        debt_equity=0.33, interest_coverage=9.9,
        decision="Approved",
        decision_conditions="Corporate Guarantee from parent; promoter participation in PPA.",
        maker="Priya Iyer (RC)", reviewer="Karan Desai (RM)",
    )
    cc3.composite_score = 86.2
    cc3.internal_rating = "4PEL AA+"
    cc3.rc_head_required = True
    cc3.rc_head_approved = True
    db.session.add(cc3)
    db.session.flush()

    db.session.add(AuditLog(credit_check=cc1, actor="Priya Iyer (RC)",
                            action="Credit check created"))
    db.session.add(AuditLog(credit_check=cc1, actor="Karan Desai (RM)",
                            action="Reviewer approved"))
    db.session.add(AuditLog(credit_check=cc3, actor="Priya Iyer (RC)",
                            action="Dispatched to BD"))

    db.session.commit()
