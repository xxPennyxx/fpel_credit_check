"""
Microsoft Graph mail sender.

Uses the OAuth2 client-credentials (application) flow with the
'Mail.Send' APPLICATION permission. That means the app sends mail
*as* a fixed mailbox (GRAPH_SENDER_UPN), not as the signed-in user.

Why client-credentials?
  - It works for unattended scenarios (cron, webhooks, admin buttons).
  - Mail.Send delegated permission would require us to ask each user
    for an extra consent grant at login time, which is overkill for
    a "send a test mail from the admin panel" button.

Azure setup needed (see README):
  1. App registration with a client secret
  2. API permissions -> Microsoft Graph -> Application -> Mail.Send
  3. Click "Grant admin consent"
  4. .env: AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
           GRAPH_SENDER_UPN=<the mailbox the mail is sent from, e.g.
                             noreply@fourthpartner.co>
"""

import os
import time
import requests
import msal
from flask import current_app


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_CACHE = {"value": None, "expires_at": 0}


def _cfg(key: str, default: str = "") -> str:
    return current_app.config.get(key) or os.environ.get(key, default)


def _get_app_token() -> str:
    """Cached app-only Graph token (refreshed a minute before expiry)."""
    now = time.time()
    if _TOKEN_CACHE["value"] and _TOKEN_CACHE["expires_at"] - 60 > now:
        return _TOKEN_CACHE["value"]

    tenant = _cfg("MICROSOFT_TENANT_ID")
    client_id = _cfg("MICROSOFT_CLIENT_ID")
    secret = _cfg("MICROSOFT_CLIENT_SECRET")
    if not all([tenant, client_id, secret]):
        raise RuntimeError(
            "Graph email is not configured. Set MICROSOFT_TENANT_ID, "
            "MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET in .env"
        )

    authority = _cfg("MICROSOFT_AUTHORITY") or f"https://login.microsoftonline.com/{tenant}"
    scope = _cfg("MICROSOFT_GRAPH_SCOPE", "https://graph.microsoft.com/.default")

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=secret,
        authority=authority,
    )
    result = app.acquire_token_for_client(scopes=[scope])
    if "access_token" not in result:
        raise RuntimeError(
            f"Could not acquire Graph token: "
            f"{result.get('error_description') or result.get('error')}"
        )

    _TOKEN_CACHE["value"] = result["access_token"]
    _TOKEN_CACHE["expires_at"] = now + int(result.get("expires_in", 3600))
    return _TOKEN_CACHE["value"]


def send_mail(
    to: str | list[str],
    subject: str,
    body_html: str,
    *,
    cc: list[str] | None = None,
    sender: str | None = None,
) -> dict:
    """
    Send an HTML mail via Microsoft Graph as GRAPH_SENDER_UPN.

    Returns: dict with {"status": "sent"} on success.
    Raises:  RuntimeError on any failure.
    """
    sender_upn = sender or _cfg("GRAPH_SENDER_UPN")
    if not sender_upn:
        raise RuntimeError(
            "GRAPH_SENDER_UPN is not set. Add it to .env "
            "(e.g. GRAPH_SENDER_UPN=noreply@fourthpartner.co)."
        )

    if isinstance(to, str):
        to = [to]
    cc = cc or []

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        },
        "saveToSentItems": True,
    }

    token = _get_app_token()
    url = f"{GRAPH_BASE}/users/{sender_upn}/sendMail"
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )

    if r.status_code == 202:
        return {"status": "sent", "to": to, "from": sender_upn}

    # Surface the Graph error in a readable way for the admin UI
    try:
        err = r.json().get("error", {})
        msg = f"{err.get('code', r.status_code)}: {err.get('message', r.text)}"
    except ValueError:
        msg = f"HTTP {r.status_code}: {r.text}"
    raise RuntimeError(f"Graph sendMail failed -> {msg}")
