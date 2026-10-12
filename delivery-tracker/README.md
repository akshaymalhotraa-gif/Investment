# Airbus & Boeing Delivery Tracker

Scrapes [planespotters.net/deliveries](https://www.planespotters.net/deliveries) (all pages) every 2 days
with GitHub Actions, keeps only Airbus and Boeing aircraft, and maintains:

- `data/airbus_boeing_deliveries.xlsx`: tabs **Summary** (monthly Airbus vs Boeing), **By Type**,
  **All Deliveries**, **Airbus**, **Boeing**
- `data/deliveries.csv`: the master record, merged each run and deduplicated on manufacturer + MSN
- An optional Google Sheet mirror (see below)

Each row records `First Seen` and `Last Seen` dates, so you can tell what was added in each run.

## Running it
- **Automatic:** `.github/workflows/delivery-tracker.yml` runs at 06:17 UTC every 2nd day and commits the refreshed files.
- **Manual / backfill:** Actions tab → *Airbus & Boeing delivery tracker* → *Run workflow*, and raise `max_pages` if needed.
  A run stops early once 3 pages in a row add nothing new.
- **Locally:** `pip install -r requirements.txt && python scrape_deliveries.py`

Note: the schedule only fires once this workflow is on the repo's **default branch**.

## Google Sheets (optional)
1. Create a Google Cloud service account, enable the Sheets API, and download its JSON key.
2. Create a Google Sheet and share it (Editor) with the service account's email.
3. Add the repo secrets `GOOGLE_SERVICE_ACCOUNT_JSON` (the full JSON) and `GOOGLE_SHEET_ID` (the ID from the sheet URL).
