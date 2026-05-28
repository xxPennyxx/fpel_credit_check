"""
FPEL Renewable Capital - Credit Check (Dummy App)
SQLAlchemy models.

Mirrors the SOP at a simplified level:
- Company  -> the offtaker / customer
- Case     -> a credit-check request raised by BD
- CreditCheck -> the RC team's review of a case
- FinancialRecord -> per-year financial line-items (mocked Instafinancials data)
- AuditLog -> override / approval trail
"""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Company(db.Model):
    """The prospective / existing offtaker."""
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    cin = db.Column(db.String(50))                     # mock CIN
    industry = db.Column(db.String(120))
    date_of_incorporation = db.Column(db.Date)
    is_existing_offtaker = db.Column(db.Boolean, default=False)
    external_rating_agency = db.Column(db.String(30))   # e.g. CRISIL
    external_rating = db.Column(db.String(15))          # e.g. AA
    external_rating_date = db.Column(db.Date)

    cases = db.relationship("Case", back_populates="company", cascade="all, delete-orphan")
    financials = db.relationship("FinancialRecord", back_populates="company",
                                 cascade="all, delete-orphan",
                                 order_by="FinancialRecord.fiscal_year.desc()")


class FinancialRecord(db.Model):
    """Year-wise financials for a company (mock Instafinancials pull, in INR Crores)."""
    __tablename__ = "financial_records"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    fiscal_year = db.Column(db.String(10), nullable=False)   # e.g. "FY2025"
    revenue_cr = db.Column(db.Float)
    ebitda_cr = db.Column(db.Float)
    pat_cr = db.Column(db.Float)
    networth_cr = db.Column(db.Float)
    debt_cr = db.Column(db.Float)
    interest_cr = db.Column(db.Float)
    source = db.Column(db.String(30), default="Instafinancials")   # or "Manual Upload"

    company = db.relationship("Company", back_populates="financials")


class Case(db.Model):
    """A credit-check request raised by the BD team (the 'All Cases' submission)."""
    __tablename__ = "cases"

    id = db.Column(db.Integer, primary_key=True)
    case_ref = db.Column(db.String(20), unique=True, nullable=False)   # e.g. CC-2026-001
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)

    # Capacity Required (per SOP Section I.2)
    solar_mwp = db.Column(db.Float, default=0)
    wind_mw = db.Column(db.Float, default=0)
    # total_mwp is derived: solar_mwp + (wind_mw * 2)

    park_name = db.Column(db.String(120))
    state_name = db.Column(db.String(80))
    consumption_summary = db.Column(db.Text)             # SOP Section I.6

    raised_by = db.Column(db.String(120))                # BD user
    raised_on = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(30), default="Submitted")
    # Status flow: Submitted -> Under Review -> Maker Done -> Reviewer Approved -> Dispatched

    company = db.relationship("Company", back_populates="cases")
    credit_check = db.relationship("CreditCheck", back_populates="case",
                                   uselist=False, cascade="all, delete-orphan")

    @property
    def total_mwp(self):
        return round((self.solar_mwp or 0) + (self.wind_mw or 0) * 2, 2)


class CreditCheck(db.Model):
    """The RC-team review built on top of a Case (SOP Section II)."""
    __tablename__ = "credit_checks"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, unique=True)

    brief_profile = db.Column(db.Text)
    financial_analysis = db.Column(db.Text)
    strengths = db.Column(db.Text)
    weaknesses = db.Column(db.Text)
    latest_updates = db.Column(db.Text)

    # Scoring matrix - simple ratios (dummy)
    revenue_growth_pct = db.Column(db.Float)
    ebitda_margin_pct = db.Column(db.Float)
    debt_equity = db.Column(db.Float)
    interest_coverage = db.Column(db.Float)
    composite_score = db.Column(db.Float)
    internal_rating = db.Column(db.String(15))    # e.g. "4PEL AA+"

    # View / decision
    decision = db.Column(db.String(30))            # Approved / Not Approved / On Hold / Judgmentally Approved
    decision_conditions = db.Column(db.Text)

    # Workflow
    maker = db.Column(db.String(120))
    reviewer = db.Column(db.String(120))
    rc_head_required = db.Column(db.Boolean, default=False)
    rc_head_approved = db.Column(db.Boolean, default=False)
    updated_on = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    case = db.relationship("Case", back_populates="credit_check")
    audit_logs = db.relationship("AuditLog", back_populates="credit_check",
                                 cascade="all, delete-orphan",
                                 order_by="AuditLog.timestamp.desc()")


class AuditLog(db.Model):
    """Records every override / decision action - SOP demands an audit trail."""
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    credit_check_id = db.Column(db.Integer, db.ForeignKey("credit_checks.id"), nullable=False)
    actor = db.Column(db.String(120))
    action = db.Column(db.String(200))
    note = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    credit_check = db.relationship("CreditCheck", back_populates="audit_logs")


# ----------------------------------------------------------------------------
# Authentication & RBAC
# ----------------------------------------------------------------------------
class User(db.Model):
    """
    A user who has signed in via Microsoft SSO at least once.

    Roles:
      - "super_admin"  -> hard-coded (ai@fourthpartner.co), full powers
      - "admin"        -> can access the Admin portal, manage roles, send test mail
      - "viewer"       -> read-only access to the app
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(200))
    oid = db.Column(db.String(64), index=True)      # Azure AD object id (stable)
    tenant_id = db.Column(db.String(64))
    role = db.Column(db.String(20), nullable=False, default="viewer")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    first_login_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, default=datetime.utcnow)
    login_count = db.Column(db.Integer, default=0, nullable=False)

    def is_admin(self) -> bool:
        return self.role in ("admin", "super_admin")

    def is_super_admin(self) -> bool:
        return self.role == "super_admin"


# ----------------------------------------------------------------------------
# D365 Credit Case mirror
# ----------------------------------------------------------------------------
class D365Case(db.Model):
    """
    A local cache of a record from the D365 FPELDetailBases entity.

    On every Sync, we fetch all rows from D365, resolve OnwerWorker_PersonnelNumber
    -> Employee.Name from the D365 Employees entity, and upsert into this table.
    The dashboard then reads from here so the page is fast and works offline.
    """
    __tablename__ = "d365_cases"

    # Internal PK (we don't trust D365 CaseId to be unique cross-company)
    id = db.Column(db.Integer, primary_key=True)

    # D365 primary identifier
    case_id = db.Column(db.String(64), unique=True, index=True, nullable=False)

    # Resolved person name (display) + raw personnel number (for traceability)
    responsible_person = db.Column(db.String(200))            # display name
    responsible_person_personnel_number = db.Column(db.String(50))

    # Status / categorisation
    status = db.Column(db.String(64))
    case_category = db.Column(db.String(120))                 # CaseCategoryHierarchyDetail_CaseCategory
    project_category = db.Column(db.String(120))              # FPELProjectCategory
    type_of_project = db.Column(db.String(120))               # FPELTypeOfProject
    segment = db.Column(db.String(120))                       # FPELSegment
    sub_segment = db.Column(db.String(120))                   # FPELSubSegment

    # Entity / location
    entity_name = db.Column(db.String(200))                   # FPELEntityName
    entity_code = db.Column(db.String(64))                    # FPELEntityCode
    state = db.Column(db.String(64))                          # FPELState
    location = db.Column(db.String(200))                      # FPELLocation
    park_name = db.Column(db.String(200))                     # FPELParkName

    # Capacity (numeric where possible)
    solar_capacity = db.Column(db.Float)                      # FPELSolarCapacity
    wind_capacity = db.Column(db.Float)                       # FPELWindCapacity

    # Credit & ratings
    internal_credit_rating = db.Column(db.String(32))         # FPELInternalCreditRating
    external_credit_rating = db.Column(db.String(32))         # FPELExternalCreditRating
    external_credit_rating_agency = db.Column(db.String(120)) # FPELExternalCreditRatingAgency
    other_rating_agency = db.Column(db.String(120))           # FPELOtherRatingAgency
    reason_for_no_rating = db.Column(db.Text)                 # FPELReasonForNoRating
    reason_for_notch_int_ext = db.Column(db.Text)             # FPELRsnForNotchIntAndExt
    reason_for_judgement_upgrade = db.Column(db.Text)         # FPELRsnForJudgementUpgrade

    # Dates (stored as strings; D365 returns ISO timestamps - we keep raw for fidelity)
    date_of_ext_credit_rating = db.Column(db.String(64))      # FPELDateOfExtCreditRating
    date_for_dua = db.Column(db.String(64))                   # FPELDateForDUA
    date_of_final_deviation = db.Column(db.String(64))        # FPELDateOfFinalDeviation
    ppa_request_date_bd = db.Column(db.String(64))            # FPELPPARequestDateBD
    credit_check_month = db.Column(db.String(64))             # FPELCreditCheckMonth
    closed_datetime = db.Column(db.String(64))                # ClosedDateTime

    # Workflow / comms
    closed_by = db.Column(db.String(200))                     # ClosedBy
    reply_bd_to_rc = db.Column(db.Text)                       # FPELReplyFromBDtoRC
    reply_rc_to_bd = db.Column(db.Text)                       # FPELReplyRCtoBD
    description = db.Column(db.Text)                          # Description
    memo = db.Column(db.Text)                                 # Memo

    # Sync metadata
    last_synced_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<D365Case {self.case_id} {self.entity_name}>"


class SyncMeta(db.Model):
    """
    Single-row table that records the last successful D365 sync run -
    timestamp, row count, and last error (if any). Used to surface
    'Last synced X minutes ago' on the dashboard.
    """
    __tablename__ = "sync_meta"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    last_run_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(20))            # "ok" / "error"
    rows_synced = db.Column(db.Integer)
    last_message = db.Column(db.Text)
    triggered_by = db.Column(db.String(200))
