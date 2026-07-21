# Panay + Guimaras Foreclosure Tracker — Design Spec

**Date:** 2026-07-21
**Repo:** `AIinterruptor/panay-foreclosure-tracker` (new, public)
**Hosting:** GitHub Pages (`/docs` on `main`)
**Refresh:** GitHub Actions cron, every 6 hours

## Purpose

An auto-refreshing public dashboard listing foreclosed / acquired-asset properties
across Panay island (Iloilo, Capiz, Aklan, Antique) and Guimaras, Philippines.
Each listing shows **location, selling price, and seller (bank/institution)**, with
the location linking out to Google Maps. Data refreshes every 6 hours with no manual
intervention and no running server — a static site fed by a scheduled scraper.

## Non-goals (YAGNI)

- No native GitHub App / OAuth / webhooks — a static site + scheduled Action does the job.
- No backend, database, or dynamic server.
- No geocoding API — Google Maps *search* links from the address text (zero cost, no key).
- No sources that are robots-banned (foreclosedbahay.com bans AI crawlers — excluded).
- No fragile sources in v1: BPI PDF catalogs, PNB flaky-JS finder — deferred.

## Reconnaissance findings that shaped this design (2026-07-21)

- **Plain HTTP is blocked** on nearly every target (403 / reCAPTCHA). A rendering /
  anti-bot layer is mandatory → **Playwright headless Chromium** in the Actions runner.
- **foreclosurephilippines.com** is the highest-leverage source: static HTML, one clean
  page per province **including a dedicated `/location/guimaras`**, already aggregating
  Pag-IBIG + PDIC + all major banks with prices and seller-inferable titles.
- **Pag-IBIG OPA** exposes a real JSON API (`Load_SearchListProperties_COPA`) — official
  ground-truth, but currently only Iloilo + Capiz have Panay inventory.
- **Metrobank** offers a downloadable property list including TCT numbers.
- **Coverage truth:** Iloilo holds the volume. Aklan / Antique / Guimaras inventory is
  naturally thin (often single digits). The UI must state this so a short Guimaras list
  never reads as a bug.

## Architecture

Three layers: **Scrapers → Pipeline → Frontend**, wired by a GitHub Actions cron.

### 1. Scrapers (`scrapers/`)

Each module exposes `fetch() -> list[dict]` returning raw records, and never raises to
the caller (errors are captured and returned as a status). v1 sources ("Core 3"):

| Module | Source | Method | Coverage | Notes |
|---|---|---|---|---|
| `foreclosurephilippines.py` | foreclosurephilippines.com | Playwright, paginated | All 5 provinces + Guimaras | Backbone. Static HTML; throttle politely. Listing rows are public (membership CTA does not gate them). |
| `pagibig_opa.py` | Pag-IBIG OPA | JSON API | Iloilo + Capiz | Replicate cascading dropdown params (region+province+city_muni+prop_type required; endpoint 500s on blanks). Region VI = `060000000`; Iloilo `063000000`, Capiz `061900000`. |
| `metrobank.py` | metrobank.com.ph/assets-for-sale | Playwright → "Download Property List" | Region VI filter | Carries TCT numbers. robots blocks `.xls` paths — use the HTML/download button, filter client-side. |

### 2. Pipeline (`build.py`)

Normalize → dedup → sort → emit.

**Unified schema** (one record):
```
source          # which scraper produced it
seller          # bank / institution (Pag-IBIG, PDIC, Metrobank, BDO, BPI, ...)
property_type   # residential, commercial, lot, etc.
location_text   # full address string as published
province        # Iloilo | Capiz | Aklan | Antique | Guimaras
price_php        # numeric selling / minimum-bid price (null if unstated)
lot_area_sqm     # numeric or null
floor_area_sqm   # numeric or null
tct              # title number if available (Metrobank), else null
sale_type        # public auction stage / negotiated sale, if known
auction_date     # if known, else null
maps_url         # Google Maps search link (see below)
```

- **`maps_url`** = `https://www.google.com/maps/search/?api=1&query=` + URL-encoded
  (`location_text` + `", Philippines"`). No API key, no cost.
- **Dedup:** fingerprint = normalized(location_text) + rounded price + rounded lot area.
  On collision, keep the more authoritative record (official source > aggregator) and
  merge both seller names into `seller` (e.g. "BDO (via foreclosurephilippines)").
- **Sort:** province (Iloilo, Capiz, Aklan, Antique, Guimaras) → price ascending.
- **Emit:**
  - `data/listings.json` — array of records (frontend consumes this).
  - `data/listings.csv` — same data, portable.
  - `data/meta.json` — `{ last_run_utc, last_run_manila, total, per_source: {name: {count, ok, error}} }`.

### 3. Frontend (`docs/` → GitHub Pages)

Single static `docs/index.html` + vanilla JS (no framework, no build step):

- Fetches `../data/listings.json` and `../data/meta.json` client-side.
- **Sortable/filterable table:** filter by province, seller, property type, price range;
  free-text search box.
- **Location cell is a link** → opens `maps_url` in a new tab.
- **Header:** total count · last-updated in Manila time · per-source counts ·
  a standing **coverage note** ("Guimaras / Aklan / Antique inventory is naturally thin —
  often single digits; Iloilo carries most listings").
- **Staleness banner:** if `meta.json` shows a source errored on the last run, a small
  non-blocking banner flags which source is stale.

## Automation (`.github/workflows/refresh.yml`)

- Triggers: `cron: '0 */6 * * *'` **and** `workflow_dispatch` (manual button).
- Steps: checkout → setup Python → `playwright install --with-deps chromium` →
  run scrapers → `build.py` → commit `data/*` **only if changed** → Pages redeploys.
- **Resilience:**
  - Each scraper is wrapped in try/except at the pipeline level. One source failing
    logs its error into `meta.json` and **never aborts** the run — the others publish.
  - An **empty scrape does not wipe** existing data: if a source returns zero rows, the
    pipeline retains that source's prior rows and marks it stale in `meta.json`. Good data
    is never overwritten with nothing.
  - Commit only on real change (avoids empty 6-hourly commits).

## Error handling & honesty

- Per-source failures are captured, surfaced on the page, and last-good data retained.
- `meta.json` is the audit trail: what ran, what each source found, what broke.
- The page always states coverage limits so a thin list is never mistaken for a failure.

## Repo layout

```
panay-foreclosure-tracker/
├── scrapers/
│   ├── __init__.py
│   ├── foreclosurephilippines.py
│   ├── pagibig_opa.py
│   └── metrobank.py
├── build.py
├── requirements.txt
├── data/
│   ├── listings.json
│   ├── listings.csv
│   └── meta.json
├── docs/
│   ├── index.html          # GitHub Pages entry
│   └── superpowers/specs/  # this spec
├── .github/workflows/refresh.yml
└── README.md
```

## Testing

- Each scraper: a unit test with a saved HTML/JSON fixture (recorded from a live page)
  asserting it parses into the unified schema — so scrapers can be verified without
  hitting the network, and a source layout change is caught by a failing test.
- `build.py`: unit tests for normalization, the Google Maps URL builder, dedup
  fingerprinting/merge, and the "empty scrape retains prior data" rule.
- A smoke test that `index.html` renders a fixture `listings.json` without JS errors.

## Deferred (future versions)

- UnionBank + Lamudi scrapers (broader Iloilo coverage; Lamudi names the selling bank).
- BPI Buena Mano PDF catalog parsing.
- PNB Property Finder (needs robust JS handling).
- Email/Slack notification when a *new* listing appears in Guimaras.
