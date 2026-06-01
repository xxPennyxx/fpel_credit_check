"""
D365 Finance & Operations - Credit Check sync
=============================================
Pulls credit-case records from the FPELDetailBases OData entity, resolves each
record's OnwerWorker_PersonnelNumber against the Employees entity (to produce
a human-readable ResponsiblePerson name), and upserts the result into the local
SQLite cache (d365_cases table).

Auth: OAuth2 client-credentials flow (Azure AD) using MSAL. Token is cached
      in-process for 55 minutes (D365 tokens are valid for 60).

Pattern mirrors the Site Survey app's d365_sync.py so the two stay consistent.
"""

import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from typing import Any

import msal

from models import db, D365Case, SyncMeta


# ---------------------------------------------------------------------------
#  Configuration helpers
# ---------------------------------------------------------------------------
def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key, default) or default


def _d365_base() -> str:
    return _cfg("D365_BASE_URL", "").rstrip("/")


def _company() -> str:
    return _cfg("D365_COMPANY", "")          # blank → cross-company query only


def _cases_entity() -> str:
    return _cfg("D365_CASES_ENTITY", "FPELDetailBases")


def _employees_entity() -> str:
    return _cfg("D365_EMPLOYEES_ENTITY", "Employees")


# ---------------------------------------------------------------------------
#  Auth - with in-process token cache (avoids re-authenticating every call)
# ---------------------------------------------------------------------------
_token_cache = {"token": None, "expires_at": 0.0}


def _get_d365_token() -> str:
    now = time.monotonic()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    tenant_id = _cfg("D365_TENANT_ID")
    client_id = _cfg("D365_CLIENT_ID")
    client_secret = _cfg("D365_CLIENT_SECRET")
    base = _d365_base()

    if not all([tenant_id, client_id, client_secret, base]):
        raise RuntimeError(
            "D365 environment variables are not fully configured. "
            "Set D365_TENANT_ID, D365_CLIENT_ID, D365_CLIENT_SECRET, D365_BASE_URL in .env"
        )

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    scope = [f"{base}/.default"]

    app = msal.ConfidentialClientApplication(
        client_id, authority=authority, client_credential=client_secret
    )
    result = app.acquire_token_for_client(scopes=scope)
    if "access_token" not in result:
        raise RuntimeError(
            f"D365 token acquisition failed: "
            f"{result.get('error_description') or result.get('error') or json.dumps(result)}"
        )

    _token_cache["token"] = result["access_token"]
    _token_cache["expires_at"] = now + 3300        # 55 min
    return _token_cache["token"]


# ---------------------------------------------------------------------------
#  OData helpers
# ---------------------------------------------------------------------------
def _odata_get(url: str, token: str) -> dict:
    """Single GET. Returns parsed JSON body."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def _odata_get_all(url: str, token: str, max_pages: int = 200) -> list[dict]:
    """Walk the @odata.nextLink chain and return all rows."""
    rows: list[dict] = []
    page = 0
    next_url: str | None = url
    while next_url and page < max_pages:
        body = _odata_get(next_url, token)
        rows.extend(body.get("value", []) or [])
        next_url = body.get("@odata.nextLink")
        page += 1
    return rows


# ---------------------------------------------------------------------------
#  Field selection - we only ask D365 for what the user requested
# ---------------------------------------------------------------------------
# Field list provided by the user. OnwerWorker_PersonnelNumber is included
# (D365 spelling preserved) - it's resolved into a name via the Employees lookup.
CASE_FIELDS = [
    "CaseId",
    "OnwerWorker_PersonnelNumber",
    "FPELSolarCapacity",
    "Status",
    "FPELState",
    "ClosedBy",
    "CaseCategoryHierarchyDetail_CaseCategory",
    "FPELReasonForNoRating",
    "Memo",
    "FPELTypeOfProject",
    "FPELSubSegment",
    "FPELSegment",
    "FPELDateOfExtCreditRating",
    "FPELDateForDUA",
    "ClosedDateTime",
    "FPELProjectCategory",
    "FPELOtherRatingAgency",
    "Description",
    "FPELDateOfFinalDeviation",
    "FPELReplyFromBDtoRC",
    "FPELExternalCreditRating",
    "FPELInternalCreditRating",
    "FPELParkName",
    "FPELRsnForNotchIntAndExt",
    "FPELLocation",
    "FPELEntityName",
    "FPELCreditCheckMonth",
    "FPELWindCapacity",
    "FPELReplyRCtoBD",
    "FPELExternalCreditRatingAgency",
    "FPELRsnForJudgementUpgrade",
    "FPELEntityCode",
    "FPELPPARequestDateBD",
]

# Employees fields we need to build the personnel-number -> name map.
EMPLOYEE_FIELDS = ["PersonnelNumber", "Name"]


# ---------------------------------------------------------------------------
#  Fetch
# ---------------------------------------------------------------------------
def fetch_d365_cases() -> tuple[bool, list[dict] | str]:
    """Fetch every row from FPELDetailBases. Returns (ok, rows_or_error_msg)."""
    base = _d365_base()
    if not base:
        return False, "D365_BASE_URL not configured."

    try:
        token = _get_d365_token()
    except Exception as exc:
        return False, f"Auth failed: {exc}"

    select = ",".join(CASE_FIELDS)
    qs = [f"$select={select}", "cross-company=true"]
    if _company():
        # If a company code is configured, scope server-side
        flt = urllib.parse.quote(f"dataAreaId eq '{_company()}'")
        qs.append(f"$filter={flt}")
    url = f"{base}/data/{_cases_entity()}?" + "&".join(qs)

    try:
        rows = _odata_get_all(url, token)
        return True, rows
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        return False, f"D365 HTTP {exc.code}: {body[:500]}"
    except Exception as exc:
        return False, f"D365 fetch error: {exc}"


def fetch_d365_employees_map() -> tuple[bool, dict[str, str] | str]:
    """Fetch all employees and return {PersonnelNumber: Name}.

    For tenants with many employees this can be slow on first call; we cache
    the result for the duration of one sync via the local DB call site.
    """
    base = _d365_base()
    if not base:
        return False, "D365_BASE_URL not configured."

    try:
        token = _get_d365_token()
    except Exception as exc:
        return False, f"Auth failed: {exc}"

    select = ",".join(EMPLOYEE_FIELDS)
    url = f"{base}/data/{_employees_entity()}?$select={select}&cross-company=true"

    try:
        rows = _odata_get_all(url, token)
        out: dict[str, str] = {}
        for r in rows:
            pn = (r.get("PersonnelNumber") or "").strip()
            name = (r.get("Name") or "").strip()
            if pn and name:
                # Keep first non-empty mapping; an employee can appear in multiple
                # companies but the Name is consistent.
                out.setdefault(pn, name)
        return True, out
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        return False, f"D365 HTTP {exc.code}: {body[:500]}"
    except Exception as exc:
        return False, f"D365 employees fetch error: {exc}"


def resolve_employee_id(name: str | None = None, email: str | None = None) -> str | None:
    """Best-effort lookup of an Employee's PersonnelNumber (Employee ID) from
    the D365 Employees entity, matching on Name first and then on the primary
    email field if configured.

    Returns the PersonnelNumber string, or None if D365 is unreachable / the
    employee can't be matched. Callers must treat None as "not resolved".
    """
    base = _d365_base()
    if not base:
        return None
    try:
        token = _get_d365_token()
    except Exception:
        return None

    # Optional email field name (varies by tenant); only used if configured.
    email_field = _cfg("D365_EMPLOYEE_EMAIL_FIELD", "").strip()

    # Build candidate $filter clauses (tried in order, most reliable first).
    filters = []
    if name:
        safe = name.replace("'", "''")
        filters.append(f"Name eq '{safe}'")
    if email and email_field:
        safe = email.replace("'", "''")
        filters.append(f"{email_field} eq '{safe}'")

    select = ",".join(EMPLOYEE_FIELDS)
    for flt in filters:
        qs = urllib.parse.urlencode({"$select": select, "$filter": flt})
        url = f"{base}/data/{_employees_entity()}?{qs}&cross-company=true"
        try:
            data = _odata_get(url, token)
            rows = data.get("value") or []
            if rows:
                pn = (rows[0].get("PersonnelNumber") or "").strip()
                if pn:
                    return pn
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
#  Helpers - coerce D365 values into model types
# ---------------------------------------------------------------------------
def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _row_to_case_kwargs(row: dict, emp_map: dict[str, str]) -> dict:
    """Translate one D365 row (camelCase D365 names) into D365Case kwargs."""
    pn = _safe_str(row.get("OnwerWorker_PersonnelNumber"))
    resolved_name = emp_map.get(pn or "", "") if pn else ""

    return dict(
        case_id=_safe_str(row.get("CaseId")) or "",
        responsible_person=resolved_name or None,
        responsible_person_personnel_number=pn,
        status=_safe_str(row.get("Status")),
        case_category=_safe_str(row.get("CaseCategoryHierarchyDetail_CaseCategory")),
        project_category=_safe_str(row.get("FPELProjectCategory")),
        type_of_project=_safe_str(row.get("FPELTypeOfProject")),
        segment=_safe_str(row.get("FPELSegment")),
        sub_segment=_safe_str(row.get("FPELSubSegment")),
        entity_name=_safe_str(row.get("FPELEntityName")),
        entity_code=_safe_str(row.get("FPELEntityCode")),
        state=_safe_str(row.get("FPELState")),
        location=_safe_str(row.get("FPELLocation")),
        park_name=_safe_str(row.get("FPELParkName")),
        solar_capacity=_safe_float(row.get("FPELSolarCapacity")),
        wind_capacity=_safe_float(row.get("FPELWindCapacity")),
        internal_credit_rating=_safe_str(row.get("FPELInternalCreditRating")),
        external_credit_rating=_safe_str(row.get("FPELExternalCreditRating")),
        external_credit_rating_agency=_safe_str(row.get("FPELExternalCreditRatingAgency")),
        other_rating_agency=_safe_str(row.get("FPELOtherRatingAgency")),
        reason_for_no_rating=_safe_str(row.get("FPELReasonForNoRating")),
        reason_for_notch_int_ext=_safe_str(row.get("FPELRsnForNotchIntAndExt")),
        reason_for_judgement_upgrade=_safe_str(row.get("FPELRsnForJudgementUpgrade")),
        date_of_ext_credit_rating=_safe_str(row.get("FPELDateOfExtCreditRating")),
        date_for_dua=_safe_str(row.get("FPELDateForDUA")),
        date_of_final_deviation=_safe_str(row.get("FPELDateOfFinalDeviation")),
        ppa_request_date_bd=_safe_str(row.get("FPELPPARequestDateBD")),
        credit_check_month=_safe_str(row.get("FPELCreditCheckMonth")),
        closed_datetime=_safe_str(row.get("ClosedDateTime")),
        closed_by=_safe_str(row.get("ClosedBy")),
        reply_bd_to_rc=_safe_str(row.get("FPELReplyFromBDtoRC")),
        reply_rc_to_bd=_safe_str(row.get("FPELReplyRCtoBD")),
        description=_safe_str(row.get("Description")),
        memo=_safe_str(row.get("Memo")),
        last_synced_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
#  Sync orchestrator
# ---------------------------------------------------------------------------
def sync_d365_cases(triggered_by: str = "system") -> tuple[bool, str, int]:
    """
    Pull all FPELDetailBases rows + the Employees map, upsert into d365_cases.
    Returns (ok, message, rows_processed).
    """
    started = datetime.utcnow()

    # 1. Pull employees first so we can resolve names while iterating cases
    ok_emp, emp_map_or_err = fetch_d365_employees_map()
    if not ok_emp:
        _record_meta(False, 0, str(emp_map_or_err), triggered_by, started)
        return False, str(emp_map_or_err), 0
    emp_map: dict[str, str] = emp_map_or_err  # type: ignore[assignment]

    # 2. Pull cases
    ok, rows_or_err = fetch_d365_cases()
    if not ok:
        _record_meta(False, 0, str(rows_or_err), triggered_by, started)
        return False, str(rows_or_err), 0
    rows: list[dict] = rows_or_err  # type: ignore[assignment]

    # 3. Upsert
    inserted = 0
    updated = 0
    skipped = 0
    for r in rows:
        case_id = _safe_str(r.get("CaseId"))
        if not case_id:
            skipped += 1
            continue
        kwargs = _row_to_case_kwargs(r, emp_map)
        existing = D365Case.query.filter_by(case_id=case_id).first()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            updated += 1
        else:
            db.session.add(D365Case(**kwargs))
            inserted += 1

    db.session.commit()

    msg = (
        f"Synced {len(rows)} D365 rows "
        f"(inserted={inserted}, updated={updated}, skipped={skipped}, "
        f"employees={len(emp_map)})."
    )
    _record_meta(True, inserted + updated, msg, triggered_by, started)
    return True, msg, inserted + updated


def _record_meta(ok: bool, rows: int, message: str, triggered_by: str, started: datetime):
    """Insert/update the single 'd365_cases' SyncMeta row."""
    meta = SyncMeta.query.filter_by(name="d365_cases").first()
    if meta is None:
        meta = SyncMeta(name="d365_cases")
        db.session.add(meta)
    meta.last_run_at = datetime.utcnow()
    meta.last_status = "ok" if ok else "error"
    meta.rows_synced = rows
    meta.last_message = message[:1000]
    meta.triggered_by = (triggered_by or "")[:200]
    db.session.commit()


def get_last_sync_meta() -> SyncMeta | None:
    return SyncMeta.query.filter_by(name="d365_cases").first()
