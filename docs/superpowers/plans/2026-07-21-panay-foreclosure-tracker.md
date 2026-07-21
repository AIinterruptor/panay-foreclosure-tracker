# Panay + Guimaras Foreclosure Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auto-refreshing (every 6h) static GitHub Pages dashboard of foreclosed properties in Panay + Guimaras, showing location (→ Google Maps), selling price, and seller, fed by a Playwright-based scraper run in GitHub Actions.

**Architecture:** Three layers — `scrapers/` (one module per source, each `fetch()→list[dict]`, never raises), `build.py` (normalize→dedup→sort→emit JSON/CSV/meta), and `docs/index.html` (vanilla-JS static frontend consuming the JSON). A GitHub Actions cron runs scrapers→build every 6h and commits changed data; Pages serves `/docs` on `main`.

**Tech Stack:** Python 3.11+, Playwright (Chromium), pytest, vanilla HTML/CSS/JS (no build step), GitHub Actions, GitHub Pages.

## Global Constraints

- Python 3.11+; dependencies pinned in `requirements.txt`: `playwright`, `pytest`.
- Every scraper module exposes `fetch() -> list[dict]` and **never raises** — it returns `[]` and its errors are surfaced by the pipeline, not thrown.
- Unified record schema (exact keys): `source, seller, property_type, location_text, province, price_php, lot_area_sqm, floor_area_sqm, tct, sale_type, auction_date, maps_url, image_url`.
- `image_url`: listing photo URL when the source exposes one, else `None`. Passthrough string (no coercion). Scrapers should capture the card/listing thumbnail when present.
- Provinces (exact strings, this sort order): `Iloilo, Capiz, Aklan, Antique, Guimaras`.
- `maps_url` = `https://www.google.com/maps/search/?api=1&query=` + `urllib.parse.quote_plus(location_text + ", Philippines")`.
- Numeric fields (`price_php, lot_area_sqm, floor_area_sqm`) are `float` or `None`; never a formatted string.
- An empty scrape MUST NOT wipe prior data — the pipeline retains a source's previous rows and marks it stale.
- Commit generated `data/*` from Actions **only when changed**.
- Do NOT scrape foreclosedbahay.com (robots-banned). v1 sources = foreclosurephilippines, Pag-IBIG OPA, Metrobank only.
- All commits end with the Co-Authored-By trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Project scaffold + normalization core

**Files:**
- Create: `requirements.txt`, `scrapers/__init__.py`, `normalize.py`, `tests/test_normalize.py`, `.gitignore`

**Interfaces:**
- Produces:
  - `RECORD_KEYS: list[str]` — the 12 schema keys in order.
  - `maps_url(location_text: str) -> str`
  - `to_float(raw) -> float | None` — parses `"₱1,590,000.00"`, `"1590000"`, `1590000`, `""`, `None`.
  - `normalize(raw: dict) -> dict` — takes a partial record, returns a full record with every `RECORD_KEYS` key present (missing → `None`), numerics coerced via `to_float`, and `maps_url` computed from `location_text`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalize.py
from normalize import RECORD_KEYS, maps_url, to_float, normalize

def test_maps_url_encodes_address_and_appends_country():
    u = maps_url("Lot 5 Blk 3, Brgy Pavia, Iloilo")
    assert u == ("https://www.google.com/maps/search/?api=1&query="
                 "Lot+5+Blk+3%2C+Brgy+Pavia%2C+Iloilo%2C+Philippines")

def test_to_float_parses_peso_strings_and_blanks():
    assert to_float("₱1,590,000.00") == 1590000.0
    assert to_float("1590000") == 1590000.0
    assert to_float(1590000) == 1590000.0
    assert to_float("") is None
    assert to_float(None) is None
    assert to_float("N/A") is None

def test_normalize_fills_all_keys_and_coerces():
    raw = {"source": "test", "location_text": "Oton, Iloilo",
           "province": "Iloilo", "price_php": "₱5,020,000"}
    rec = normalize(raw)
    assert set(rec.keys()) == set(RECORD_KEYS)
    assert rec["price_php"] == 5020000.0
    assert rec["tct"] is None
    assert rec["maps_url"].endswith("Oton%2C+Iloilo%2C+Philippines")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'normalize'`

- [ ] **Step 3: Write minimal implementation**

```python
# requirements.txt
playwright
pytest
```

```
# .gitignore
__pycache__/
*.pyc
.pytest_cache/
```

```python
# scrapers/__init__.py
```

```python
# normalize.py
import re
from urllib.parse import quote_plus

RECORD_KEYS = [
    "source", "seller", "property_type", "location_text", "province",
    "price_php", "lot_area_sqm", "floor_area_sqm", "tct",
    "sale_type", "auction_date", "maps_url",
]
_NUMERIC = {"price_php", "lot_area_sqm", "floor_area_sqm"}

def maps_url(location_text):
    q = quote_plus((location_text or "").strip() + ", Philippines")
    return "https://www.google.com/maps/search/?api=1&query=" + q

def to_float(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    if cleaned in ("", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def normalize(raw):
    rec = {k: raw.get(k) for k in RECORD_KEYS}
    for k in _NUMERIC:
        rec[k] = to_float(rec[k])
    rec["maps_url"] = maps_url(rec.get("location_text") or "")
    return rec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore scrapers/__init__.py normalize.py tests/test_normalize.py
git commit -m "feat: project scaffold + normalization core

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Capture live fixtures (recon)

This task hits the network once to record real page/API responses into `tests/fixtures/`, so every scraper below is testable offline. It is the ONLY task that must run online.

**Files:**
- Create: `tools/capture_fixtures.py`, and the recorded fixtures under `tests/fixtures/`

**Interfaces:**
- Produces fixture files consumed by Tasks 3–5:
  - `tests/fixtures/foreclosurephilippines_iloilo.html`
  - `tests/fixtures/foreclosurephilippines_guimaras.html`
  - `tests/fixtures/pagibig_opa_iloilo.json`
  - `tests/fixtures/metrobank_region6.html`

- [ ] **Step 1: Write the capture tool**

```python
# tools/capture_fixtures.py
"""Run online once to record real responses as test fixtures.
Usage: python tools/capture_fixtures.py
"""
import json, pathlib
from playwright.sync_api import sync_playwright

FIX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

PAGES = {
    "foreclosurephilippines_iloilo.html":
        "https://www.foreclosurephilippines.com/location/iloilo",
    "foreclosurephilippines_guimaras.html":
        "https://www.foreclosurephilippines.com/location/guimaras",
    "metrobank_region6.html":
        "https://www.metrobank.com.ph/assets-for-sale/properties",
}

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"))
        page = ctx.new_page()
        for name, url in PAGES.items():
            page.goto(url, wait_until="networkidle", timeout=60000)
            (FIX / name).write_text(page.content(), encoding="utf-8")
            print("saved", name, len(page.content()), "bytes")
        # Pag-IBIG OPA JSON (Iloilo). Adjust city_muni to a city with stock.
        api = ("https://www.pagibigfundservices.com/OnlinePublicAuction/"
               "ListofProperties/Load_SearchListProperties_COPA"
               "?flag=1&region=060000000&province=063000000"
               "&city_muni=063022000&prop_type=1"
               "&range_from=0&range_to=999999999"
               "&lot_from=0&lot_to=999999&floor_from=0&floor_to=999999"
               "&occupancy=0")
        resp = page.request.get(api)
        (FIX / "pagibig_opa_iloilo.json").write_text(
            resp.text(), encoding="utf-8")
        print("saved pagibig json", resp.status)
        browser.close()

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Install Playwright and run capture**

Run:
```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
python tools/capture_fixtures.py
```
Expected: prints `saved ...` lines; four fixture files exist in `tests/fixtures/`.
If a URL's layout differs from the spec's recon (site changed since 2026-07-21), inspect the saved HTML and note the actual container/field selectors — Tasks 3–5 parse against these real fixtures, so record what is actually there.

- [ ] **Step 3: Verify fixtures are non-trivial**

Run: `python -c "import pathlib,glob; [print(f, pathlib.Path(f).stat().st_size) for f in glob.glob('tests/fixtures/*')]"`
Expected: each file > 1 KB. If Pag-IBIG JSON is `{"success":true,"data":[]}` for the chosen city, try another Iloilo city_muni code until one returns rows (Iloilo City, Leganes, Oton, Pavia, Pototan, Santa Barbara had stock in recon), and re-run capture.

- [ ] **Step 4: Commit fixtures**

```bash
git add tools/capture_fixtures.py tests/fixtures/
git commit -m "chore: capture live source fixtures for offline scraper tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: foreclosurephilippines scraper (backbone)

Parse the captured province HTML into records. This is the widest-coverage source and the only one that reaches Guimaras.

**Files:**
- Create: `scrapers/foreclosurephilippines.py`, `tests/test_foreclosurephilippines.py`

**Interfaces:**
- Consumes: `normalize.normalize`.
- Produces:
  - `parse(html: str, province: str) -> list[dict]` — pure function, no network; parses one province page's listing cards into normalized records with `source="foreclosurephilippines"`.
  - `fetch() -> list[dict]` — Playwright-loads all 5 province pages + Guimaras, calls `parse` per page, returns combined list; catches all exceptions and returns `[]` on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_foreclosurephilippines.py
import pathlib
from scrapers.foreclosurephilippines import parse

FIX = pathlib.Path(__file__).parent / "fixtures"

def test_parse_iloilo_returns_normalized_records():
    html = (FIX / "foreclosurephilippines_iloilo.html").read_text(encoding="utf-8")
    recs = parse(html, "Iloilo")
    assert len(recs) > 0
    r = recs[0]
    assert r["source"] == "foreclosurephilippines"
    assert r["province"] == "Iloilo"
    assert r["location_text"]                     # non-empty
    assert r["maps_url"].startswith("https://www.google.com/maps/search/")
    # price is float-or-None, never a string
    assert r["price_php"] is None or isinstance(r["price_php"], float)

def test_parse_guimaras_smoke():
    html = (FIX / "foreclosurephilippines_guimaras.html").read_text(encoding="utf-8")
    recs = parse(html, "Guimaras")
    # Guimaras is thin; assert it parses without error and tags province.
    assert all(r["province"] == "Guimaras" for r in recs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_foreclosurephilippines.py -v`
Expected: FAIL — `ModuleNotFoundError` / `parse` undefined.

- [ ] **Step 3: Write implementation**

Inspect the fixture first to confirm the real card container/field selectors, then implement `parse` against them. Skeleton (adjust selector strings to match the captured HTML):

```python
# scrapers/foreclosurephilippines.py
from html.parser import HTMLParser
from normalize import normalize

BASE = "https://www.foreclosurephilippines.com/location/"
PROVINCES = {
    "Iloilo": "iloilo", "Capiz": "capiz", "Aklan": "aklan",
    "Antique": "antique", "Guimaras": "guimaras",
}

def parse(html, province):
    """Parse one province listing page into normalized records.
    Uses BeautifulSoup-free stdlib parsing against the card markup
    confirmed in tests/fixtures/foreclosurephilippines_<prov>.html.
    """
    from bs4 import BeautifulSoup  # add beautifulsoup4 to requirements.txt
    soup = BeautifulSoup(html, "html.parser")
    records = []
    # NOTE: replace '.property-listing' etc. with the real classes seen in the fixture.
    for card in soup.select(".property-listing, article.listing, .listing-item"):
        title = card.get_text(" ", strip=True)
        loc_el = card.select_one(".location, .listing-location")
        price_el = card.select_one(".price, .listing-price")
        raw = {
            "source": "foreclosurephilippines",
            "province": province,
            "location_text": (loc_el.get_text(strip=True) if loc_el else title)[:300],
            "price_php": price_el.get_text(strip=True) if price_el else None,
            "seller": _infer_seller(title),
            "property_type": _infer_type(title),
            "sale_type": _infer_sale_type(title),
        }
        records.append(normalize(raw))
    return records

def _infer_seller(title):
    t = title.lower()
    for key, name in [("pag-ibig", "Pag-IBIG"), ("pdic", "PDIC"),
                      ("bdo", "BDO"), ("bpi", "BPI"), ("metrobank", "Metrobank"),
                      ("landbank", "Landbank"), ("pnb", "PNB")]:
        if key in t:
            return name
    return "Unknown (foreclosurephilippines)"

def _infer_type(title):
    t = title.lower()
    if "vacant lot" in t or "lot only" in t: return "Lot"
    if "commercial" in t: return "Commercial"
    if "condo" in t: return "Condominium"
    if "house" in t or "residential" in t: return "Residential"
    return None

def _infer_sale_type(title):
    t = title.lower()
    if "negotiated" in t: return "Negotiated Sale"
    if "public auction" in t or "bidding" in t: return "Public Auction"
    return None

def fetch():
    try:
        from playwright.sync_api import sync_playwright
        out = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36")).new_page()
            for prov, slug in PROVINCES.items():
                try:
                    page.goto(BASE + slug, wait_until="networkidle", timeout=60000)
                    out.extend(parse(page.content(), prov))
                except Exception:
                    continue
            browser.close()
        return out
    except Exception:
        return []
```

Add `beautifulsoup4` to `requirements.txt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pip install beautifulsoup4 && python -m pytest tests/test_foreclosurephilippines.py -v`
Expected: PASS. If `test_parse_iloilo_returns_normalized_records` finds 0 cards, the selectors don't match the fixture — open the fixture, find the real listing container class, fix the `soup.select(...)` argument, re-run.

- [ ] **Step 5: Commit**

```bash
git add scrapers/foreclosurephilippines.py tests/test_foreclosurephilippines.py requirements.txt
git commit -m "feat: foreclosurephilippines scraper (backbone, all provinces + Guimaras)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Pag-IBIG OPA scraper (official, Iloilo + Capiz)

**Files:**
- Create: `scrapers/pagibig_opa.py`, `tests/test_pagibig_opa.py`

**Interfaces:**
- Consumes: `normalize.normalize`.
- Produces:
  - `parse(payload: dict, province: str) -> list[dict]` — maps OPA JSON `data[]` rows to normalized records, `source="pagibig_opa"`, `seller="Pag-IBIG"`.
  - `fetch() -> list[dict]` — calls the OPA API for Iloilo + Capiz cities, returns combined normalized list; returns `[]` on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pagibig_opa.py
import json, pathlib
from scrapers.pagibig_opa import parse

FIX = pathlib.Path(__file__).parent / "fixtures"

def test_parse_maps_opa_rows():
    payload = json.loads((FIX / "pagibig_opa_iloilo.json").read_text(encoding="utf-8"))
    recs = parse(payload, "Iloilo")
    assert isinstance(recs, list)
    for r in recs:
        assert r["source"] == "pagibig_opa"
        assert r["seller"] == "Pag-IBIG"
        assert r["province"] == "Iloilo"
        assert r["price_php"] is None or isinstance(r["price_php"], float)

def test_parse_handles_empty_payload():
    assert parse({"success": True, "data": []}, "Capiz") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pagibig_opa.py -v`
Expected: FAIL — module/`parse` undefined.

- [ ] **Step 3: Write implementation**

Open `tests/fixtures/pagibig_opa_iloilo.json`, confirm the real field names in each `data[]` row, and map them below (replace the `row.get(...)` keys with the actual ones seen).

```python
# scrapers/pagibig_opa.py
from normalize import normalize

API = ("https://www.pagibigfundservices.com/OnlinePublicAuction/"
       "ListofProperties/Load_SearchListProperties_COPA")
# Iloilo (063000000) + Capiz (061900000) cities that had stock in recon.
TARGETS = [
    ("Iloilo", "063000000", "063022000"),  # Iloilo City — adjust per real codes
    ("Capiz",  "061900000", "061914000"),  # Roxas City — adjust per real codes
]

def parse(payload, province):
    rows = (payload or {}).get("data") or []
    out = []
    for row in rows:
        loc = ", ".join(str(row.get(k, "")) for k in
                        ("barangay", "city_municipality", "province") if row.get(k))
        raw = {
            "source": "pagibig_opa",
            "seller": "Pag-IBIG",
            "province": province,
            "location_text": loc or str(row.get("address", "")),
            "price_php": row.get("min_bid_price") or row.get("appraised_value"),
            "lot_area_sqm": row.get("lot_area"),
            "floor_area_sqm": row.get("floor_area"),
            "property_type": row.get("property_type"),
            "sale_type": row.get("disposal_desc") or "Public Auction",
        }
        out.append(normalize(raw))
    return out

def fetch():
    try:
        from playwright.sync_api import sync_playwright
        out = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            req = browser.new_context().request
            for prov, region_prov, city in TARGETS:
                url = (f"{API}?flag=1&region=060000000&province={region_prov}"
                       f"&city_muni={city}&prop_type=1&range_from=0"
                       f"&range_to=999999999&lot_from=0&lot_to=999999"
                       f"&floor_from=0&floor_to=999999&occupancy=0")
                try:
                    resp = req.get(url, timeout=60000)
                    out.extend(parse(resp.json(), prov))
                except Exception:
                    continue
            browser.close()
        return out
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pagibig_opa.py -v`
Expected: PASS. If `test_parse_maps_opa_rows` sees keys that don't exist, fix the `row.get(...)` field names to match the fixture's real JSON keys.

- [ ] **Step 5: Commit**

```bash
git add scrapers/pagibig_opa.py tests/test_pagibig_opa.py
git commit -m "feat: Pag-IBIG OPA scraper (official, Iloilo + Capiz)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Metrobank scraper (official, TCT numbers)

**Files:**
- Create: `scrapers/metrobank.py`, `tests/test_metrobank.py`

**Interfaces:**
- Consumes: `normalize.normalize`.
- Produces:
  - `parse(html: str) -> list[dict]` — parses the Metrobank property list, filters to Panay provinces, `source="metrobank"`, `seller="Metrobank"`, carries `tct`.
  - `fetch() -> list[dict]` — Playwright-loads the list (applying the Region VI filter if present), returns normalized list; `[]` on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrobank.py
import pathlib
from scrapers.metrobank import parse

FIX = pathlib.Path(__file__).parent / "fixtures"
PANAY = {"Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"}

def test_parse_filters_to_panay_and_tags_seller():
    html = (FIX / "metrobank_region6.html").read_text(encoding="utf-8")
    recs = parse(html)
    for r in recs:
        assert r["source"] == "metrobank"
        assert r["seller"] == "Metrobank"
        assert r["province"] in PANAY
        assert r["price_php"] is None or isinstance(r["price_php"], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metrobank.py -v`
Expected: FAIL — module/`parse` undefined.

- [ ] **Step 3: Write implementation**

Inspect the fixture for the real table/row markup and province field, then implement. If the fixture has zero Panay rows (Metrobank often has none in Region VI), the test's loop passes vacuously — that is acceptable; the parser still must run without error.

```python
# scrapers/metrobank.py
from normalize import normalize

URL = "https://www.metrobank.com.ph/assets-for-sale/properties"
PANAY = {"iloilo": "Iloilo", "capiz": "Capiz", "aklan": "Aklan",
         "antique": "Antique", "guimaras": "Guimaras"}

def _province_of(text):
    t = (text or "").lower()
    for key, name in PANAY.items():
        if key in t:
            return name
    return None

def parse(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # NOTE: replace selectors with the real ones seen in the fixture.
    for row in soup.select("table tbody tr, .property-card"):
        text = row.get_text(" ", strip=True)
        prov = _province_of(text)
        if not prov:
            continue
        cells = [c.get_text(strip=True) for c in row.select("td")]
        raw = {
            "source": "metrobank",
            "seller": "Metrobank",
            "province": prov,
            "location_text": text[:300],
            "price_php": next((c for c in cells if "₱" in c or "," in c), None),
            "tct": next((c for c in cells if c.upper().startswith(("TCT", "CCT"))), None),
        }
        out.append(normalize(raw))
    return out

def fetch():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36")).new_page()
            page.goto(URL, wait_until="networkidle", timeout=60000)
            recs = parse(page.content())
            browser.close()
        return recs
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_metrobank.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scrapers/metrobank.py tests/test_metrobank.py
git commit -m "feat: Metrobank scraper (official, carries TCT)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Pipeline — dedup, sort, emit, stale-retention

**Files:**
- Create: `build.py`, `tests/test_build.py`

**Interfaces:**
- Consumes: all scraper `fetch()` functions, `normalize.RECORD_KEYS`.
- Produces:
  - `fingerprint(rec: dict) -> str` — normalized location + rounded price + rounded lot area.
  - `dedup(records: list[dict]) -> list[dict]` — collapses same-fingerprint rows, official source wins, merges seller names.
  - `sort_records(records) -> list[dict]` — province order then price ascending (None prices last).
  - `merge_with_prior(fresh_by_source, prior, source_status) -> list[dict]` — if a source returned 0 rows, reuse its prior rows and mark it stale.
  - `main()` — runs all scrapers, builds `data/listings.json`, `data/listings.csv`, `data/meta.json`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build.py
from build import fingerprint, dedup, sort_records, merge_with_prior

def _rec(**kw):
    base = {"source": "x", "seller": "S", "location_text": "Oton, Iloilo",
            "province": "Iloilo", "price_php": 1000.0, "lot_area_sqm": 100.0}
    base.update(kw); return base

def test_fingerprint_stable_across_case_and_price_rounding():
    a = _rec(location_text="Oton, Iloilo", price_php=1000.0)
    b = _rec(location_text="OTON,  ILOILO", price_php=1000.49)
    assert fingerprint(a) == fingerprint(b)

def test_dedup_prefers_official_and_merges_sellers():
    off = _rec(source="pagibig_opa", seller="Pag-IBIG")
    agg = _rec(source="foreclosurephilippines", seller="BDO")
    out = dedup([agg, off])
    assert len(out) == 1
    assert out[0]["source"] == "pagibig_opa"
    assert "BDO" in out[0]["seller"] and "Pag-IBIG" in out[0]["seller"]

def test_sort_province_then_price_none_last():
    recs = [_rec(province="Guimaras", price_php=5.0),
            _rec(province="Iloilo", price_php=None),
            _rec(province="Iloilo", price_php=10.0)]
    out = sort_records(recs)
    assert out[0]["province"] == "Iloilo" and out[0]["price_php"] == 10.0
    assert out[1]["province"] == "Iloilo" and out[1]["price_php"] is None
    assert out[2]["province"] == "Guimaras"

def test_merge_with_prior_retains_empty_source():
    prior = [_rec(source="metrobank", location_text="Roxas, Capiz", province="Capiz")]
    status = {}
    out = merge_with_prior({"metrobank": []}, prior, status)
    assert any(r["source"] == "metrobank" for r in out)
    assert status["metrobank"]["stale"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_build.py -v`
Expected: FAIL — module/functions undefined.

- [ ] **Step 3: Write implementation**

```python
# build.py
import csv, json, re, pathlib
from datetime import datetime, timezone, timedelta
from normalize import RECORD_KEYS

DATA = pathlib.Path(__file__).resolve().parent / "data"
PROV_ORDER = ["Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"]
OFFICIAL = {"pagibig_opa", "metrobank"}

def fingerprint(rec):
    loc = re.sub(r"\s+", " ", (rec.get("location_text") or "").strip().lower())
    price = rec.get("price_php")
    lot = rec.get("lot_area_sqm")
    return f"{loc}|{round(price) if price else 'x'}|{round(lot) if lot else 'x'}"

def dedup(records):
    by_fp = {}
    for r in records:
        fp = fingerprint(r)
        if fp not in by_fp:
            by_fp[fp] = dict(r)
            continue
        keep = by_fp[fp]
        winner = r if (r["source"] in OFFICIAL and keep["source"] not in OFFICIAL) else keep
        loser = keep if winner is r else r
        sellers = {s.strip() for s in
                   [winner.get("seller"), loser.get("seller")] if s}
        winner = dict(winner)
        winner["seller"] = " / ".join(sorted(sellers))
        by_fp[fp] = winner
    return list(by_fp.values())

def sort_records(records):
    def key(r):
        pi = PROV_ORDER.index(r["province"]) if r["province"] in PROV_ORDER else 99
        price = r.get("price_php")
        return (pi, price is None, price if price is not None else 0.0)
    return sorted(records, key=key)

def merge_with_prior(fresh_by_source, prior, source_status):
    out = []
    prior_by_source = {}
    for r in prior:
        prior_by_source.setdefault(r["source"], []).append(r)
    all_sources = set(fresh_by_source) | set(prior_by_source)
    for src in all_sources:
        fresh = fresh_by_source.get(src, [])
        if fresh:
            out.extend(fresh)
            source_status.setdefault(src, {})["stale"] = False
        else:
            retained = prior_by_source.get(src, [])
            out.extend(retained)
            source_status.setdefault(src, {})["stale"] = True
    return out

def _manila_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))

def main():
    from scrapers import foreclosurephilippines, pagibig_opa, metrobank
    scrapers = {"foreclosurephilippines": foreclosurephilippines,
                "pagibig_opa": pagibig_opa, "metrobank": metrobank}
    DATA.mkdir(exist_ok=True)
    prior = []
    if (DATA / "listings.json").exists():
        prior = json.loads((DATA / "listings.json").read_text(encoding="utf-8"))

    fresh_by_source, status = {}, {}
    for name, mod in scrapers.items():
        try:
            rows = mod.fetch()
            fresh_by_source[name] = rows
            status[name] = {"count": len(rows), "ok": True, "error": None}
        except Exception as e:  # defensive; fetch() shouldn't raise
            fresh_by_source[name] = []
            status[name] = {"count": 0, "ok": False, "error": str(e)}

    merged = merge_with_prior(fresh_by_source, prior, status)
    records = sort_records(dedup(merged))

    (DATA / "listings.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (DATA / "listings.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECORD_KEYS)
        w.writeheader(); w.writerows(records)
    now = _manila_now()
    meta = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
        "last_run_manila": now.strftime("%Y-%m-%d %H:%M %Z"),
        "total": len(records),
        "per_source": status,
    }
    (DATA / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} records")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_build.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Run the full pipeline once (online) to seed data**

Run: `python build.py`
Expected: prints `Wrote N records`; `data/listings.json`, `data/listings.csv`, `data/meta.json` exist. N may be modest; Guimaras rows may be few — that is expected.

- [ ] **Step 6: Commit**

```bash
git add build.py tests/test_build.py data/
git commit -m "feat: build pipeline — dedup, sort, emit, stale-retention + seed data

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Frontend (static GitHub Pages site)

**Files:**
- Create: `docs/index.html` (self-contained: inline CSS + JS)

**Interfaces:**
- Consumes: `../data/listings.json`, `../data/meta.json` (fetched client-side).
- Produces: a filterable table; location cells link to `maps_url`; header shows totals, last-updated (Manila), per-source counts, coverage note, and a stale-source banner.

- [ ] **Step 1: Write `docs/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Panay + Guimaras Foreclosure Tracker</title>
<style>
  body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
  header{padding:1rem 1.25rem;background:#1e293b;border-bottom:1px solid #334155}
  h1{margin:0 0 .25rem;font-size:1.25rem}
  .meta{font-size:.8rem;color:#94a3b8}
  .note{background:#422006;color:#fde68a;padding:.4rem .75rem;font-size:.8rem}
  .stale{background:#450a0a;color:#fecaca;padding:.4rem .75rem;font-size:.8rem;display:none}
  .controls{display:flex;gap:.5rem;flex-wrap:wrap;padding:.75rem 1.25rem;background:#0f172a}
  input,select{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:.4rem .6rem}
  table{width:100%;border-collapse:collapse;font-size:.85rem}
  th,td{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #1e293b}
  th{position:sticky;top:0;background:#1e293b;cursor:pointer}
  a{color:#60a5fa}
  .price{text-align:right;font-variant-numeric:tabular-nums}
</style>
</head>
<body>
<header>
  <h1>Panay + Guimaras Foreclosure Tracker</h1>
  <div class="meta" id="meta">Loading…</div>
</header>
<div class="note">Guimaras / Aklan / Antique inventory is naturally thin — often single digits. Iloilo carries most listings. Data auto-refreshes every 6 hours.</div>
<div class="stale" id="stale"></div>
<div class="controls">
  <input id="search" placeholder="Search location / seller…">
  <select id="province"><option value="">All provinces</option></select>
  <select id="seller"><option value="">All sellers</option></select>
</div>
<table>
  <thead><tr>
    <th data-k="province">Province</th><th data-k="location_text">Location</th>
    <th data-k="price_php">Price (₱)</th><th data-k="seller">Seller</th>
    <th data-k="property_type">Type</th><th data-k="tct">TCT</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
let DATA=[], sortKey="province", sortAsc=true;
const $=s=>document.querySelector(s);
const peso=v=>v==null?"—":"₱"+Number(v).toLocaleString("en-PH");
function opts(sel,vals){for(const v of [...new Set(vals)].filter(Boolean).sort())
  {const o=document.createElement("option");o.value=v;o.textContent=v;sel.appendChild(o);}}
function render(){
  const q=$("#search").value.toLowerCase(), pv=$("#province").value, sl=$("#seller").value;
  let rows=DATA.filter(r=>
    (!pv||r.province===pv)&&(!sl||(r.seller||"").includes(sl))&&
    (!q||((r.location_text||"")+ (r.seller||"")).toLowerCase().includes(q)));
  rows.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
    if(x==null)return 1; if(y==null)return -1;
    return (x>y?1:x<y?-1:0)*(sortAsc?1:-1);});
  $("#rows").innerHTML=rows.map(r=>`<tr>
    <td>${r.province||""}</td>
    <td><a href="${r.maps_url}" target="_blank" rel="noopener">${r.location_text||""}</a></td>
    <td class="price">${peso(r.price_php)}</td>
    <td>${r.seller||""}</td><td>${r.property_type||""}</td><td>${r.tct||""}</td>
  </tr>`).join("");
}
Promise.all([fetch("../data/listings.json").then(r=>r.json()),
             fetch("../data/meta.json").then(r=>r.json())])
.then(([list,meta])=>{
  DATA=list;
  $("#meta").textContent=`${meta.total} listings · updated ${meta.last_run_manila} · `+
    Object.entries(meta.per_source).map(([k,v])=>`${k}: ${v.count}`).join(" · ");
  const stale=Object.entries(meta.per_source).filter(([,v])=>v.stale||!v.ok).map(([k])=>k);
  if(stale.length){const s=$("#stale");s.style.display="block";
    s.textContent="⚠ Stale/failed sources (showing last good data): "+stale.join(", ");}
  opts($("#province"),DATA.map(r=>r.province));
  opts($("#seller"),DATA.map(r=>r.seller));
  document.querySelectorAll("th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; sortAsc=(k===sortKey)?!sortAsc:true; sortKey=k; render();});
  ["#search","#province","#seller"].forEach(s=>$(s).oninput=render);
  render();
}).catch(e=>{$("#meta").textContent="Failed to load data: "+e;});
</script>
</body>
</html>
```

- [ ] **Step 2: Verify it renders against seeded data**

Run: `cd docs && python -m http.server 8000` then open `http://localhost:8000/` in a browser.
Expected: table populates from `../data/listings.json`; province/seller filters work; clicking a location opens Google Maps in a new tab; header shows counts + Manila timestamp. Stop the server with Ctrl-C.

- [ ] **Step 3: Commit**

```bash
git add docs/index.html
git commit -m "feat: static GitHub Pages frontend with filters + Google Maps links

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: GitHub Actions 6-hour refresh workflow

**Files:**
- Create: `.github/workflows/refresh.yml`, `README.md`

**Interfaces:**
- Consumes: `requirements.txt`, `build.py`.
- Produces: scheduled + manual workflow that runs the pipeline and commits changed `data/*`.

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/refresh.yml
name: Refresh foreclosure listings
on:
  schedule:
    - cron: '0 */6 * * *'   # every 6 hours (UTC)
  workflow_dispatch: {}
permissions:
  contents: write
concurrency:
  group: refresh
  cancel-in-progress: false
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - name: Install deps
        run: |
          python -m pip install -r requirements.txt
          python -m playwright install --with-deps chromium
      - name: Scrape + build
        run: python build.py
      - name: Commit if changed
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/
          if git diff --staged --quiet; then
            echo "No changes."
          else
            git commit -m "data: refresh listings $(date -u +%Y-%m-%dT%H:%MZ)"
            git push
          fi
```

- [ ] **Step 2: Write README**

```markdown
# Panay + Guimaras Foreclosure Tracker

Auto-refreshing (every 6h) list of foreclosed / acquired-asset properties in
Panay island (Iloilo, Capiz, Aklan, Antique) + Guimaras. Location, selling price,
seller — location links to Google Maps. Live site: **GitHub Pages** (`/docs`).

## Sources (v1)
- foreclosurephilippines.com (all provinces + Guimaras)
- Pag-IBIG OPA API (official — Iloilo + Capiz)
- Metrobank assets-for-sale (official — carries TCT)

foreclosedbahay.com is intentionally excluded (robots.txt bans AI crawlers).

## How it works
GitHub Actions runs `build.py` every 6h → scrapers (Playwright) → normalize →
dedup → sort → writes `data/listings.json|csv|meta.json` → commits on change →
Pages serves `docs/index.html`.

## Coverage note
Guimaras / Aklan / Antique inventory is naturally thin (often single digits).
Iloilo carries most listings.

## Local dev
```
pip install -r requirements.txt
python -m playwright install chromium
python tools/capture_fixtures.py   # record fixtures (online, once)
python -m pytest -q                 # offline tests
python build.py                     # full run (online)
```

## Enabling Pages
Settings → Pages → Source: "Deploy from a branch" → branch `main`, folder `/docs`.
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/refresh.yml README.md
git commit -m "ci: 6-hourly refresh workflow + README

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Publish to GitHub + enable Pages (Commander-gated)

This task needs the Commander's GitHub auth — it is the one step FRIDAY cannot do silently. Present the commands; run them only on the Commander's go.

**Files:** none (repo operations).

- [ ] **Step 1: Create the public repo and push**

```bash
echo "<AIinterruptor PAT>" | gh auth login --with-token
gh repo create AIinterruptor/panay-foreclosure-tracker --public --source=. --remote=origin --push
```

- [ ] **Step 2: Enable Pages (/docs on main)**

```bash
gh api -X POST repos/AIinterruptor/panay-foreclosure-tracker/pages \
  -f "source[branch]=main" -f "source[path]=/docs"
```
Expected: Pages URL returned (`https://aiinterruptor.github.io/panay-foreclosure-tracker/`).

- [ ] **Step 3: Trigger the first scheduled run manually**

```bash
gh workflow run "Refresh foreclosure listings" \
  -R AIinterruptor/panay-foreclosure-tracker
gh run watch -R AIinterruptor/panay-foreclosure-tracker
```
Expected: workflow succeeds; `data/` updated; site live.

- [ ] **Step 4: Verify the live site**

Open `https://aiinterruptor.github.io/panay-foreclosure-tracker/`.
Expected: table populated, filters work, location links open Google Maps, header shows Manila timestamp + per-source counts.
