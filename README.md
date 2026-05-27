# FPEL Renewable Capital - Credit Check (Dummy App)

A simple, teaching-grade Flask + SQLAlchemy + HTML/CSS/JS application that mirrors the workflow described in the FPEL **Credit Check Automation SOP**.

All companies, people, ratings, and numbers in this app are **fictional**. The app is meant to demonstrate the building blocks; it is not connected to D365, Instafinancials, or any production system.

---

## What the app demonstrates

The SOP describes two end-to-end modules sitting inside D365. This dummy app implements simplified versions of both:

1. **All Cases (BD module)** - the form the Business Development team uses to raise a credit check request. Mandatory fields are validated: company name, capacity, park, state, and consumption summary.
2. **Credit Check (RC module)** - the screen the Renewable Capital team uses to score the company, write commentary (profile, strengths, weaknesses, latest updates), pick a decision (Approved / Not Approved / On Hold / Judgmentally Approved), and dispatch.
3. **One Pager** - a lightweight periodic review for existing offtakers.
4. **Mock Instafinancials API** - a `/api/companies/search` endpoint that the front-end calls for live autocomplete on the new-case form.

Workflow rules that come from the SOP and that the app enforces (in a dummy way):
- Total MWp is computed as `Solar + Wind * 2` and is non-editable.
- All financials are stored in **INR Crores**.
- Internal rating (e.g. `4PEL AA+`) is computed from a simple scoring matrix.
- RC Head approval flag is raised automatically when total capacity > 5 MWp or rating falls in the A / BBB family.
- Every credit-check edit is written to an audit trail.

---

## Tech stack

| Layer       | Tooling                                  |
|-------------|------------------------------------------|
| Backend     | Python 3.10+, Flask 3.0                  |
| ORM / DB    | SQLAlchemy 2.0 (via Flask-SQLAlchemy), SQLite file `fpel_credit_check.db` |
| Templates   | Jinja2 (Flask default)                   |
| Front-end   | Plain HTML5, CSS3, vanilla JavaScript    |

No build step, no Node, no React. The app boots in seconds.

---

## Folder structure

```
fpel_credit_check/
+- app.py                # Flask app factory + all routes + scoring helper
+- auth.py               # Microsoft SSO blueprint (MSAL) + RBAC decorators
+- email_service.py      # Microsoft Graph sendMail helper (app-only)
+- models.py             # SQLAlchemy models (Company, Case, CreditCheck, User, ...)
+- seed.py               # Loads dummy companies / financials / cases on first run
+- requirements.txt
+- .env.example          # Template for the real .env (Azure secrets, role defaults)
+- .gitignore
+- README.md
+- fpel_credit_check.db  # created automatically on first run
+- templates/
|  +- base.html
|  +- dashboard.html
|  +- cases_list.html
|  +- new_case.html
|  +- case_detail.html
|  +- credit_check.html
|  +- one_pager.html
|  +- one_pager_view.html
|  +- login.html
|  +- admin_users.html
|  +- unauthorized.html
+- static/
   +- css/style.css
   +- js/app.js
```

---

## How to run locally

```bash
# 1. create a virtual environment (recommended)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. configure secrets (see "Microsoft SSO setup" below)
cp .env.example .env
# then edit .env and fill in the Azure values

# 4. run
python app.py
```

Then open `http://127.0.0.1:5000` in a browser. On first launch, `seed.py` populates 4 dummy companies, ~12 years of financial rows, and 3 sample cases.

To reset the database, simply delete `fpel_credit_check.db` and re-run.

---

## Microsoft SSO + Admin portal + Graph email

The app authenticates users via **Microsoft Entra ID (Azure AD)** using MSAL and the OAuth2 authorization-code flow. Roles are:

| Role          | Who                                                                 | Powers                                                                 |
|---------------|---------------------------------------------------------------------|------------------------------------------------------------------------|
| `super_admin` | `ai@fourthpartner.co` (hard-coded, cannot be demoted/deactivated)   | Everything, including minting other super admins                       |
| `admin`       | `vaidehi.sridhar@fourthpartner.co` by default, plus anyone promoted | Admin portal, role management, send test email, write to all modules   |
| `viewer`      | Everyone else                                                       | Read-only access                                                       |

The Admin portal (`/admin/users`) lists every user who has ever signed in, plus the seeded admins. From there you can change roles, deactivate accounts, and send a test email via the Microsoft Graph API.

### One-time Azure setup

1. Go to <https://entra.microsoft.com> -> **App registrations** -> **+ New registration**.
2. Name: `FPEL Credit Check`. Supported account type: **Single tenant**.
3. Redirect URI (platform = Web):
   - Local dev: `http://localhost:5000/auth/callback`
   - Production: `https://<your-host>/auth/callback`
4. After creation, copy these into your `.env`:
   - **Directory (tenant) ID** -> `MICROSOFT_TENANT_ID`
   - **Application (client) ID** -> `MICROSOFT_CLIENT_ID`
5. **Certificates & secrets** -> **+ New client secret** -> copy the *Value* (not the ID) -> `MICROSOFT_CLIENT_SECRET`.
6. **API permissions** -> **+ Add a permission** -> **Microsoft Graph**:
   - **Delegated** -> `User.Read` (auto-added; used for sign-in)
   - **Application** -> `Mail.Send` (used for the test-email button)
   - Click **Grant admin consent for Fourth Partner Energy**.
7. **Authentication** -> ensure **ID tokens** is ticked under "Implicit grant and hybrid flows".

### Creating your `.env`

```bash
cp .env.example .env
```

Then fill it in. The minimum you need is:

```ini
FLASK_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
MICROSOFT_TENANT_ID=<from step 4>
MICROSOFT_CLIENT_ID=<from step 4>
MICROSOFT_CLIENT_SECRET=<from step 5>
SUPER_ADMIN_EMAIL=ai@fourthpartner.co
DEFAULT_ADMIN_EMAILS=vaidehi.sridhar@fourthpartner.co
ALLOWED_EMAIL_DOMAIN=fourthpartner.co
GRAPH_SENDER_UPN=noreply@fourthpartner.co
```

Set `GRAPH_SENDER_UPN` to a real licensed mailbox in your tenant -- this is the address that the test email will be sent *from*. (`noreply@fourthpartner.co` or `it-notifications@fourthpartner.co` are typical choices.)

### How the test-email button works

1. Admin clicks **Send test email** in the Admin portal.
2. `email_service.send_mail()` uses the client-credentials OAuth flow to get an app-only Graph token (cached until expiry).
3. It POSTs to `https://graph.microsoft.com/v1.0/users/{GRAPH_SENDER_UPN}/sendMail`.
4. Graph returns `202 Accepted` and the message lands in the recipient's inbox.

If the call fails, the exact Graph error code/message is flashed back to the admin -- usually it's a missing permission or unconsented scope.

---

## How the parts fit together

A request flows through the app like this:

1. The browser asks for a URL (e.g. `/cases/new`).
2. Flask's URL map (defined in `register_routes` inside `app.py`) picks the matching view function.
3. The view function queries / writes via SQLAlchemy models (`models.py`).
4. The view renders a Jinja2 template (`templates/*.html`), which extends `base.html`.
5. The template is styled by `static/css/style.css`; small interactivity (autocomplete, capacity calc) comes from `static/js/app.js`.

For the autocomplete, the JS calls `/api/companies/search?q=...`, which the Flask route returns as JSON - this is how a real Instafinancials API integration would be wired up.

---

## How to extend it (suggested next steps)

- **Authentication** - DONE. Microsoft SSO via MSAL with three roles (super_admin / admin / viewer) and an Admin portal at `/admin/users`.
- **Real Instafinancials integration** - replace the mock `/api/companies/search` with a wrapper around the actual Instafinancials API.
- **PDF / DOCX export** - generate a Credit Check report using `python-docx` or `WeasyPrint`.
- **OCR check for uploaded PDFs** - call Tesseract / Azure Document Intelligence when FY2025 financials are uploaded manually.
- **Move from SQLite to PostgreSQL** - one config line change in `app.py`.
- **Background jobs** - schedule One-Pager refreshes via Celery / APScheduler.
- **D365 integration** - wrap each model as an OData entity and sync via Dataverse Web API.

---

_Demo built for internal training. All names and figures are fictional._

# fpel_credit_check