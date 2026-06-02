# SKILL.md Patch — add InstaFinancials auto-fetch (Path C)

Apply these two edits to `rc-credit-check/SKILL.md`.

---

## Edit 1 — Step 1, Q4 (add an option)

In **Step 1 — Collect Deal Details**, add a fetch option to Q4:

```
- **Q4 — Documents available?** *(multiSelect: true)*
  - `Financial statements (PDF / Excel)`
  - `External credit rating report (PDF)`
  - `Consumption analysis (PDF / Excel)`
  - `Fetch financials from InstaFinancials (provide CIN or company name)`   ← NEW
  - `None — please search the web`
```

If the user picks the InstaFinancials option, ask for the **CIN** (preferred) or
the exact registered company name as plain text.

---

## Edit 2 — Step 2, add Path C (before "Required Financial Figures")

```
### PATH C — Fetch from InstaFinancials API

Use when the user selected the InstaFinancials option in Q4 and an
`INSTAFIN_API_KEY` is configured in the environment.

Run the bundled fetch script (locate via Glob:
`**/skills/rc-credit-check/scripts/fetch_instafinancials.py`):

```bash
export INSTAFIN_API_KEY="$INSTAFIN_API_KEY"   # already set in the environment

# By CIN (preferred):
python3 /PATH_TO_SKILL/scripts/fetch_instafinancials.py \
  --cin <CIN> --out /tmp/data_partial.json

# Or by name:
python3 /PATH_TO_SKILL/scripts/fetch_instafinancials.py \
  --name "<Company Name>" --out /tmp/data_partial.json
```

The script writes a PARTIAL data.json containing:
`company_name`, `cin`, `incorporation_date`, `financials` (table), `screening`,
and a numeric `_metrics` block (per-year revenue, EBITDA, margins, PAT, net worth,
debt, D/E, ICR, cash profit) used for the 4PEL scoring in Step 3.

Read `/tmp/data_partial.json`, then:
- Use the `_metrics.by_year` figures directly for Step 3 scoring (FY2025).
- Fill the qualitative sections (brief profile, strengths, weaknesses,
  latest_updates, credit_view) and the external credit rating via targeted
  WebSearch — the financials API does not return ratings or news.
- Merge the partial JSON into the full v2 schema before calling
  `generate_report.py`.

Set data confidence to **High** (structured filing data). Always note that
auto-fetched figures should be verified against the MCA filing, as
InstaFinancials data can lag.
```

---

## Note on data sourcing
Path C does not replace web search — it replaces manual typing of the financial
figures. Ratings, news, and qualitative commentary still come from WebSearch or
uploaded documents.
