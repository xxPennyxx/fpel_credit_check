"""
FPEL Renewable Capital - Credit Check (Dummy App)
Main Flask application: routes, business logic, REST endpoints.

NOTE: This is a teaching / demo app. All names, companies, ratings, financial
figures are fictional and used only to illustrate the SOP workflow.
"""

import os
from datetime import datetime, date, timedelta

from dotenv import load_dotenv
load_dotenv()  # must run before we read os.environ below

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, abort, session, send_from_directory, current_app,
)
from werkzeug.utils import secure_filename

from models import db, Company, FinancialRecord, Case, CreditCheck, AuditLog, User, D365Case, SyncMeta
from auth import (
    auth_bp, login_required, admin_required, bd_required, rc_required,
    _resolve_role_for_email, is_admin_role, can_create_case, is_rc_role,
    ASSIGNABLE_ROLES, ROLE_LABELS,
)
from email_service import send_mail
from d365_sync import sync_d365_cases, get_last_sync_meta, resolve_employee_id
from services.scoring import score_credit_check
from services.credit_report import generate as generate_credit_report


# ---------- App factory ----------
def create_app():
    app = Flask(__name__)

    # Pull config from environment (.env)
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dummy-key-do-not-use-in-prod")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///fpel_credit_check.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Session hardening
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.permanent_session_lifetime = timedelta(hours=8)

    # Case attachments (financial statements / electricity bills)
    app.config["UPLOAD_FOLDER"] = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(app.instance_path, "uploads")
    )
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB cap
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # Auth-related env, made available via app.config
    for key in (
        "MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_AUTHORITY", "MICROSOFT_GRAPH_SCOPE",
        "SUPER_ADMIN_EMAIL", "DEFAULT_ADMIN_EMAILS",
        "ALLOWED_EMAIL_DOMAIN", "GRAPH_SENDER_UPN",
        "SESSION_COOKIE_SECURE", "SESSION_COOKIE_SAMESITE",
    ):
        if os.environ.get(key):
            app.config[key] = os.environ[key]

    # Honor SESSION_COOKIE_SECURE from .env (true for prod over HTTPS, false locally)
    sec = (os.environ.get("SESSION_COOKIE_SECURE") or "").strip().lower()
    if sec in ("true", "1", "yes"):
        app.config["SESSION_COOKIE_SECURE"] = True

    db.init_app(app)
    app.register_blueprint(auth_bp)
    register_routes(app)

    # Expose the current user object + datetime helper in every template
    @app.context_processor
    def inject_globals():
        u = session.get("user")
        role = (u or {}).get("role")
        return {
            "current_user": u,
            "datetime": datetime,
            # Permission flags for templates
            "perm_is_admin": is_admin_role(role),
            "perm_can_create": can_create_case(role),
            "perm_is_rc": is_rc_role(role),
        }

    with app.app_context():
        db.create_all()
        _ensure_schema()
        if Company.query.count() == 0:
            from seed import load_dummy_data
            load_dummy_data()
        _ensure_seed_admins()

    return app


def _ensure_schema():
    """Lightweight migration: add columns introduced after the table was first
    created (db.create_all() does not ALTER existing tables). Safe + idempotent.
    """
    from sqlalchemy import text, inspect
    inspector = inspect(db.engine)
    try:
        cols = {c["name"] for c in inspector.get_columns("d365_cases")}
    except Exception:
        return
    add = {
        "attachment_filename": "VARCHAR(255)",
        "attachment_original_name": "VARCHAR(255)",
        "credit_report_filename": "VARCHAR(255)",
        "credit_decision": "VARCHAR(40)",
        "credit_score": "FLOAT",
    }
    for name, ddl in add.items():
        if name not in cols:
            db.session.execute(
                text(f"ALTER TABLE d365_cases ADD COLUMN {name} {ddl}")
            )
    db.session.commit()


def _ensure_seed_admins():
    """
    Create User rows for the super-admin and default admin(s) so the Admin
    portal shows them even before they sign in for the first time. Their
    roles are re-asserted on every login (see auth._resolve_role_for_email).

    Display names are hard-coded to the real names of the seeded accounts;
    once a user signs in via SSO the name from the ID-token claims takes
    over (so anyone with a different Entra ID display name will be updated
    automatically).
    """
    # Known default display names for the two seeded accounts.
    DEFAULT_DISPLAY_NAMES = {
        "ai@fourthpartner.co": "AI FPEL",
        "vaidehi.sridhar@fourthpartner.co": "Vaidehi Sridhar",
    }

    # One-time cleanup: remove the demo BD/RC accounts that were previously
    # seeded for illustration. Safe + idempotent (no-op once they're gone).
    _DUMMY_EMAILS = [
        "aarav.sharma@fourthpartner.co",
        "neha.kapoor@fourthpartner.co",
        "rohan.mehta@fourthpartner.co",
        "priya.iyer@fourthpartner.co",
        "karthik.nair@fourthpartner.co",
    ]
    for _e in _DUMMY_EMAILS:
        _u = User.query.filter_by(email=_e).first()
        if _u is not None:
            db.session.delete(_u)
    db.session.commit()

    seeds = [
        (os.environ.get("SUPER_ADMIN_EMAIL", "ai@fourthpartner.co").lower(), "super_admin"),
    ]
    for e in (os.environ.get("DEFAULT_ADMIN_EMAILS", "vaidehi.sridhar@fourthpartner.co") or "").split(","):
        e = e.strip().lower()
        if e:
            seeds.append((e, "admin"))

    for email, role in seeds:
        default_name = (
            DEFAULT_DISPLAY_NAMES.get(email)
            or email.split("@")[0].replace(".", " ").title()
        )
        u = User.query.filter_by(email=email).first()
        if u is None:
            db.session.add(User(
                email=email, role=role,
                display_name=default_name,
                login_count=0,
            ))
        else:
            u.role = _resolve_role_for_email(email, u.role)
            # Backfill display_name only if it's still the auto-generated
            # placeholder (so we don't clobber a real name from SSO claims).
            placeholder = email.split("@")[0].replace(".", " ").title()
            if email in DEFAULT_DISPLAY_NAMES and (
                not u.display_name or u.display_name == placeholder
            ):
                u.display_name = default_name
    db.session.commit()


# ---------- Scoring helper (dummy arithmetic) ----------
def compute_score_and_rating(cc):
    """Very simple scoring matrix - purely illustrative."""
    score = 0
    score += min(max((cc.revenue_growth_pct or 0), 0), 30) * 0.8
    score += min(max((cc.ebitda_margin_pct or 0), 0), 40) * 0.6
    score += max(40 - (cc.debt_equity or 0) * 15, 0)
    score += min((cc.interest_coverage or 0) * 4, 40)
    score = round(score, 1)

    if score >= 80:
        rating = "4PEL AA+"
    elif score >= 70:
        rating = "4PEL AA"
    elif score >= 60:
        rating = "4PEL A"
    elif score >= 50:
        rating = "4PEL BBB+"
    elif score >= 40:
        rating = "4PEL BBB"
    else:
        rating = "4PEL BBB-"
    return score, rating


def _is_admin_user(u):
    return bool(u) and u.get("role") in ("admin", "super_admin")


def _next_cs_case_id():
    """Return the next available serial case id in the format CS-XXXXXXX.

    Scans existing CS- ids, takes the highest numeric suffix, and returns the
    next one zero-padded to 7 digits.
    """
    prefix = "CS-"
    max_n = 0
    for (cid,) in db.session.query(D365Case.case_id).filter(
        D365Case.case_id.like("CS-%")
    ).all():
        suffix = (cid or "")[len(prefix):]
        if suffix.isdigit():
            max_n = max(max_n, int(suffix))
    return f"{prefix}{max_n + 1:07d}"


ALLOWED_ATTACHMENT_EXT = {"pdf", "png", "jpg", "jpeg", "xlsx", "xls", "csv", "doc", "docx"}


def _save_attachment(file_storage, case_id):
    """Validate and save an uploaded attachment. Returns (stored_name, original_name)
    or (None, None) if no/invalid file. Raises ValueError on disallowed type."""
    if not file_storage or not file_storage.filename:
        return None, None
    original = file_storage.filename
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_ATTACHMENT_EXT:
        raise ValueError(
            "Unsupported file type. Allowed: " + ", ".join(sorted(ALLOWED_ATTACHMENT_EXT))
        )
    safe_case = secure_filename(case_id) or "case"
    stored = f"{safe_case}_{secure_filename(original)}"
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], stored)
    file_storage.save(path)
    return stored, original


def _can_create_case(u):
    return bool(u) and can_create_case(u.get("role"))


def _is_rc_user(u):
    return bool(u) and is_rc_role(u.get("role"))


# ---------- Routes ----------
def register_routes(app):

    # Public home page (landing). Signed-in users get bounced to the dashboard.
    @app.route("/")
    def home():
        if session.get("user"):
            return redirect(url_for("dashboard"))
        return render_template("home.html")

    # Authenticated dashboard — D365 credit cases live here
    @app.route("/dashboard")
    @login_required
    def dashboard():
        rows = (
            D365Case.query
            .order_by(D365Case.last_synced_at.desc(), D365Case.case_id.asc())
            .all()
        )

        # KPIs are derived from the synced D365 set
        total = len(rows)
        open_count = sum(1 for r in rows if (r.status or "").lower() not in ("closed", "resolved", ""))
        closed_count = sum(1 for r in rows if (r.status or "").lower() in ("closed", "resolved"))
        with_rating = sum(1 for r in rows if (r.internal_credit_rating or "").strip())

        statuses = sorted({(r.status or "").strip() for r in rows if (r.status or "").strip()})

        return render_template(
            "dashboard.html",
            rows=rows,
            kpi_total=total,
            kpi_open=open_count,
            kpi_closed=closed_count,
            kpi_with_rating=with_rating,
            statuses=statuses,
            d365_meta=get_last_sync_meta(),
        )

    @app.route("/d365/sync", methods=["POST"])
    @login_required
    def d365_sync_now():
        """Manually pull the latest FPELDetailBases rows from D365."""
        actor = (session.get("user") or {})
        triggered_by = f"{actor.get('name','?')} <{actor.get('email','?')}>"
        try:
            ok, msg, n = sync_d365_cases(triggered_by=triggered_by)
            flash(msg, "success" if ok else "error")
        except Exception as e:
            flash(f"D365 sync failed: {e}", "error")
        return redirect(request.args.get("next") or url_for("dashboard"))

    @app.route("/d365/cases/<path:case_id>")
    @login_required
    def d365_case_detail(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()
        return render_template("d365_case_detail.html", case=case)

    @app.route("/d365/cases/<path:case_id>/attachment")
    @login_required
    def d365_case_attachment(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()
        if not case.attachment_filename:
            abort(404)
        return send_from_directory(
            current_app.config["UPLOAD_FOLDER"],
            case.attachment_filename,
            as_attachment=True,
            download_name=case.attachment_original_name or case.attachment_filename,
        )

    # ---------- Edit a case (BD team + Admin only) ----------
    @app.route("/d365/cases/<path:case_id>/edit", methods=["GET", "POST"])
    @bd_required
    def d365_case_edit(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()
        bd_users = (
            User.query.filter_by(role="bd", is_active=True)
            .order_by(User.display_name.asc())
            .all()
        )

        def _f(name):
            return (request.form.get(name) or "").strip()

        def _num(name):
            v = (request.form.get(name) or "").strip()
            try:
                return float(v) if v != "" else None
            except ValueError:
                return None

        if request.method == "POST":
            responsible_person = _f("responsible_person")
            entity_name = _f("entity_name")
            status = _f("status")

            errors = []
            if not responsible_person:
                errors.append("Responsible Person (BD) is mandatory.")
            if not entity_name:
                errors.append("Entity Name is mandatory.")
            if not status:
                errors.append("Status is mandatory.")
            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template("d365_case_edit.html", case=case, bd_users=bd_users)

            # Re-resolve Employee ID if the responsible person changed.
            if responsible_person != (case.responsible_person or ""):
                selected = next(
                    (b for b in bd_users
                     if (b.display_name or b.email) == responsible_person),
                    None,
                )
                case.responsible_person_personnel_number = resolve_employee_id(
                    name=responsible_person,
                    email=(selected.email if selected else None),
                ) or case.responsible_person_personnel_number

            # Optional replacement attachment
            try:
                stored_name, original_name = _save_attachment(
                    request.files.get("attachment"), case.case_id
                )
            except ValueError as ve:
                flash(str(ve), "error")
                return render_template("d365_case_edit.html", case=case, bd_users=bd_users)
            if stored_name:
                case.attachment_filename = stored_name
                case.attachment_original_name = original_name

            case.responsible_person = responsible_person
            case.status = status
            case.entity_name = entity_name
            case.entity_code = _f("entity_code")
            case.state = _f("state")
            case.location = _f("location")
            case.park_name = _f("park_name")
            case.segment = _f("segment")
            case.sub_segment = _f("sub_segment")
            case.type_of_project = _f("type_of_project")
            case.project_category = _f("project_category")
            case.case_category = _f("case_category")
            case.solar_capacity = _num("solar_capacity")
            case.wind_capacity = _num("wind_capacity")
            case.internal_credit_rating = _f("internal_credit_rating")
            case.external_credit_rating = _f("external_credit_rating")
            case.external_credit_rating_agency = _f("external_credit_rating_agency")
            case.other_rating_agency = _f("other_rating_agency")
            case.credit_check_month = _f("credit_check_month")
            case.ppa_request_date_bd = _f("ppa_request_date_bd")
            case.date_for_dua = _f("date_for_dua")
            case.description = _f("description")
            case.memo = _f("memo")

            db.session.commit()
            flash(f"Case {case.case_id} updated successfully.", "success")
            return redirect(url_for("d365_case_detail", case_id=case.case_id))

        return render_template("d365_case_edit.html", case=case, bd_users=bd_users)

    # ---------- RC action on a case (RC + Admin only) ----------
    @app.route("/d365/cases/<path:case_id>/action", methods=["POST"])
    @rc_required
    def d365_case_action(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()

        def _f(name):
            return (request.form.get(name) or "").strip()

        new_status = _f("status")
        if new_status:
            case.status = new_status

        # RC working fields
        reply = _f("reply_rc_to_bd")
        if reply:
            case.reply_rc_to_bd = reply

        internal_rating = _f("internal_credit_rating")
        if internal_rating:
            case.internal_credit_rating = internal_rating

        memo = _f("memo")
        if memo:
            case.memo = memo

        db.session.commit()
        actor = (session.get("user") or {}).get("name") or "RC"
        flash(f"Case {case.case_id} updated by {actor} (RC).", "success")
        return redirect(url_for("d365_case_detail", case_id=case.case_id))

    # ---------- Generate RC credit-check report (RC + Admin only) ----------
    @app.route("/d365/cases/<path:case_id>/credit-report", methods=["GET", "POST"])
    @rc_required
    def d365_credit_report(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()

        def _f(name):
            return (request.form.get(name) or "").strip()

        if request.method == "GET":
            return render_template("d365_credit_report.html", case=case)

        # 1. Apply the deterministic 4PEL scoring matrix
        scoring_inputs = {
            "long_term_rating": _f("long_term_rating") or case.external_credit_rating,
            "net_worth_cr": _f("net_worth_cr"),
            "turnover_cr": _f("turnover_cr"),
            "pat_cr": _f("pat_cr"),
            "market_cap_cr": _f("market_cap_cr"),
            "debt_equity": _f("debt_equity"),
            "interest_coverage": _f("interest_coverage"),
            "revenue_growth_pct": _f("revenue_growth_pct"),
            "ebitda_margin_pct": _f("ebitda_margin_pct"),
            "pat_margin_pct": _f("pat_margin_pct"),
            "cash_profit_pct": _f("cash_profit_pct"),
            "industry": _f("industry"),
            "directors_rating": _f("directors_rating") or 3,
            "debt_free": request.form.get("debt_free") == "on",
            "listed": request.form.get("listed") == "on",
        }
        result = score_credit_check(scoring_inputs)

        # 2. Assemble the report JSON (v2 schema expected by the generator)
        def _split_lines(name):
            return [ln.strip() for ln in (request.form.get(name) or "").splitlines() if ln.strip()]

        cap_bits = []
        if case.solar_capacity:
            cap_bits.append(f"{case.solar_capacity:.2f} MWp Solar")
        if case.wind_capacity:
            cap_bits.append(f"{case.wind_capacity:.2f} MW Wind")
        requirement = " + ".join(cap_bits)
        if case.state:
            requirement = f"{requirement} ({case.state})" if requirement else case.state

        fin_rows = []
        for label, key in [
            ("Revenue from Operations", "turnover_cr"),
            ("EBITDA Margin (%)", "ebitda_margin_pct"),
            ("PAT", "pat_cr"),
            ("PAT Margin (%)", "pat_margin_pct"),
            ("Net Worth", "net_worth_cr"),
            ("Debt / Equity (D/E)", "debt_equity"),
        ]:
            val = _f(key)
            if val:
                fin_rows.append({"values": [label, val], "section_header": False})

        report_data = {
            "company_name": case.entity_name or case.case_id,
            "report_date": datetime.utcnow().strftime("%d-%m-%Y"),
            "incorporation_date": _f("incorporation_date") or "—",
            "nature_of_business": _f("nature_of_business") or "—",
            "industry": _f("industry") or "—",
            "requirement": requirement or "—",
            "screening": result["screening"],
            "brief_profile_paragraphs": _split_lines("brief_profile") or ["—"],
            "financials": {"columns": ["Particulars", "FY2025"], "rows": fin_rows},
            "financial_analysis_sections": [],
            "strengths": [{"title": "Strength", "detail": s} for s in _split_lines("strengths")],
            "weaknesses": [{"title": "Risk", "detail": w} for w in _split_lines("weaknesses")],
            "latest_updates": [],
            "internal_rating": result["rating"],
            "decision": result["decision"],
            "credit_view": _f("credit_view") or "—",
            "conditions": _split_lines("conditions"),
            "scoring": {
                "total_score": result["total_score"],
                "max_score": result["max_score"],
                "rating": result["rating"],
                "parameters": result["parameters"],
            },
        }

        # 3. Generate the Word document into the uploads folder
        safe_case = secure_filename(case.case_id) or "case"
        stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        report_name = f"CreditReport_{safe_case}_{stamp}.docx"
        out_path = os.path.join(current_app.config["UPLOAD_FOLDER"], report_name)
        try:
            generate_credit_report(report_data, out_path)
        except Exception as e:
            flash(f"Report generation failed: {e}", "error")
            return render_template("d365_credit_report.html", case=case)

        # 4. Persist outcome on the case
        case.credit_report_filename = report_name
        case.internal_credit_rating = result["rating"]
        case.credit_decision = result["decision"]
        case.credit_score = result["total_score"]
        db.session.commit()

        flash(
            f"Credit report generated: {result['rating']} — {result['decision']} "
            f"(score {result['total_score']}/{result['max_score']}). "
            "This is a draft — review all figures before sending to BD.",
            "success",
        )
        return redirect(url_for("d365_case_detail", case_id=case.case_id))

    @app.route("/d365/cases/<path:case_id>/credit-report/download")
    @login_required
    def d365_credit_report_download(case_id):
        case = D365Case.query.filter_by(case_id=case_id).first_or_404()
        if not case.credit_report_filename:
            abort(404)
        return send_from_directory(
            current_app.config["UPLOAD_FOLDER"],
            case.credit_report_filename,
            as_attachment=True,
            download_name=f"CreditReport_{secure_filename(case.case_id)}.docx",
        )

    # ---------- Cases (BD module) ----------
    @app.route("/cases")
    @login_required
    def cases_list():
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "")
        query = Case.query.join(Company)
        if q:
            query = query.filter(Company.name.ilike(f"%{q}%"))
        if status:
            query = query.filter(Case.status == status)
        cases = query.order_by(Case.raised_on.desc()).all()
        return render_template("cases_list.html", cases=cases, q=q, status=status)

    @app.route("/cases/new", methods=["GET", "POST"])
    @bd_required
    def new_case():
        # BD users (and admins) raise new cases; the "Responsible Person"
        # dropdown is populated from active users holding the BD role.
        bd_users = (
            User.query.filter_by(role="bd", is_active=True)
            .order_by(User.display_name.asc())
            .all()
        )

        def _f(name):
            return (request.form.get(name) or "").strip()

        def _num(name):
            v = (request.form.get(name) or "").strip()
            try:
                return float(v) if v != "" else None
            except ValueError:
                return None

        if request.method == "POST":
            responsible_person = _f("responsible_person")
            entity_name = _f("entity_name")
            status = _f("status")

            errors = []
            if not responsible_person:
                errors.append("Responsible Person (BD) is mandatory.")
            if not entity_name:
                errors.append("Entity Name is mandatory.")
            if not status:
                errors.append("Status is mandatory.")

            if errors:
                for e in errors:
                    flash(e, "error")
                return render_template(
                    "new_case.html", form=request.form,
                    bd_users=bd_users, next_case_id=_next_cs_case_id(),
                )

            # Auto-assign the next available serial case id (CS-XXXXXXX).
            # Recomputed server-side (the form field is display-only / disabled).
            case_id = _next_cs_case_id()
            while D365Case.query.filter_by(case_id=case_id).first() is not None:
                # Extremely unlikely race; bump to the following serial.
                n = int(case_id[3:]) + 1
                case_id = f"CS-{n:07d}"

            # Resolve the Employee ID (PersonnelNumber) for the selected BD user
            # from the D365 Employees entity, matching on name/email. Best-effort:
            # stays None if D365 is unreachable or the person isn't matched.
            selected = next(
                (b for b in bd_users
                 if (b.display_name or b.email) == responsible_person),
                None,
            )
            personnel_number = resolve_employee_id(
                name=responsible_person,
                email=(selected.email if selected else None),
            )

            # Optional attachment (financial statement / electricity bill)
            try:
                stored_name, original_name = _save_attachment(
                    request.files.get("attachment"), case_id
                )
            except ValueError as ve:
                flash(str(ve), "error")
                return render_template(
                    "new_case.html", form=request.form,
                    bd_users=bd_users, next_case_id=_next_cs_case_id(),
                )

            case = D365Case(
                case_id=case_id,
                responsible_person=responsible_person,
                responsible_person_personnel_number=personnel_number,
                status=status,
                entity_name=entity_name,
                entity_code=_f("entity_code"),
                state=_f("state"),
                location=_f("location"),
                park_name=_f("park_name"),
                segment=_f("segment"),
                sub_segment=_f("sub_segment"),
                type_of_project=_f("type_of_project"),
                project_category=_f("project_category"),
                case_category=_f("case_category"),
                solar_capacity=_num("solar_capacity"),
                wind_capacity=_num("wind_capacity"),
                internal_credit_rating=_f("internal_credit_rating"),
                external_credit_rating=_f("external_credit_rating"),
                external_credit_rating_agency=_f("external_credit_rating_agency"),
                other_rating_agency=_f("other_rating_agency"),
                credit_check_month=_f("credit_check_month"),
                ppa_request_date_bd=_f("ppa_request_date_bd"),
                date_for_dua=_f("date_for_dua"),
                description=_f("description"),
                memo=_f("memo"),
                attachment_filename=stored_name,
                attachment_original_name=original_name,
            )
            db.session.add(case)
            db.session.commit()
            flash(f"Case {case_id} created successfully.", "success")
            return redirect(url_for("d365_case_detail", case_id=case.case_id))

        return render_template(
            "new_case.html", form={},
            bd_users=bd_users, next_case_id=_next_cs_case_id(),
        )

    @app.route("/cases/<int:case_id>")
    @login_required
    def case_detail(case_id):
        case = Case.query.get_or_404(case_id)
        return render_template("case_detail.html", case=case)

    # ---------- Credit Check (RC module) ----------
    @app.route("/cases/<int:case_id>/credit-check", methods=["GET", "POST"])
    @login_required
    def credit_check(case_id):
        if request.method == "POST" and not _is_admin_user(session.get("user")):
            return render_template("unauthorized.html"), 403

        case = Case.query.get_or_404(case_id)
        maker_name = (session.get("user") or {}).get("name") or "Priya Iyer (RC)"
        cc = case.credit_check or CreditCheck(case=case, maker=maker_name)
        if not case.credit_check:
            db.session.add(cc)
            case.status = "Under Review"
            db.session.commit()

        if request.method == "POST":
            cc.brief_profile = request.form.get("brief_profile")
            cc.financial_analysis = request.form.get("financial_analysis")
            cc.strengths = request.form.get("strengths")
            cc.weaknesses = request.form.get("weaknesses")
            cc.latest_updates = request.form.get("latest_updates")
            cc.revenue_growth_pct = float(request.form.get("revenue_growth_pct") or 0)
            cc.ebitda_margin_pct = float(request.form.get("ebitda_margin_pct") or 0)
            cc.debt_equity = float(request.form.get("debt_equity") or 0)
            cc.interest_coverage = float(request.form.get("interest_coverage") or 0)
            cc.decision = request.form.get("decision")
            cc.decision_conditions = request.form.get("decision_conditions")

            cc.composite_score, cc.internal_rating = compute_score_and_rating(cc)

            cc.rc_head_required = (
                case.total_mwp > 5
                or (cc.internal_rating or "").startswith("4PEL A")
                or (cc.internal_rating or "").startswith("4PEL BBB")
            )

            db.session.add(AuditLog(
                credit_check=cc,
                actor=cc.maker or "Maker",
                action="Updated credit check",
                note=f"Rating: {cc.internal_rating} | Decision: {cc.decision}",
            ))
            case.status = "Maker Done"
            db.session.commit()
            flash("Credit check saved. Score & rating recomputed.", "success")
            return redirect(url_for("credit_check", case_id=case.id))

        return render_template("credit_check.html", case=case, cc=cc)

    @app.route("/cases/<int:case_id>/dispatch", methods=["POST"])
    @admin_required
    def dispatch(case_id):
        case = Case.query.get_or_404(case_id)
        if not case.credit_check or not case.credit_check.decision:
            flash("Cannot dispatch - decision is missing.", "error")
            return redirect(url_for("credit_check", case_id=case.id))
        case.status = "Dispatched"
        db.session.add(AuditLog(
            credit_check=case.credit_check,
            actor=case.credit_check.maker or "Maker",
            action="Dispatched to BD",
        ))
        db.session.commit()
        flash("Credit check dispatched to BD team.", "success")
        return redirect(url_for("case_detail", case_id=case.id))

    # ---------- One Pager module (RC only) ----------
    # Shows all Planned + In Process D365 cases with an option to generate the
    # RC credit-check report. Restricted to RC (and Admin) via @rc_required.
    @app.route("/one-pager")
    @rc_required
    def one_pager_list():
        active_statuses = ("planned", "inprocess", "in process", "in progress")
        rows = (
            D365Case.query
            .order_by(D365Case.last_synced_at.desc(), D365Case.case_id.asc())
            .all()
        )
        cases = [r for r in rows if (r.status or "").strip().lower() in active_statuses]
        return render_template("one_pager.html", cases=cases)

    @app.route("/one-pager/<int:company_id>")
    @login_required
    def one_pager_view(company_id):
        company = Company.query.get_or_404(company_id)
        return render_template("one_pager_view.html", company=company)

    # ---------- Mock Instafinancials API ----------
    @app.route("/api/companies/search")
    @login_required
    def api_company_search():
        q = request.args.get("q", "").strip().lower()
        results = []
        if q:
            for c in Company.query.all():
                if q in c.name.lower():
                    results.append({
                        "id": c.id, "name": c.name,
                        "industry": c.industry, "rating": c.external_rating,
                    })
        return jsonify(results)

    # ================================================================
    # ADMIN PORTAL
    # ================================================================
    @app.route("/admin/users")
    @admin_required
    def admin_users():
        users = User.query.order_by(
            User.login_count.desc(), User.last_login_at.desc(), User.email.asc()
        ).all()

        # Display names are captured from the SSO token claims at first login
        # (see auth.callback). No Microsoft Graph lookup is performed here.

        return render_template(
            "admin_users.html",
            users=users,
            super_admin_email=os.environ.get("SUPER_ADMIN_EMAIL", "ai@fourthpartner.co").lower(),
            assignable_roles=ASSIGNABLE_ROLES,
            role_labels=ROLE_LABELS,
        )

    @app.route("/admin/users/<int:user_id>/role", methods=["POST"])
    @admin_required
    def admin_set_role(user_id):
        actor = session.get("user") or {}
        target = User.query.get_or_404(user_id)

        new_role = request.form.get("role", "").strip()
        if new_role not in ("bd", "rc", "viewer", "admin", "super_admin"):
            flash("Invalid role.", "error")
            return redirect(url_for("admin_users"))

        # Block self-demotion: an admin/super_admin cannot change their own role
        # to anything lower (and we keep it simple by blocking ANY self-change).
        if actor.get("id") == target.id and new_role != target.role:
            flash("You cannot change your own role. Ask another admin to do it.", "error")
            return redirect(url_for("admin_users"))

        if new_role == "super_admin" and actor.get("role") != "super_admin":
            flash("Only the super admin can promote to super_admin.", "error")
            return redirect(url_for("admin_users"))

        super_email = os.environ.get("SUPER_ADMIN_EMAIL", "ai@fourthpartner.co").lower()
        if target.email == super_email and new_role != "super_admin":
            flash("The super admin's role is fixed and cannot be changed.", "error")
            return redirect(url_for("admin_users"))

        target.role = new_role
        db.session.commit()
        flash(f"{target.email} is now {new_role}.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
    @admin_required
    def admin_toggle_active(user_id):
        actor = session.get("user") or {}
        target = User.query.get_or_404(user_id)

        # Block self-deactivation
        if actor.get("id") == target.id:
            flash("You cannot deactivate your own account.", "error")
            return redirect(url_for("admin_users"))

        super_email = os.environ.get("SUPER_ADMIN_EMAIL", "ai@fourthpartner.co").lower()
        if target.email == super_email:
            flash("Cannot deactivate the super admin.", "error")
            return redirect(url_for("admin_users"))
        target.is_active = not target.is_active
        db.session.commit()
        flash(f"{target.email} is now {'active' if target.is_active else 'inactive'}.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/test-email", methods=["POST"])
    @admin_required
    def admin_test_email():
        actor = session.get("user") or {}
        recipient = (request.form.get("recipient") or actor.get("email") or "").strip()
        if not recipient:
            flash("Please provide a recipient email address.", "error")
            return redirect(url_for("admin_users"))

        subject = "FPEL Credit Check - Microsoft Graph test email"
        body = render_template(
            "emails/test_email.html",
            actor_name=actor.get("name") or "FPEL Administrator",
            actor_email=actor.get("email") or "",
            actor_role=actor.get("role") or "admin",
            triggered_at=datetime.utcnow().strftime("%d-%m-%Y %H:%M:%S"),
            sender_upn=os.environ.get("GRAPH_SENDER_UPN", "noreply@fourthpartner.co"),
        )
        try:
            send_mail(recipient, subject, body)
            flash(f"Test email sent to {recipient} via Microsoft Graph.", "success")
        except Exception as e:
            flash(f"Test email failed: {e}", "error")
        return redirect(url_for("admin_users"))


if __name__ == "__main__":
    app = create_app()

    host = "localhost"
    port = int(os.environ.get("PORT", 5000))
    url = f"http://{host}:{port}"

    # Friendly startup banner. With Werkzeug's reloader on, only the child
    # worker process has WERKZEUG_RUN_MAIN set to "true" — print there so
    # the banner appears exactly once (not in the supervising parent).
    is_worker = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    is_no_reload = os.environ.get("WERKZEUG_RUN_MAIN") is None  # reloader disabled
    if is_worker or is_no_reload:
        print()
    # Reloader picks up code changes automatically. WERKZEUG_RUN_MAIN is set
    # on the reload-spawned child process, so the banner only prints once.
    app.run(host=host, port=port, debug=True, use_reloader=True)
# end of file

