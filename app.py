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
    flash, jsonify, abort, session,
)

from models import db, Company, FinancialRecord, Case, CreditCheck, AuditLog, User, D365Case, SyncMeta
from auth import auth_bp, login_required, admin_required, _resolve_role_for_email
from email_service import send_mail
from d365_sync import sync_d365_cases, get_last_sync_meta


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
        return {
            "current_user": session.get("user"),
            "datetime": datetime,
        }

    with app.app_context():
        db.create_all()
        if Company.query.count() == 0:
            from seed import load_dummy_data
            load_dummy_data()
        _ensure_seed_admins()

    return app


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
    @login_required
    def new_case():
        if request.method == "POST" and not _is_admin_user(session.get("user")):
            return render_template("unauthorized.html"), 403

        if request.method == "POST":
            company_name = request.form.get("company_name", "").strip()
            park = request.form.get("park_name", "").strip()
            state = request.form.get("state_name", "").strip()
            solar = float(request.form.get("solar_mwp") or 0)
            wind = float(request.form.get("wind_mw") or 0)
            consumption = request.form.get("consumption_summary", "").strip()

            errors = []
            if not company_name: errors.append("Company name is mandatory.")
            if not park: errors.append("Park name is mandatory.")
            if not state: errors.append("State is mandatory.")
            if solar <= 0 and wind <= 0: errors.append("Capacity (Solar or Wind) must be > 0.")
            if not consumption: errors.append("Consumption analysis summary is mandatory.")

            if errors:
                for e in errors: flash(e, "error")
                return render_template("new_case.html", form=request.form)

            company = Company.query.filter_by(name=company_name).first()
            if not company:
                company = Company(
                    name=company_name,
                    industry=request.form.get("industry") or "Manufacturing",
                    external_rating_agency=request.form.get("rating_agency"),
                    external_rating=request.form.get("rating"),
                )
                db.session.add(company)
                db.session.flush()
                db.session.add(FinancialRecord(
                    company_id=company.id, fiscal_year="FY2025",
                    revenue_cr=500.0, ebitda_cr=80.0, pat_cr=35.0,
                    networth_cr=350.0, debt_cr=180.0, interest_cr=20.0,
                ))

            case_ref = f"CC-2026-{Case.query.count() + 1:03d}"
            raised_by = (session.get("user") or {}).get("name") or "Aarav Sharma (BD)"
            case = Case(
                case_ref=case_ref, company=company,
                solar_mwp=solar, wind_mw=wind,
                park_name=park, state_name=state,
                consumption_summary=consumption,
                raised_by=raised_by,
                status="Submitted",
            )
            db.session.add(case)
            db.session.commit()
            flash(f"Case {case_ref} submitted successfully.", "success")
            return redirect(url_for("case_detail", case_id=case.id))

        return render_template("new_case.html", form={})

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

    # ---------- One Pager module ----------
    @app.route("/one-pager")
    @login_required
    def one_pager_list():
        offtakers = Company.query.filter_by(is_existing_offtaker=True).all()
        return render_template("one_pager.html", offtakers=offtakers)

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
        )

    @app.route("/admin/users/<int:user_id>/role", methods=["POST"])
    @admin_required
    def admin_set_role(user_id):
        actor = session.get("user") or {}
        target = User.query.get_or_404(user_id)

        new_role = request.form.get("role", "").strip()
        if new_role not in ("viewer", "admin", "super_admin"):
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

