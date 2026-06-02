# InstaFinancials Auto-Fetch — Setup & Integration Guide

This wires the InstaFinancials API into the `rc-credit-check` skill so financial
figures are pulled automatically instead of typed into the One Pager by hand.

The web-search and document-upload paths stay as fallbacks for companies the
API doesn't cover.

---

## 1. Procurement — what to buy (one-time)

Before any code runs, the team needs API access:

1. **Subscribe** to an InstaFinancials API product that returns full financial
   statements — Balance Sheet + P&L for 3 years. The basic master-data lookup is
   not enough; confirm the plan returns: Net Worth, Total Debt (LT + ST),
   Revenue, EBITDA (or the components to derive it), Finance Costs, Depreciation, PAT.
2. **Obtain** the API key, the base URL, and the endpoint documentation.
3. **Confirm the lookup key.** InstaFinancials is keyed on **CIN**, not company
   name. Either get a name→CIN search endpoint on the plan, or plan to supply the
   CIN for each case.
4. Note it is **paid per call** — the skill should only call it on confirmed cases.

---

## 1.5 Basically:
1. Subscribe to the right product. InstaFinancials sells data via API under products like Company Financials, Comprehensive Company Report, and MCA documents. For the One Pager you want the financials endpoint that returns Balance Sheet + P&L (3 years), not just the basic master-data lookup. Confirm with their sales team that the plan returns Net Worth, Total Debt, EBITDA/Revenue/PAT, Finance Costs, Depreciation.
2. Get the API key + base URL + endpoint docs. Their API is keyed by CIN (Corporate Identification Number), not company name — so you'll also need either a name→CIN search endpoint or to supply the CIN yourself.
3. Note that it's a paid, per-call API. Each fetch costs credits, so the skill should call it only on confirmed cases.

## 2. Store the API key (never hard-code)

The script reads the key from an environment variable:

```bash
export INSTAFIN_API_KEY="your-key-here"
```

For repeated use, add that line to the shell profile or store it in the team's
secrets manager. The key must never be committed to the skill files or this repo.

---

## 3. Configure the script

Open `fetch_instafinancials.py` and edit the two `# >>> CONFIGURE` areas using
the API docs:

- **`CONFIG` block** — `base_url`, `financials_path`, `search_path`,
  and `auth_mode` (header vs query param).
- **`_map_response()`** — change the `raw[...]` field names and the per-year
  field names (`RevenueFromOperations`, `NetWorth`, etc.) to match the real
  response. Also set the correct `unit` ("rupees" / "lakh" / "crore") so figures
  normalise to ₹ Crore correctly.

The fastest way to nail the field paths: make one real call, save the JSON, and
adapt the mapping against it (see step 4).

---

## 4. Test before going live (no live calls needed)

Use the `--mock` flag with a saved sample response:

```bash
python3 fetch_instafinancials.py --mock sample_response.json --out data_partial.json
```

Verify the printed FYs and the figures in `data_partial.json` are correct. Once a
real key is available, swap `--mock` for `--cin` or `--name`:

```bash
python3 fetch_instafinancials.py --cin U12345MH2009PLC123456 --out data_partial.json
# or
python3 fetch_instafinancials.py --name "Acme Industries Pvt Ltd" --out data_partial.json
```

---

## 5. Install into the skill

Copy `fetch_instafinancials.py` into the skill's `scripts/` folder, alongside
`generate_report.py`, and apply the SKILL.md patch in
`SKILL_PATCH_PathC.md` (this folder). The installed skill lives under the
Claude skills plugin directory; an admin with write access drops the file there.

---

## 6. How it runs end-to-end

1. Skill collects deal details (Step 1) — now offers "Fetch from InstaFinancials".
2. `fetch_instafinancials.py` pulls data → writes a **partial** `data.json`
   (company name, incorporation date, financials table, screening, and a numeric
   `_metrics` block).
3. The skill fills the qualitative sections (brief profile, strengths,
   weaknesses, latest updates, credit view) from web search / uploads, and runs
   the 4PEL scoring using the `_metrics` numbers.
4. `generate_report.py` renders the final Word document — unchanged.

The API only populates the numeric half of the One Pager; the analyst still
reviews and the scoring/qualitative work is unchanged.

---

## What the API can and can't fill

| Section | Source |
|---|---|
| Company name, incorporation date | InstaFinancials |
| Financial snapshot table | InstaFinancials |
| Screening (Net Worth / Turnover / PAT) | InstaFinancials |
| Numeric inputs to 4PEL scoring | InstaFinancials |
| External credit rating | Rating report / web (not in financials API) |
| Brief profile, strengths, weaknesses | Web search / uploads |
| Latest updates / news | Web search |
| Credit view & conditions | Analyst judgement |

> Always verify auto-fetched figures against the source before the report goes
> to BD — InstaFinancials data can lag MCA filings.


1. Register: Create an account on the InstaFinancials platform.
2. Login: Access api.instafinancials.com.
3. Generate Key: Click on “Generate API Key.”
4. Activation: Share your registered email ID and the products you wish to test with us at sales@instafinancials.com for API key activation.
5. Test: Access the Sandbox environment to test the APIs.
6. Payment: Purchase API credits through advance payment.
7. Production: Receive your Production API Key.
8. Integration: Start integrating the APIs into your application using the Production Key.