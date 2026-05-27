"""
Microsoft SSO (Azure AD / Entra ID) via MSAL.

Flow:
  1. /auth/login            -> redirects user to Microsoft sign-in page
  2. /auth/callback         -> Microsoft redirects back here with a code
                               -> we exchange code for tokens, read claims,
                                  upsert the User row, stash a tiny session.
  3. /auth/logout           -> clears the local session and bounces to MS logout.

Roles:
  - super_admin : ai@fourthpartner.co (configurable via SUPER_ADMIN_EMAIL)
  - admin       : vaidehi.sridhar@fourthpartner.co (configurable via DEFAULT_ADMIN_EMAILS)
                  plus anyone the super_admin / admin promotes from the Admin portal.
  - viewer      : everyone else, by default.
"""

import os
from datetime import datetime
from functools import wraps

import msal
from flask import (
    Blueprint, current_app, redirect, render_template,
    request, session, url_for, flash, abort,
)

from models import db, User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ---------------------------------------------------------------------------
# MSAL plumbing
# ---------------------------------------------------------------------------
def _cfg(key: str, default: str = "") -> str:
    return current_app.config.get(key) or os.environ.get(key, default)


def _authority() -> str:
    # Prefer the explicit MICROSOFT_AUTHORITY if set, else build from tenant id
    explicit = _cfg("MICROSOFT_AUTHORITY")
    if explicit:
        return explicit
    tenant = _cfg("MICROSOFT_TENANT_ID", "common")
    return f"https://login.microsoftonline.com/{tenant}"


def _build_msal_app(cache=None) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=_cfg("MICROSOFT_CLIENT_ID"),
        client_credential=_cfg("MICROSOFT_CLIENT_SECRET"),
        authority=_authority(),
        token_cache=cache,
    )


# Delegated scopes used at the *login* step only.
# Mail.Send used by the admin "test email" button is an APPLICATION permission
# fetched separately in email_service.py via the client-credentials flow.
LOGIN_SCOPES = ["User.Read"]


# ---------------------------------------------------------------------------
# Role bootstrapping helpers
# ---------------------------------------------------------------------------
def _super_admin_email() -> str:
    return (_cfg("SUPER_ADMIN_EMAIL", "ai@fourthpartner.co") or "").strip().lower()


def _default_admin_emails() -> set[str]:
    raw = _cfg("DEFAULT_ADMIN_EMAILS", "vaidehi.sridhar@fourthpartner.co") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _allowed_domain() -> str | None:
    """Optional: restrict sign-in to a single email domain (e.g. fourthpartner.co)."""
    d = (_cfg("ALLOWED_EMAIL_DOMAIN", "") or "").strip().lower()
    return d or None


def _resolve_role_for_email(email: str, existing_role: str | None) -> str:
    """
    Hard rules:
      - super admin email is ALWAYS super_admin
      - default admin emails are admin UNLESS they've already been promoted to super_admin
      - otherwise keep whatever role they already had, defaulting to viewer
    """
    email_l = (email or "").lower()
    if email_l == _super_admin_email():
        return "super_admin"
    if email_l in _default_admin_emails():
        return existing_role if existing_role == "super_admin" else "admin"
    return existing_role or "viewer"


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            # Remember where they were going so we can send them back post-login
            session["next_url"] = request.url
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        u = session.get("user")
        if not u:
            session["next_url"] = request.url
            return redirect(url_for("auth.login"))
        if u.get("role") not in ("admin", "super_admin"):
            return render_template("unauthorized.html"), 403
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@auth_bp.route("/login")
def login():
    """Kick off the OAuth2 auth-code flow."""
    if not _cfg("MICROSOFT_CLIENT_ID"):
        return render_template(
            "login.html",
            error="Microsoft SSO is not configured. Set MICROSOFT_CLIENT_ID / "
                  "MICROSOFT_CLIENT_SECRET / MICROSOFT_TENANT_ID in your .env file.",
        )

    flow = _build_msal_app().initiate_auth_code_flow(
        scopes=LOGIN_SCOPES,
        redirect_uri=url_for("auth.callback", _external=True),
    )
    session["auth_flow"] = flow
    return redirect(flow["auth_uri"])


@auth_bp.route("/callback")
def callback():
    """Microsoft redirects here with ?code=... and ?state=..."""
    flow = session.pop("auth_flow", None)
    if not flow:
        flash("Login session expired. Please sign in again.", "error")
        return redirect(url_for("auth.login"))

    try:
        result = _build_msal_app().acquire_token_by_auth_code_flow(flow, request.args)
    except ValueError as e:
        flash(f"Sign-in failed: {e}", "error")
        return redirect(url_for("auth.login"))

    if "error" in result:
        flash(f"Sign-in failed: {result.get('error_description', result['error'])}", "error")
        return redirect(url_for("auth.login"))

    claims = result.get("id_token_claims") or {}
    email = (claims.get("preferred_username")
             or claims.get("email")
             or claims.get("upn")
             or "").lower()
    name = claims.get("name") or email
    oid = claims.get("oid")
    tid = claims.get("tid")

    if not email:
        flash("Microsoft did not return an email address for this account.", "error")
        return redirect(url_for("auth.login"))

    # Optional domain allow-list
    allowed = _allowed_domain()
    if allowed and not email.endswith("@" + allowed):
        flash(f"Only @{allowed} accounts can sign in.", "error")
        return redirect(url_for("auth.login"))

    # Upsert User row
    user = User.query.filter_by(email=email).first()
    now = datetime.utcnow()
    if user is None:
        user = User(
            email=email, display_name=name, oid=oid, tenant_id=tid,
            first_login_at=now, last_login_at=now, login_count=1,
            role=_resolve_role_for_email(email, None),
        )
        db.session.add(user)
    else:
        user.display_name = name or user.display_name
        user.oid = oid or user.oid
        user.tenant_id = tid or user.tenant_id
        user.last_login_at = now
        user.login_count = (user.login_count or 0) + 1
        # Re-assert hard-coded roles on every login so nobody can demote them
        user.role = _resolve_role_for_email(email, user.role)

    if not user.is_active:
        db.session.commit()
        flash("Your account has been deactivated. Contact the administrator.", "error")
        return redirect(url_for("auth.login"))

    db.session.commit()

    # Minimal session payload — never put tokens or secrets in here
    session["user"] = {
        "id": user.id,
        "email": user.email,
        "name": user.display_name,
        "role": user.role,
    }

    next_url = session.pop("next_url", None) or url_for("dashboard")
    flash(f"Signed in as {user.display_name} ({user.role}).", "success")
    return redirect(next_url)


@auth_bp.route("/logout")
def logout():
    session.clear()
    tenant = _cfg("MICROSOFT_TENANT_ID", "common")
    post_logout = url_for("auth.login", _external=True)
    return redirect(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={post_logout}"
    )
