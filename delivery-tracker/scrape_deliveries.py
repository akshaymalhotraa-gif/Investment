"""Airbus & Boeing delivery tracker scraped from planespotters.net/deliveries.

Walks the paginated deliveries list, keeps only Airbus and Boeing aircraft,
merges new rows into data/deliveries.csv (the master record) and rebuilds
data/airbus_boeing_deliveries.xlsx. Optionally mirrors to Google Sheets when
GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID are set.
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

BASE_URL = "https://www.planespotters.net/deliveries"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "deliveries.csv"
XLSX_PATH = DATA_DIR / "airbus_boeing_deliveries.xlsx"

MAX_PAGES = int(os.environ.get("MAX_PAGES", "60"))
# Stop once this many consecutive pages bring nothing new (older pages are already captured).
STOP_AFTER_STALE_PAGES = int(os.environ.get("STOP_AFTER_STALE_PAGES", "3"))
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "3"))

FIELDS = [
    "Manufacturer", "Aircraft Type", "Registration", "MSN", "Operator",
    "Delivery Date", "Status", "Source URL", "First Seen", "Last Seen",
]

# Header text on the site -> our field name.
HEADER_MAP = {
    "reg": "Registration", "registration": "Registration",
    "msn": "MSN", "c/n": "MSN", "cn": "MSN", "serial": "MSN", "serial number": "MSN",
    "type": "Aircraft Type", "aircraft type": "Aircraft Type", "aircraft": "Aircraft Type", "model": "Aircraft Type",
    "airline": "Operator", "operator": "Operator", "customer": "Operator", "owner": "Operator",
    "delivery": "Delivery Date", "delivery date": "Delivery Date", "delivered": "Delivery Date", "date": "Delivery Date",
    "status": "Status",
}


def manufacturer_of(aircraft_type: str):
    t = aircraft_type.lower()
    if "airbus" in t or re.search(r"\ba3\d\d", t) or "a220" in t or "beluga" in t:
        return "Airbus"
    if "boeing" in t or re.search(r"\b7[0-8]7\b|\b7[0-8]7[- ]", t) or "737 max" in t:
        return "Boeing"
    return None


# ---------------------------------------------------------------- fetching
def make_session():
    """Prefer curl_cffi (browser TLS fingerprint) to get past bot protection."""
    try:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate="chrome")
    except ImportError:
        import requests
        s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept-Language": "en-GB,en;q=0.9",
    })
    return s


def fetch(session, url, retries=4):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=45)
            if r.status_code == 200:
                return r.text
            print(f"  HTTP {r.status_code} for {url}", file=sys.stderr)
            if r.status_code == 404:
                return None
        except Exception as e:  # network hiccup
            print(f"  error {e!r} for {url}", file=sys.stderr)
        time.sleep(2 ** (attempt + 1))
    return None


# ---------------------------------------------------------------- parsing
def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _grid(soup):
    """Return (header_cells, data_rows) from either a <table> or the div-based dt-tr/dt-td layout."""
    candidates = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        head = rows[0].find_all(["th", "td"])
        body = [r.find_all(["td", "th"]) for r in rows[1:]]
        candidates.append((head, body))
    for container in soup.select(".dt-table, [class*=datatable]"):
        rows = container.select(".dt-tr")
        if len(rows) < 2:
            continue
        head_row = container.select_one(".dt-tr.dt-th-row") or rows[0]
        head = head_row.select(".dt-th") or head_row.select(".dt-td")
        body = [r.select(".dt-td") for r in rows if r is not head_row]
        candidates.append((head, body))

    def score(c):
        names = {HEADER_MAP.get(_clean(h.get_text()).lower()) for h in c[0]}
        return (len(names - {None}), len(c[1]))
    return max(candidates, key=score) if candidates else ([], [])


def parse_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    head, body = _grid(soup)
    cols = [HEADER_MAP.get(_clean(h.get_text()).lower()) for h in head]
    if "Aircraft Type" not in cols:
        return [], soup
    rows = []
    for cells in body:
        if len(cells) < len([c for c in cols if c]):
            continue
        rec = {}
        for name, cell in zip(cols, cells):
            if name and name not in rec:
                rec[name] = _clean(cell.get_text(" "))
        mfr = manufacturer_of(rec.get("Aircraft Type", ""))
        if not mfr:
            continue
        rec["Manufacturer"] = mfr
        rec["Delivery Date"] = normalise_date(rec.get("Delivery Date", ""))
        link = next((a["href"] for c in cells for a in c.find_all("a", href=True)
                     if "/production-list/" in a["href"] or "/airframe/" in a["href"]), "")
        rec["Source URL"] = urljoin(page_url, link) if link else page_url
        rows.append(rec)
    return rows, soup


def normalise_date(s):
    s = _clean(s)
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%d.%m.%Y", "%d/%m/%Y", "%b %Y", "%B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s


def next_page_url(soup, current_url, page_no):
    link = soup.find("a", rel="next") or soup.find("link", rel="next")
    if not link:
        for a in soup.find_all("a", href=True):
            txt = _clean(a.get_text()).lower()
            if txt in {"next", "next »", "»", "›", "next page", ">"} or "next" in " ".join(a.get("class", [])):
                link = a
                break
    if link and link.get("href"):
        return urljoin(current_url, link["href"])
    # Fallback: ?page=N
    u = urlparse(BASE_URL)
    q = parse_qs(u.query)
    q["page"] = [str(page_no + 1)]
    return urlunparse(u._replace(query=urlencode(q, doseq=True)))


# ---------------------------------------------------------------- storage
def key(rec):
    msn = rec.get("MSN", "").strip()
    if msn:
        return (rec["Manufacturer"], msn)  # MSNs are unique per manufacturer
    return (rec["Manufacturer"], rec.get("Registration", ""), rec.get("Aircraft Type", ""))


def load_db():
    if not CSV_PATH.exists():
        return {}
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {key(r): r for r in csv.DictReader(f)}


def save_csv(db):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = sorted(db.values(), key=lambda r: (r.get("Delivery Date", ""), r.get("MSN", "")), reverse=True)
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def scrape(db):
    session = make_session()
    today = dt.date.today().isoformat()
    url, seen_urls, stale, new_count, pages = BASE_URL, set(), 0, 0, 0
    for page_no in range(1, MAX_PAGES + 1):
        if url in seen_urls:
            break
        seen_urls.add(url)
        print(f"Page {page_no}: {url}")
        html = fetch(session, url)
        if not html:
            break
        rows, soup = parse_page(html, url)
        pages += 1
        if not rows and page_no == 1:
            raise SystemExit("No delivery rows parsed from page 1 - site layout changed or request blocked.")
        if not rows:
            break
        page_new = 0
        for rec in rows:
            k = key(rec)
            if k in db:
                old = db[k]
                for f in ("Registration", "Operator", "Delivery Date", "Status", "Source URL"):
                    if rec.get(f):
                        old[f] = rec[f]
                old["Last Seen"] = today
            else:
                rec["First Seen"] = rec["Last Seen"] = today
                db[k] = rec
                page_new += 1
        new_count += page_new
        print(f"  {len(rows)} Airbus/Boeing rows, {page_new} new")
        stale = stale + 1 if page_new == 0 else 0
        if stale >= STOP_AFTER_STALE_PAGES:
            print("  no new rows for several pages - caught up")
            break
        url = next_page_url(soup, url, page_no)
        time.sleep(REQUEST_DELAY)
    return new_count, pages


# ---------------------------------------------------------------- outputs
def build_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    hdr_font, hdr_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")

    def sheet(ws, header, data, widths):
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill, c.alignment = hdr_font, hdr_fill, Alignment(horizontal="center")
        for r in data:
            ws.append(r)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        if data:
            ws.auto_filter.ref = ws.dimensions

    # Summary: monthly deliveries by manufacturer (live COUNTIFS on the All Deliveries tab)
    ws = wb.active
    ws.title = "Summary"
    months = sorted({r["Delivery Date"][:7] for r in rows if re.match(r"\d{4}-\d{2}", r.get("Delivery Date", ""))},
                    reverse=True)
    n = len(rows) + 1
    data = []
    for i, m in enumerate(months, start=2):
        data.append([m,
                     f'=COUNTIFS(\'All Deliveries\'!$A$2:$A${n},"Airbus",\'All Deliveries\'!$F$2:$F${n},A{i}&"*")',
                     f'=COUNTIFS(\'All Deliveries\'!$A$2:$A${n},"Boeing",\'All Deliveries\'!$F$2:$F${n},A{i}&"*")',
                     f"=B{i}+C{i}"])
    sheet(ws, ["Month", "Airbus", "Boeing", "Total"], data, [12, 10, 10, 10])
    ws.cell(row=1, column=6, value="Last updated (UTC)").font = Font(bold=True)
    ws.cell(row=1, column=7, value=dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    ws.cell(row=2, column=6, value="Source").font = Font(bold=True)
    ws.cell(row=2, column=7, value=BASE_URL)
    ws.column_dimensions["F"].width = 20
    ws.column_dimensions["G"].width = 40

    # By type: deliveries per aircraft type
    types = sorted({(r["Manufacturer"], r["Aircraft Type"]) for r in rows})
    ws = wb.create_sheet("By Type")
    data = [[m, t, f'=COUNTIFS(\'All Deliveries\'!$A$2:$A${n},A{i},\'All Deliveries\'!$B$2:$B${n},B{i})']
            for i, (m, t) in enumerate(types, start=2)]
    sheet(ws, ["Manufacturer", "Aircraft Type", "Deliveries"], data, [14, 30, 12])

    widths = [12, 26, 12, 10, 32, 13, 12, 50, 12, 12]
    as_list = lambda rs: [[r.get(f, "") for f in FIELDS] for r in rs]
    sheet(wb.create_sheet("All Deliveries"), FIELDS, as_list(rows), widths)
    sheet(wb.create_sheet("Airbus"), FIELDS, as_list([r for r in rows if r["Manufacturer"] == "Airbus"]), widths)
    sheet(wb.create_sheet("Boeing"), FIELDS, as_list([r for r in rows if r["Manufacturer"] == "Boeing"]), widths)
    wb.save(XLSX_PATH)


def sync_google_sheet(rows):
    creds_json, sheet_id = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("GOOGLE_SHEET_ID")
    if not (creds_json and sheet_id):
        print("Google Sheets sync skipped (secrets not set).")
        return
    import gspread
    gc = gspread.service_account_from_dict(json.loads(creds_json))
    sh = gc.open_by_key(sheet_id)
    for title, subset in (("All Deliveries", rows),
                          ("Airbus", [r for r in rows if r["Manufacturer"] == "Airbus"]),
                          ("Boeing", [r for r in rows if r["Manufacturer"] == "Boeing"])):
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title, rows=1, cols=len(FIELDS))
        ws.clear()
        ws.update([FIELDS] + [[r.get(f, "") for f in FIELDS] for r in subset], value_input_option="RAW")
    print(f"Google Sheet {sheet_id} updated.")


def main():
    db = load_db()
    before = len(db)
    new_count, pages = scrape(db)
    rows = save_csv(db)
    build_xlsx(rows)
    sync_google_sheet(rows)
    print(f"Done: {pages} pages scanned, {new_count} new deliveries, {len(rows)} total (was {before}).")


if __name__ == "__main__":
    main()
